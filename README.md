# Portfolio Summary & XIRR Tracker

A small Streamlit app that turns your CDSL Consolidated Account Statement
(CAS) PDF into a clean, tabular summary of your holdings by asset class —
Equity, Mutual Fund Folios, Mutual Funds Held in Demat Form, and Others
(government securities / Sovereign Gold Bonds) — with charts, a downloadable
Excel breakdown, and an XIRR calculator.

## Why XIRR needs a second source

Your monthly CAS tells you what you hold *today* and, for mutual funds, the
cumulative amount you've invested — enough to compute an absolute return %.
It does **not** list the date and amount of every individual purchase, SIP
instalment, or redemption, which is what a true XIRR (money-weighted,
annualised return) requires.

So the app does two things:
1. **Always works, from the CAS alone:** the asset-class summary, holdings
   tables, and each mutual fund's absolute return % (which CAMS/KFIN already
   compute and print on the statement).
2. **Optional, for real XIRR:** bring in transaction history from the
   **Connect & Import** tab, from any combination of:
   - **Zerodha (live)** — current holdings + average buy price via Zerodha's
     free Kite Connect Personal API (`zerodha_connector.py`). Gives real
     equity cost basis instantly, but not purchase dates.
   - **Zerodha (Tradebook CSV)** — full dated buy/sell history, exported
     free from Console → Reports → Tradebook (`zerodha_tradebook.py`).
     This is what actually feeds equity XIRR.
   - **Kuvera (statement)** — Kuvera has no public API, so this parses the
     transaction statement (.xlsx) you export from Kuvera's own Reports
     section (`kuvera_import.py`). Kuvera's export isn't a normal table —
     it's every field flattened one-per-row down a single column, with an
     inconsistent field count — so this parses by classifying each value's
     *type* (a date starts a record; text fields are scheme/buy-sell;
     numbers are units/price/amount) rather than assuming a fixed position.
     Kuvera also doesn't include an ISIN, so each scheme name is matched to
     your CAS by fund house + category words (gated so "HDFC Large Cap"
     can never match "SBI Large Cap" just because both contain "large
     cap") — the app shows you the match table before you rely on it. A
     scheme with no match usually means you've fully redeemed it, which is
     fine: its buy/sell history alone is already a complete cash-flow
     cycle, so it doesn't need a current-value cash flow to compute XIRR.
   - **Manual CSV** — the original template, for anything else (PMS
     accounts, other brokers, funds outside Zerodha/Kuvera).

   Everything loaded gets pooled together and the XIRR tab computes real,
   dated XIRR per holding and per asset class.

### A deliberate omission: no Kuvera API integration

Kuvera doesn't publish a developer API. There's a community-maintained
*unofficial* spec reverse-engineering some of Kuvera's endpoints — but
inspecting it shows it only covers public market data (fund NAVs, AMC
lists, gold/crypto prices) plus login/profile — it does **not** document
any endpoint for your actual folios, holdings, or transactions. Going
further than that published spec would mean probing Kuvera's private,
undocumented endpoints directly, which isn't something this project does —
the risk (to your account, and just generally scraping a fintech platform's
internal API without any sanctioned path) isn't worth it when the Reports
export does the job safely. If Kuvera ever ships a real API, swapping it in
would be a contained change to `kuvera_import.py`.

### A note on Zerodha's live connect

Kite Connect access tokens expire daily by Zerodha's design — there's no
official way to get a long-lived personal token. So "Zerodha (live)" is a
"click connect each time you check in" flow, not a background sync. Nothing
you enter (API key, secret, or the resulting token) is written to disk —
it lives only in Streamlit's session state for that browser session.

## Project structure

```
.
├── app.py                      # Streamlit app (UI + orchestration)
├── cas_parser.py                # Parses the CDSL CAS PDF into structured data
├── zerodha_connector.py         # Kite Connect Personal API (live holdings + cost basis)
├── zerodha_tradebook.py         # Parses Zerodha Console Tradebook CSV/XLSX exports
├── kuvera_import.py             # Flexible parser for Kuvera statement exports
├── xirr.py                      # XIRR (money-weighted return) calculation
├── transactions_template.csv    # Template for the manual-entry XIRR path
├── requirements.txt
└── .streamlit/config.toml       # Streamlit server config
```

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

It'll open at `http://localhost:8501`. Upload your CAS PDF in the sidebar.

Nothing is written to disk or sent anywhere outside the running app — the
PDF and any transaction CSV you upload are parsed in memory for that
session only.

## Put it on GitHub

```bash
git init
git add .
git commit -m "Initial commit: portfolio summary & XIRR tracker"
```

Then create a new (empty) repository on GitHub — **don't** initialise it
with a README, since you already have one — and push:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

⚠️ **Before you push anything**, double-check `git status` doesn't show
your actual CAS PDF or a filled-in transaction CSV — `.gitignore` is
already set up to exclude `*.pdf` and `*.csv` (other than the template),
but it's worth a glance since this is financial data.

## Deploy to Streamlit Community Cloud (free)

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with
   GitHub.
2. Click **"New app"**, pick the repo you just pushed, branch `main`, and
   set the main file path to `app.py`.
3. Click **Deploy**. It'll install `requirements.txt` and give you a
   public URL like `https://<something>.streamlit.app`.

That URL is public by default — anyone with the link can open the app and
upload *their own* CAS (nothing of yours is stored on the server between
visits). If you'd rather keep it private, Streamlit Community Cloud lets
you restrict access to specific viewers under the app's settings, or you
can run it privately with `streamlit run app.py` on your own machine
whenever you need it.

## Extending this

- The parser (`cas_parser.py`) classifies holdings using India's standard
  ISIN prefix convention (`INE` = equity, `INF` = mutual fund, `IN0` =
  government security), so it should work on any CDSL CAS, not just this
  one — NSDL-issued CAS PDFs use a similar layout and would need light
  adjustments if the table headers differ.
- `xirr.py` is a standalone, dependency-light XIRR solver (Newton-Raphson
  with a bisection fallback) — it's reusable outside Streamlit if you want
  to fold this into a bigger net-worth tracker later.
