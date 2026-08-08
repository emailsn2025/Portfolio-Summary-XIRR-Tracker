"""
Portfolio Summary & XIRR Tracker
--------------------------------
Upload a CDSL Consolidated Account Statement (CAS) PDF and get a clean,
tabular summary of your holdings by asset class - plus, if you supply your
transaction history, real XIRR per asset class.

Run locally:    streamlit run app.py
Deploy:         see README.md for GitHub + Streamlit Community Cloud steps
"""

from datetime import date, datetime
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

from cas_parser import parse_cas, CASData
from xirr import CashFlow, xirr
from zerodha_tradebook import parse_tradebook
import kuvera_import
import zerodha_connector

st.set_page_config(page_title="Portfolio Summary & XIRR Tracker", page_icon="📊", layout="wide")

TXN_SCHEMA = ["Date", "AssetClass", "Identifier", "Description", "Amount"]
if "txn_sources" not in st.session_state:
    st.session_state.txn_sources = {}  # source_key -> DataFrame in TXN_SCHEMA
if "zerodha_session" not in st.session_state:
    st.session_state.zerodha_session = None
if "zerodha_holdings" not in st.session_state:
    st.session_state.zerodha_holdings = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def fmt_inr(x: float) -> str:
    """Format a number in Indian comma style, e.g. 3,12,57,113.61"""
    if x is None or pd.isna(x):
        return "-"
    neg = x < 0
    x = abs(x)
    s = f"{x:,.2f}"
    # python's comma grouping is 3-digit western style; convert to Indian style
    int_part, dec_part = s.split(".")
    int_part = int_part.replace(",", "")
    if len(int_part) > 3:
        last3 = int_part[-3:]
        rest = int_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        int_part = ",".join(groups + [last3])
    out = f"₹{int_part}.{dec_part}"
    return f"-{out}" if neg else out


@st.cache_data(show_spinner=False)
def load_cas(file_bytes: bytes) -> CASData:
    return parse_cas(BytesIO(file_bytes))


def to_excel(data: CASData) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        data.asset_summary.to_excel(writer, sheet_name="Asset Class Summary", index=False)
        data.mf_folio_holdings.to_excel(writer, sheet_name="Mutual Funds", index=False)
        data.equity_holdings.to_excel(writer, sheet_name="Equity", index=False)
        data.mf_in_demat_holdings.to_excel(writer, sheet_name="MF Held in Demat", index=False)
        data.other_holdings.to_excel(writer, sheet_name="Others (Govt Sec)", index=False)
        data.valuation_trend.to_excel(writer, sheet_name="12M Trend", index=False)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Sidebar - upload
# --------------------------------------------------------------------------

st.sidebar.title("📊 Portfolio Tracker")
st.sidebar.caption("Nothing you upload leaves this session - the PDF is parsed in memory only.")
uploaded = st.sidebar.file_uploader("Upload your CDSL CAS PDF", type=["pdf"])

st.sidebar.markdown("---")
st.sidebar.caption(
    "Get your CAS from **cdslindia.com** → \"Register for easi/easiest\" → e-CAS, "
    "or from the link CDSL emails you monthly."
)

if not uploaded:
    st.title("Portfolio Summary & XIRR Tracker")
    st.write(
        "Upload your CDSL Consolidated Account Statement (CAS) PDF in the sidebar to get "
        "a tabular breakdown of your holdings by asset class."
    )
    st.info(
        "**A quick note on XIRR:** a monthly CAS shows your *current* holdings and, for "
        "mutual funds, the cumulative amount invested - but not the dates of each individual "
        "purchase or redemption. True XIRR needs those dates. This app will show you the "
        "absolute return already computed for each mutual fund, and lets you optionally "
        "upload a transaction history (see the XIRR tab) to get real, dated XIRR by "
        "holding and by asset class."
    )
    st.stop()

with st.spinner("Parsing your CAS statement..."):
    data = load_cas(uploaded.getvalue())

if data.total_value == 0:
    st.error(
        "Couldn't find holdings in this PDF. Make sure it's a CDSL CAS "
        "(Consolidated Account Statement) - the file CDSL emails monthly."
    )
    st.stop()


# --------------------------------------------------------------------------
# Header metrics
# --------------------------------------------------------------------------

st.title("Portfolio Summary & XIRR Tracker")
col1, col2, col3 = st.columns(3)
col1.metric("Total Portfolio Value", fmt_inr(data.total_value))
if not data.valuation_trend.empty and len(data.valuation_trend) >= 2:
    prev = data.valuation_trend.iloc[-2]["Portfolio Value (₹)"]
    curr = data.valuation_trend.iloc[-1]["Portfolio Value (₹)"]
    col2.metric("Change vs Last Month", fmt_inr(curr - prev), f"{(curr/prev - 1)*100:.2f}%")
mf_total_invested = data.mf_folio_holdings["Invested (₹)"].sum() if not data.mf_folio_holdings.empty else 0
mf_total_val = data.mf_folio_holdings["Valuation (₹)"].sum() if not data.mf_folio_holdings.empty else 0
if mf_total_invested:
    col3.metric(
        "Mutual Funds Return (absolute)",
        f"{(mf_total_val/mf_total_invested - 1)*100:.2f}%",
        help="Weighted absolute return across mutual fund folios (invested vs current valuation). Not annualised.",
    )

tab_summary, tab_holdings, tab_connect, tab_xirr, tab_trend = st.tabs(
    ["📋 Asset Class Summary", "🔍 Holdings Detail", "🔗 Connect & Import", "📈 XIRR", "🕒 12-Month Trend"]
)


# --------------------------------------------------------------------------
# Tab 1: Asset class summary
# --------------------------------------------------------------------------

with tab_summary:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Holdings by asset class")
        display_df = data.asset_summary.copy()
        display_df["Value (₹)"] = display_df["Value (₹)"].apply(fmt_inr)
        display_df["% of Portfolio"] = display_df["% of Portfolio"].apply(lambda x: f"{x:.2f}%")
        st.dataframe(display_df, hide_index=True, width="stretch")

        excel_bytes = to_excel(data)
        st.download_button(
            "⬇️ Download full breakdown (Excel)",
            data=excel_bytes,
            file_name=f"portfolio_summary_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with right:
        fig = px.pie(
            data.asset_summary, values="Value (₹)", names="Asset Class",
            hole=0.45,
        )
        fig.update_traces(textinfo="percent+label")
        fig.update_layout(showlegend=False, margin=dict(t=10, b=10, l=10, r=10), height=350)
        st.plotly_chart(fig, width="stretch")


# --------------------------------------------------------------------------
# Tab 2: Holdings detail
# --------------------------------------------------------------------------

with tab_holdings:
    st.subheader("Mutual Fund Folios")
    if not data.mf_folio_holdings.empty:
        mf_display = data.mf_folio_holdings.copy()
        for c in ["Invested (₹)", "Valuation (₹)", "Unrealised P/L (₹)"]:
            mf_display[c] = mf_display[c].apply(fmt_inr)
        mf_display["Unrealised P/L (%)"] = mf_display["Unrealised P/L (%)"].apply(lambda x: f"{x:.2f}%")
        st.dataframe(mf_display, hide_index=True, width="stretch")
    else:
        st.caption("No mutual fund folios found.")

    st.subheader("Equity Holdings")
    zh = st.session_state.zerodha_holdings
    if zh is not None and not zh.empty:
        eq_display = zh.copy()
        for c in ["Avg. Buy Price (₹)", "Invested (₹)", "Last Price (₹)", "Current Value (₹)", "Unrealised P/L (₹)"]:
            eq_display[c] = eq_display[c].apply(fmt_inr)
        eq_display["Unrealised P/L (%)"] = eq_display["Unrealised P/L (%)"].apply(lambda x: f"{x:.2f}%")
        st.dataframe(eq_display, hide_index=True, width="stretch")
        st.caption(
            "Cost basis and return % from your connected Zerodha account (avg. buy price via "
            "Kite Connect) - the CAS alone doesn't carry this. Values may differ slightly from "
            "the CAS if it's not from today, since prices move."
        )
    elif not data.equity_holdings.empty:
        eq_display = data.equity_holdings.copy()
        eq_display["Value (₹)"] = eq_display["Value (₹)"].apply(fmt_inr)
        st.dataframe(eq_display, hide_index=True, width="stretch")
        st.caption(
            "Note: the CAS only reports current market value for equities, not your original "
            "purchase cost - so no return % is shown here. Connect Zerodha in the "
            "**Connect & Import** tab to get real cost basis, or upload a tradebook there for XIRR."
        )
    else:
        st.caption("No direct equity holdings found.")

    st.subheader("Mutual Funds Held in Demat Form")
    if not data.mf_in_demat_holdings.empty:
        d_display = data.mf_in_demat_holdings.copy()
        d_display["Value (₹)"] = d_display["Value (₹)"].apply(fmt_inr)
        st.dataframe(d_display, hide_index=True, width="stretch")
    else:
        st.caption("None found.")

    st.subheader("Others (Government Securities / Sovereign Gold Bonds)")
    if not data.other_holdings.empty:
        o_display = data.other_holdings.copy()
        o_display["Value (₹)"] = o_display["Value (₹)"].apply(fmt_inr)
        st.dataframe(o_display, hide_index=True, width="stretch")
    else:
        st.caption("None found.")


# --------------------------------------------------------------------------
# Tab 3: Connect & Import - the sources that feed real XIRR
# --------------------------------------------------------------------------

with tab_connect:
    st.write(
        "Real XIRR needs dated cash flows - your CAS alone doesn't have them (see the XIRR tab "
        "for why). Bring them in from any combination of the sources below; everything you load "
        "here gets pooled together for the XIRR tab."
    )

    src_zerodha_live, src_zerodha_csv, src_kuvera, src_manual = st.tabs(
        ["Zerodha (live)", "Zerodha (Tradebook CSV)", "Kuvera (statement)", "Manual CSV"]
    )

    # ---- Zerodha live connect (Kite Connect Personal API - free) ----
    with src_zerodha_live:
        st.markdown(
            "Pulls your **current holdings with average buy price** via Zerodha's official, "
            "free Kite Connect Personal API. This gives real cost basis for equities (which "
            "shows up in the Holdings Detail tab) - but *not* purchase dates, so it feeds cost "
            "basis, not XIRR. For dated history, use the Tradebook CSV tab instead."
        )
        st.caption(
            "Get a free API key + secret at [developers.kite.trade](https://developers.kite.trade) "
            "→ My Apps → Create New App → type **Personal**. Nothing you enter here is stored "
            "outside this browser session."
        )

        api_key = st.text_input("Kite API key", key="kite_api_key")
        api_secret = st.text_input("Kite API secret", type="password", key="kite_api_secret")

        # Auto-capture request_token if Zerodha redirected back to this app's own URL
        qp = st.query_params
        auto_token = qp.get("request_token")

        if api_key:
            login_url = zerodha_connector.get_login_url(api_key)
            st.link_button("1. Log in to Zerodha", login_url)

        request_token = st.text_input(
            "2. Paste the request_token from the redirect URL after logging in "
            "(or it's auto-filled if your app's redirect URL points back here)",
            value=auto_token or "",
            key="kite_request_token",
        )

        if st.button("3. Fetch my holdings", disabled=not (api_key and api_secret and request_token)):
            try:
                session = zerodha_connector.generate_session(api_key, api_secret, request_token)
                st.session_state.zerodha_session = session
                holdings_df = zerodha_connector.fetch_holdings(session)
                st.session_state.zerodha_holdings = holdings_df
                st.success(f"Pulled {len(holdings_df)} holdings. Check the Holdings Detail tab.")
            except Exception as e:
                st.error(f"Couldn't connect: {e}")

        if st.session_state.zerodha_holdings is not None:
            st.caption(f"✅ {len(st.session_state.zerodha_holdings)} holdings loaded from Zerodha this session.")

    # ---- Zerodha Tradebook CSV/XLSX ----
    with src_zerodha_csv:
        st.markdown(
            "Console → Reports → **Tradebook** → pick a date range → Download. Console caps "
            "each export at about a year, so upload as many files as you need to cover your "
            "full holding period - they'll be combined."
        )
        zt_files = st.file_uploader(
            "Upload Tradebook file(s)", type=["csv", "xlsx"], accept_multiple_files=True, key="zt_upload"
        )
        if zt_files and st.button("Add to XIRR data", key="zt_add"):
            try:
                parsed = parse_tradebook(zt_files)
                st.session_state.txn_sources["zerodha_tradebook"] = parsed
                st.success(f"Added {len(parsed)} trades from {len(zt_files)} file(s).")
            except Exception as e:
                st.error(str(e))
        if "zerodha_tradebook" in st.session_state.txn_sources:
            st.caption(f"✅ {len(st.session_state.txn_sources['zerodha_tradebook'])} trades loaded.")
            st.dataframe(st.session_state.txn_sources["zerodha_tradebook"], hide_index=True, width="stretch")

    # ---- Kuvera statement ----
    with src_kuvera:
        st.markdown(
            "Kuvera app → **Reports** → transaction statement (.xlsx). Kuvera has no public "
            "API, so this is a file import. Kuvera doesn't include an ISIN in its export, so "
            "each scheme name is matched to the ISIN in your CAS by fund house + category - "
            "check the match table below before relying on the results. A scheme showing "
            "**no match** usually just means you've fully redeemed it, so it no longer "
            "appears in your current CAS - that's fine, its own buy/sell history is a "
            "complete cash-flow cycle on its own."
        )
        kv_file = st.file_uploader("Upload Kuvera statement", type=["xlsx"], key="kv_upload")
        if kv_file is not None:
            try:
                kv_records = kuvera_import.load_statement(kv_file)
                txns, match_summary = kuvera_import.build_transactions(kv_records, data.mf_folio_holdings)
            except Exception as e:
                st.error(f"Couldn't read that file: {e}")
                txns, match_summary = None, None

            if txns is not None:
                st.caption(f"Parsed {len(txns)} transactions across {len(match_summary)} schemes. Scheme matching:")
                st.dataframe(match_summary, hide_index=True, width="stretch")
                if st.button("Add to XIRR data", key="kv_add"):
                    st.session_state.txn_sources["kuvera_statement"] = txns
                    st.success(f"Added {len(txns)} transactions.")
        if "kuvera_statement" in st.session_state.txn_sources:
            st.caption(f"✅ {len(st.session_state.txn_sources['kuvera_statement'])} transactions loaded.")

    # ---- Manual CSV template ----
    with src_manual:
        st.markdown("For anything else - PMS accounts, funds outside Zerodha/Kuvera, manual entry.")
        template = pd.DataFrame(
            {
                "Date": ["2023-04-01", "2023-07-01", "2026-06-30"],
                "AssetClass": ["Mutual Fund Folios", "Mutual Fund Folios", "Mutual Fund Folios"],
                "Identifier": ["INF209K01YN0", "INF209K01YN0", "INF209K01YN0"],
                "Description": ["Aditya Birla Sun Life Banking & PSU Debt Fund", "same fund - SIP #2", "current value (auto-filled)"],
                "Amount": [-50000, -50000, 0],
            }
        )
        st.caption(
            "Amount convention: **negative** = invested (purchase/SIP), **positive** = received "
            "(redemption/dividend). Leave the final 'current value' row's Amount as 0 - the "
            "XIRR tab fills it in from your CAS."
        )
        st.dataframe(template, hide_index=True, width="stretch")
        st.download_button(
            "⬇️ Download template (CSV)",
            data=template.to_csv(index=False).encode("utf-8"),
            file_name="transactions_template.csv",
            mime="text/csv",
        )
        manual_file = st.file_uploader("Upload completed CSV", type=["csv"], key="manual_upload")
        if manual_file is not None and st.button("Add to XIRR data", key="manual_add"):
            try:
                manual_df = pd.read_csv(manual_file)
                manual_df["Date"] = pd.to_datetime(manual_df["Date"]).dt.date
                missing = set(TXN_SCHEMA) - set(manual_df.columns)
                if missing:
                    st.error(f"Missing column(s): {', '.join(missing)}")
                else:
                    st.session_state.txn_sources["manual"] = manual_df[TXN_SCHEMA]
                    st.success(f"Added {len(manual_df)} rows.")
            except Exception as e:
                st.error(str(e))
        if "manual" in st.session_state.txn_sources:
            st.caption(f"✅ {len(st.session_state.txn_sources['manual'])} rows loaded.")
            st.dataframe(st.session_state.txn_sources["manual"], hide_index=True, width="stretch")
            if st.button("🗑️ Clear manual CSV data", key="manual_clear"):
                del st.session_state.txn_sources["manual"]
                st.rerun()

    if st.session_state.txn_sources:
        st.markdown("---")
        total_loaded = sum(len(df) for df in st.session_state.txn_sources.values())
        st.caption(f"**{total_loaded} transactions loaded across {len(st.session_state.txn_sources)} source(s).** See the XIRR tab for results.")
        if st.button("🗑️ Clear all imported transaction data"):
            st.session_state.txn_sources = {}
            st.session_state.zerodha_holdings = None
            st.rerun()


# --------------------------------------------------------------------------
# Tab 4: XIRR - computed from whatever's loaded in Connect & Import
# --------------------------------------------------------------------------

with tab_xirr:
    st.subheader("Real XIRR needs transaction-level cash flows")
    st.write(
        "A single CAS statement gives us your *current* holdings and, for mutual funds, the "
        "cumulative amount invested - enough to compute an **absolute return %**, which you can "
        "see in the Holdings Detail tab. It does **not** give us the date and amount of every "
        "individual purchase, SIP instalment, switch, or redemption - which is what XIRR "
        "(a money-weighted, annualised return) actually needs. That's what the "
        "**Connect & Import** tab is for."
    )

    if not st.session_state.txn_sources:
        st.info("Nothing loaded yet - head to the **Connect & Import** tab to bring in transaction history.")
        st.stop()

    txns = pd.concat(st.session_state.txn_sources.values(), ignore_index=True)
    st.caption(
        f"Using {len(txns)} transactions from: {', '.join(st.session_state.txn_sources.keys())}."
    )

    as_of = date.today()

    # Build a lookup of current valuation per Identifier (ISIN) from the parsed CAS.
    # The same ISIN can legitimately appear in more than one CAS table - e.g. a
    # mutual fund held partly as a regular folio and partly in dematerialised
    # form, or one scheme split across two SIP folios - so this must ADD
    # matching ISINs together, never overwrite one bucket's value with another's.
    from collections import defaultdict
    value_lookup = defaultdict(float)
    for df, col in [
        (data.mf_folio_holdings, "Valuation (₹)"),
        (data.equity_holdings, "Value (₹)"),
        (data.mf_in_demat_holdings, "Value (₹)"),
        (data.other_holdings, "Value (₹)"),
    ]:
        if not df.empty:
            for isin, val in df.groupby("ISIN")[col].sum().items():
                value_lookup[isin] += val
    # Live Zerodha holdings (if connected) replace the CAS equity value with a
    # more current one - this one's a deliberate override, not an accumulation,
    # since it's a fresher snapshot of the same position.
    if st.session_state.zerodha_holdings is not None and not st.session_state.zerodha_holdings.empty:
        for isin, val in st.session_state.zerodha_holdings.set_index("ISIN")["Current Value (₹)"].items():
            value_lookup[isin] = val
    value_lookup = dict(value_lookup)

    st.subheader("XIRR by holding")
    rows = []
    for ident, grp in txns.groupby("Identifier"):
        flows = [CashFlow(r["Date"], r["Amount"]) for _, r in grp.iterrows() if r["Amount"] != 0]
        current_val = value_lookup.get(str(ident).strip())
        if current_val is not None:
            flows.append(CashFlow(as_of, current_val))
        result = xirr(flows) if len(flows) >= 2 else None
        desc = grp["Description"].iloc[0] if "Description" in grp.columns else ""
        asset_class = grp["AssetClass"].iloc[0] if "AssetClass" in grp.columns else ""
        rows.append(
            {
                "Identifier": ident,
                "Description": desc,
                "Asset Class": asset_class,
                "Current Value Used (₹)": fmt_inr(current_val) if current_val is not None else "not found",
                "XIRR": f"{result*100:.2f}%" if result is not None else "couldn't solve",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("XIRR by asset class")
    st.caption(
        "Pools every cash flow within an asset class together with its total current "
        "value - the correct way to compute a blended XIRR for a group of holdings."
    )
    class_rows = []
    for asset_class, grp in txns.groupby("AssetClass"):
        flows = [CashFlow(r["Date"], r["Amount"]) for _, r in grp.iterrows() if r["Amount"] != 0]
        idents = grp["Identifier"].unique()
        total_current = sum(value_lookup.get(str(i).strip(), 0) for i in idents)
        if total_current:
            flows.append(CashFlow(as_of, total_current))
        result = xirr(flows) if len(flows) >= 2 else None
        class_rows.append(
            {
                "Asset Class": asset_class,
                "Current Value (₹)": fmt_inr(total_current),
                "XIRR": f"{result*100:.2f}%" if result is not None else "couldn't solve",
            }
        )
    st.dataframe(pd.DataFrame(class_rows), hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# Tab 4: 12-month trend (context only - explicitly NOT presented as XIRR)
# --------------------------------------------------------------------------

with tab_trend:
    st.subheader("Portfolio value - last 12 months")
    st.caption(
        "From the CAS's own month-end valuation history. This mixes market movement with "
        "any money you added or withdrew, so it's shown for context only - it is not a "
        "return figure."
    )
    if not data.valuation_trend.empty:
        fig2 = px.line(data.valuation_trend, x="Month", y="Portfolio Value (₹)", markers=True)
        fig2.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=350)
        st.plotly_chart(fig2, width="stretch")
        trend_display = data.valuation_trend.copy()
        trend_display["Portfolio Value (₹)"] = trend_display["Portfolio Value (₹)"].apply(fmt_inr)
        trend_display["Change (₹)"] = trend_display["Change (₹)"].apply(fmt_inr)
        trend_display["Change (%)"] = trend_display["Change (%)"].apply(lambda x: f"{x:.2f}%" if pd.notna(x) else "-")
        st.dataframe(trend_display, hide_index=True, width="stretch")
    else:
        st.caption("No trend data found in this CAS.")
