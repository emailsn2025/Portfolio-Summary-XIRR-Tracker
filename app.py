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

st.set_page_config(page_title="Portfolio Summary & XIRR Tracker", page_icon="📊", layout="wide")


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

tab_summary, tab_holdings, tab_xirr, tab_trend = st.tabs(
    ["📋 Asset Class Summary", "🔍 Holdings Detail", "📈 XIRR", "🕒 12-Month Trend"]
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
    if not data.equity_holdings.empty:
        eq_display = data.equity_holdings.copy()
        eq_display["Value (₹)"] = eq_display["Value (₹)"].apply(fmt_inr)
        st.dataframe(eq_display, hide_index=True, width="stretch")
        st.caption(
            "Note: the CAS only reports current market value for equities, not your original "
            "purchase cost - so no return % is shown here. Upload a tradebook in the XIRR tab "
            "for that."
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
# Tab 3: XIRR
# --------------------------------------------------------------------------

with tab_xirr:
    st.subheader("Real XIRR needs transaction-level cash flows")
    st.write(
        "A single CAS statement gives us your *current* holdings and, for mutual funds, the "
        "cumulative amount invested - enough to compute an **absolute return %**, which you can "
        "see in the Holdings Detail tab. It does **not** give us the date and amount of every "
        "individual purchase, SIP instalment, switch, or redemption - which is what XIRR "
        "(a money-weighted, annualised return) actually needs."
    )
    with st.expander("Where to get transaction-level data"):
        st.markdown(
            "- **Mutual funds:** download a *Transaction Statement* (not a holding statement) "
            "covering \"since inception\" from [CAMS](https://www.camsonline.com), "
            "[KFinKart](https://mfs.kfintech.com), or [MF Central](https://mfcentral.com) - "
            "these list every purchase/SIP/redemption with its date and amount.\n"
            "- **Equity / SGBs held in demat:** export your tradebook from your broker "
            "(Zerodha console, Nuvama, etc.) covering the full holding period.\n\n"
            "Format the data as shown in the template below, then upload it here."
        )

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
        "Amount convention: **negative** = money invested (purchase/SIP), "
        "**positive** = money received (redemption/dividend). "
        "Leave the final 'current value' row's Amount as 0 - the app fills it in "
        "automatically from your uploaded CAS."
    )
    st.dataframe(template, hide_index=True, width="stretch")
    st.download_button(
        "⬇️ Download transaction template (CSV)",
        data=template.to_csv(index=False).encode("utf-8"),
        file_name="transactions_template.csv",
        mime="text/csv",
    )

    st.markdown("---")
    txn_file = st.file_uploader(
        "Upload your completed transaction history (CSV)", type=["csv"], key="txn_upload"
    )

    if txn_file is not None:
        try:
            txns = pd.read_csv(txn_file)
            txns["Date"] = pd.to_datetime(txns["Date"]).dt.date
        except Exception as e:
            st.error(f"Couldn't read that CSV: {e}")
            st.stop()

        required_cols = {"Date", "AssetClass", "Identifier", "Amount"}
        missing = required_cols - set(txns.columns)
        if missing:
            st.error(f"Missing required column(s): {', '.join(missing)}")
            st.stop()

        as_of = date.today()

        # Build a lookup of current valuation per Identifier (ISIN) from the parsed CAS
        value_lookup = {}
        if not data.mf_folio_holdings.empty:
            value_lookup.update(
                data.mf_folio_holdings.set_index("ISIN")["Valuation (₹)"].to_dict()
            )
        if not data.equity_holdings.empty:
            value_lookup.update(
                data.equity_holdings.groupby("ISIN")["Value (₹)"].sum().to_dict()
            )
        if not data.mf_in_demat_holdings.empty:
            value_lookup.update(
                data.mf_in_demat_holdings.groupby("ISIN")["Value (₹)"].sum().to_dict()
            )
        if not data.other_holdings.empty:
            value_lookup.update(
                data.other_holdings.groupby("ISIN")["Value (₹)"].sum().to_dict()
            )

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
                    "Current Value Used (₹)": fmt_inr(current_val) if current_val is not None else "not found in CAS",
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
