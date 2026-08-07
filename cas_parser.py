"""
cas_parser.py
-------------
Parses a CDSL Consolidated Account Statement (CAS) PDF and extracts:
  - Portfolio-level asset class summary (Equity / Mutual Fund Folios /
    Mutual Funds Held in Demat Form / Others)
  - Individual equity & bond holdings (from CDSL + NSDL demat tables)
  - Individual mutual fund folio holdings (with invested amount, valuation,
    and unrealised P&L, which CAMS/KFIN pre-compute for us)
  - The 12-month portfolio valuation trend table (for context, not XIRR)

CDSL CAS PDFs interleave English and Hindi text at the character level,
which makes plain page.extract_text() unusable. This parser instead relies
on pdfplumber's table extraction, which comes through clean, and classifies
each row using India's standard ISIN prefix convention:
    INE -> Equity share
    INF -> Mutual fund unit
    IN0 -> Government security / Sovereign Gold Bond
This makes the parser reusable for any CDSL CAS, not just one specific file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import pdfplumber

ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}")

ISIN_PREFIX_TO_CLASS = {
    "INE": "Equity",
    "INF": "Mutual Funds Held in Demat Form",
    "IN0": "Others (Govt Securities/SGB)",
}


def _clean(cell: Optional[str]) -> str:
    return (cell or "").replace("\n", " ").strip()


def _to_float(cell: Optional[str]) -> float:
    s = _clean(cell).replace(",", "").replace("`", "").replace("₹", "")
    if s in ("", "--", "-", "None"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


@dataclass
class CASData:
    holder_name: str = ""
    total_value: float = 0.0
    asset_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    equity_holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    mf_in_demat_holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    other_holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    mf_folio_holdings: pd.DataFrame = field(default_factory=pd.DataFrame)
    valuation_trend: pd.DataFrame = field(default_factory=pd.DataFrame)


def parse_cas(pdf_path_or_bytes) -> CASData:
    demat_rows: list[list[str]] = []
    mf_rows: list[list[str]] = []
    asset_summary_rows: list[list[str]] = []
    trend_rows: list[list[str]] = []

    with pdfplumber.open(pdf_path_or_bytes) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                header_idx, header_type = _identify_header(table)
                if header_idx is None:
                    continue
                data_rows = table[header_idx + 1 :]

                if header_type == "demat":
                    for row in data_rows:
                        if row and row[0] and ISIN_RE.match(_clean(row[0])):
                            demat_rows.append(row)
                elif header_type == "mf":
                    for row in data_rows:
                        if len(row) > 1 and row[1] and ISIN_RE.match(_clean(row[1])):
                            mf_rows.append(row)
                elif header_type == "asset_summary" and not asset_summary_rows:
                    asset_summary_rows = table[header_idx:]
                elif header_type == "trend" and not trend_rows:
                    trend_rows = table[header_idx:]

    return _build_cas_data(demat_rows, mf_rows, asset_summary_rows, trend_rows)


def _identify_header(table: list[list[str]]):
    for idx, row in enumerate(table):
        row_text = " ".join(_clean(c) for c in row if c)
        # Skip "STATEMENT OF TRANSACTIONS" tables (period activity, not
        # current holdings) - they also contain "ISIN"/"Security" so must
        # be excluded explicitly before the holdings check below.
        is_transaction_table = any(
            marker in row_text for marker in ("Transaction", "Op. Bal", "Cl. Bal", "Credit", "Debit")
        )
        if "Scheme Name" in row_text and "ISIN" in row_text:
            return idx, "mf"
        if "ISIN" in row_text and ("Security" in row_text or "Current" in row_text) and not is_transaction_table:
            return idx, "demat"
        if "Asset Class" in row_text:
            return idx, "asset_summary"
        if "Month-Year" in row_text or ("Portfolio Valuation" in row_text and "Changes" in row_text):
            return idx, "trend"
    return None, None


def _classify_isin(isin: str) -> str:
    prefix = isin[:3]
    return ISIN_PREFIX_TO_CLASS.get(prefix, f"Other ({prefix})")


def _build_cas_data(demat_rows, mf_rows, asset_summary_rows, trend_rows) -> CASData:
    data = CASData()

    # ---- Demat holdings (equity / govt securities / MF-in-demat) ----
    equity, mf_in_demat, others = [], [], []
    for row in demat_rows:
        isin = _clean(row[0])
        security = _clean(row[1]) if len(row) > 1 else ""
        value = _to_float(row[-1])
        cls = _classify_isin(isin)
        rec = {"ISIN": isin, "Security": security, "Value (₹)": value}
        if cls == "Equity":
            equity.append(rec)
        elif cls == "Mutual Funds Held in Demat Form":
            mf_in_demat.append(rec)
        else:
            rec["Category"] = cls
            others.append(rec)

    data.equity_holdings = (
        pd.DataFrame(equity).sort_values("Value (₹)", ascending=False).reset_index(drop=True)
        if equity else pd.DataFrame(columns=["ISIN", "Security", "Value (₹)"])
    )
    data.mf_in_demat_holdings = (
        pd.DataFrame(mf_in_demat).sort_values("Value (₹)", ascending=False).reset_index(drop=True)
        if mf_in_demat else pd.DataFrame(columns=["ISIN", "Security", "Value (₹)"])
    )
    data.other_holdings = (
        pd.DataFrame(others).sort_values("Value (₹)", ascending=False).reset_index(drop=True)
        if others else pd.DataFrame(columns=["ISIN", "Security", "Value (₹)", "Category"])
    )

    # ---- Mutual fund folio holdings ----
    mf_records = []
    for row in mf_rows:
        scheme = _clean(row[0])
        isin = _clean(row[1])
        folio = _clean(row[2]) if len(row) > 2 else ""
        invested = _to_float(row[5]) if len(row) > 5 else 0.0
        valuation = _to_float(row[6]) if len(row) > 6 else 0.0
        pl_abs = _to_float(row[7]) if len(row) > 7 else (valuation - invested)
        pl_pct = _to_float(row[8]) if len(row) > 8 else (
            (pl_abs / invested * 100) if invested else 0.0
        )
        mf_records.append(
            {
                "Scheme": scheme,
                "ISIN": isin,
                "Folio No.": folio,
                "Invested (₹)": invested,
                "Valuation (₹)": valuation,
                "Unrealised P/L (₹)": pl_abs,
                "Unrealised P/L (%)": pl_pct,
            }
        )
    data.mf_folio_holdings = (
        pd.DataFrame(mf_records).sort_values("Valuation (₹)", ascending=False).reset_index(drop=True)
        if mf_records else pd.DataFrame(
            columns=["Scheme", "ISIN", "Folio No.", "Invested (₹)", "Valuation (₹)",
                     "Unrealised P/L (₹)", "Unrealised P/L (%)"]
        )
    )

    # ---- Asset class summary ----
    summary_records = []
    for row in asset_summary_rows[1:] if asset_summary_rows else []:
        label = _clean(row[0])
        if not label or label.lower() == "total":
            if label.lower() == "total":
                data.total_value = _to_float(row[1])
            continue
        summary_records.append({"Asset Class": label, "Value (₹)": _to_float(row[1]),
                                 "% of Portfolio": _to_float(row[2]) if len(row) > 2 else None})
    data.asset_summary = pd.DataFrame(summary_records)
    if not data.total_value and not data.asset_summary.empty:
        data.total_value = data.asset_summary["Value (₹)"].sum()

    # ---- 12-month valuation trend (context only, NOT used for XIRR) ----
    trend_records = []
    for row in trend_rows[1:] if trend_rows else []:
        month = _clean(row[0])
        if not month:
            continue
        trend_records.append(
            {
                "Month": month,
                "Portfolio Value (₹)": _to_float(row[1]),
                "Change (₹)": _to_float(row[2]) if len(row) > 2 else None,
                "Change (%)": _to_float(row[3]) if len(row) > 3 else None,
            }
        )
    data.valuation_trend = pd.DataFrame(trend_records)

    return data
