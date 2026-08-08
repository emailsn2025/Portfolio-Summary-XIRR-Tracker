"""
kuvera_import.py
-----------------
Parses a Kuvera transaction/statement export (.xlsx).

Kuvera has no public API for retail users (see README), so this is a file
importer. Their export is unlike a normal spreadsheet: it's not a table of
rows and columns at all - it's every transaction's fields (date, scheme,
buy/sell, units, price, amount) flattened one value per row down a single
column, and Kuvera sometimes inserts a blank filler cell between fields,
so a fixed "every 6th row is a new record" assumption breaks partway
through. This parser instead classifies each value by *type* (a date
starts a new record; the two text values in between are the scheme name
and buy/sell; the three numeric values are units, price, and amount),
which is robust to that inconsistency - verified against a real ~15-year,
450+ transaction export.

Kuvera also doesn't give you an ISIN at all, and its scheme names don't
match CAMS/CDSL's naming for the same fund (e.g. Kuvera's "Bandhan Small
Cap Growth Direct Plan" vs. the CAS's "D340 - Bandhan Small Cap Fund-
Direct Plan-Growth"). So this module matches scheme names to ISINs from
your already-parsed CAS by fund house + category words, gated so a fund
never matches a different fund house's scheme just because they share a
generic word like "large cap". A scheme with no confident match is not an
error - it usually means you've fully redeemed it and it's no longer in
your current CAS, in which case its buy/sell transactions alone already
form a complete, closed cash-flow cycle and don't need a current-value
cash flow for XIRR to be correct.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from io import BytesIO

import pandas as pd


# --------------------------------------------------------------------------
# Parsing the flattened single-column export
# --------------------------------------------------------------------------

def _read_bytes(file) -> bytes:
    if hasattr(file, "getvalue"):
        return file.getvalue()
    if hasattr(file, "read"):
        file.seek(0)
        return file.read()
    raise TypeError(f"Don't know how to read {file!r}")


def load_statement(file) -> pd.DataFrame:
    """
    Returns one row per transaction: Date, Scheme, Type, Units, Price, Amount.
    Raises ValueError if the file doesn't look like a Kuvera export.
    """
    fbytes = _read_bytes(file)
    raw = pd.read_excel(BytesIO(fbytes), sheet_name=0, header=None)
    if raw.shape[1] == 0:
        raise ValueError("Empty file.")

    vals = raw[0].tolist()
    n = len(vals)
    date_idxs = [i for i in range(n) if isinstance(vals[i], datetime)]
    if not date_idxs:
        raise ValueError(
            "Couldn't find any transaction dates in this file - make sure it's a Kuvera "
            "transaction/statement export."
        )
    date_idxs.append(n)  # sentinel

    records = []
    for k in range(len(date_idxs) - 1):
        start, end = date_idxs[k], date_idxs[k + 1]
        dt = vals[start]
        chunk = vals[start + 1 : end]
        strs = [v for v in chunk if isinstance(v, str)]
        nums = [
            v for v in chunk
            if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v))
        ]
        if len(strs) != 2 or len(nums) != 3:
            continue  # skip anything that doesn't fit the expected shape (e.g. a stray note row)
        scheme, ttype = strs
        units, price, amount = nums
        records.append(
            {"Date": dt.date(), "Scheme": scheme, "Type": ttype.strip().lower(),
             "Units": units, "Price": price, "Amount": amount}
        )

    if not records:
        raise ValueError(
            "Found dates in this file but couldn't parse any complete transactions from them. "
            "Kuvera may have changed their export format - check the raw file."
        )
    return pd.DataFrame(records)


# --------------------------------------------------------------------------
# Scheme-name -> ISIN matching against the parsed CAS
# --------------------------------------------------------------------------

_AMC_KEYWORDS = [
    "aditya birla", "icici prudential", "nippon india", "franklin india", "canara robeco",
    "motilal oswal", "parag parikh", "mirae asset", "hdfc", "sbi", "kotak", "axis",
    "bandhan", "quant", "uti", "dsp", "tata", "invesco", "edelweiss", "idfc", "l&t",
]
_NOISE = {
    "fund", "plan", "direct", "growth", "the", "option", "regular", "scheme",
    "erstwhile", "standard", "dir", "gr",
}


def _stem(tok: str) -> str:
    return tok[:-1] if len(tok) > 4 and tok.endswith("s") else tok


def _amc_of(name: str):
    low = name.lower()
    for kw in _AMC_KEYWORDS:
        if kw in low:
            return kw
    return None


def _normalize(name: str, strip_amc=None) -> set:
    name = re.sub(r"^[A-Z0-9]+\s*-\s*", "", name)
    name = re.sub(r"\(.*?\)", " ", name)
    if strip_amc:
        name = re.sub(re.escape(strip_amc), " ", name, flags=re.IGNORECASE)
    name = re.sub(r"[^a-zA-Z0-9& ]", " ", name)
    return {_stem(t.lower()) for t in name.split() if t.lower() not in _NOISE}


def _jaccard(a: set, b: set):
    if not a or not b:
        return 0.0, 0
    inter = a & b
    return len(inter) / len(a | b), len(inter)


def match_scheme_to_isin(scheme_name: str, cas_mf_holdings: pd.DataFrame, threshold: float = 0.45, min_intersect: int = 1):
    """
    Returns (ISIN, CAS scheme name, score) or None. Gated to the same fund
    house first so category words (e.g. "large cap") can't cause a
    cross-AMC false match.
    """
    if cas_mf_holdings is None or cas_mf_holdings.empty:
        return None
    amc = _amc_of(scheme_name)
    kn = _normalize(scheme_name, strip_amc=amc)

    candidates = list(cas_mf_holdings[["ISIN", "Scheme"]].drop_duplicates().itertuples())
    if amc:
        candidates = [c for c in candidates if _amc_of(c.Scheme) == amc]
    if not candidates:
        return None

    scored = []
    for c in candidates:
        cn = _normalize(c.Scheme, strip_amc=amc)
        score, inter_n = _jaccard(kn, cn)
        scored.append((score, inter_n, c.ISIN, c.Scheme))
    scored.sort(reverse=True)
    best = scored[0]
    if best[0] >= threshold and best[1] >= min_intersect:
        return best[2], best[3], best[0]
    return None


def build_transactions(kuvera_records: pd.DataFrame, cas_mf_holdings: pd.DataFrame):
    """
    Converts load_statement()'s output into the app's standard transaction
    schema, matching each scheme to a CAS ISIN. Returns (transactions_df,
    match_summary) where match_summary is a DataFrame of one row per unique
    scheme showing what it matched to (or "no match"), so the app can show
    the user what happened before they rely on the numbers.
    """
    rows = []
    summary = {}
    match_cache = {}

    for _, r in kuvera_records.iterrows():
        scheme = r["Scheme"]
        if scheme not in match_cache:
            match_cache[scheme] = match_scheme_to_isin(scheme, cas_mf_holdings)
        match = match_cache[scheme]

        identifier = match[0] if match else f"UNMATCHED::{scheme}"
        summary.setdefault(
            scheme,
            {
                "Kuvera Scheme": scheme,
                "Matched CAS Scheme": match[1] if match else "(no current CAS holding - likely fully redeemed)",
                "ISIN": match[0] if match else "-",
                "Match Confidence": f"{match[2]*100:.0f}%" if match else "-",
            },
        )

        signed_amount = -r["Amount"] if r["Type"] == "buy" else r["Amount"]
        rows.append(
            {
                "Date": r["Date"],
                "AssetClass": "Mutual Fund Folios",
                "Identifier": identifier,
                "Description": f"{scheme} - {r['Type']}",
                "Amount": round(float(signed_amount), 2),
            }
        )

    txns = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    match_summary = pd.DataFrame(summary.values())
    return txns, match_summary
