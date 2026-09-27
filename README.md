# E-Commerce Product Analytics Dashboard

An interactive analytics dashboard for the [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
dataset: 1,067,371 invoice lines from a UK online gift retailer, December 2009 to December 2011. It's built
with Streamlit and Plotly, and there's a matching Power BI kit.

**Live demo:** https://ecommerce-analytics-dashboard-aapp.onrender.com. It runs on Render's free plan,
which sleeps after 15 minutes idle, so the first visit can take about a minute to wake up.

![Overview page](docs/screenshots/overview.png)

## What's in it

Seven pages. Every page responds to the sidebar filters: period, countries, and *Exclude United Kingdom*.

| Page | What it answers |
|---|---|
| **Overview** | Revenue, orders, customers, AOV, units and cancellation rate, with change vs the previous period. Monthly trend with a year-over-year view. Weekday × hour heatmap. Top countries. |
| **Products** | Top N products by revenue, units or orders. Pareto curve (22% of products bring in 80% of revenue). Per-product drill-down: monthly sales, price points, countries. |
| **Geography** | Choropleth, country table, highest average order values, revenue by region. |
| **Customers (RFM)** | Recency/Frequency/Monetary scores and 10 segments. Customer share vs revenue share (Champions are 14% of customers and 51% of revenue). Every-customer scatter. CSV export. |
| **Cohort retention** | Monthly acquisition cohorts × months since first order: retention %, active customers or revenue. |
| **Market basket** | Pairwise association rules (support, confidence, lift) with adjustable thresholds and a *customers who bought X also bought* lookup. |
| **Returns & cancellations** | Cancellation rate over time, largest single cancellations, most-cancelled products, and cancellations by country and by customer. |

| Customers (RFM), dark theme | Market basket |
|---|---|
| ![RFM page](docs/screenshots/customers-rfm-dark.png) | ![Market basket page](docs/screenshots/market-basket.png) |

## Quick start

```bash
python3.12 -m venv venv
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/streamlit run app.py
```

The cleaned dataset (`data/processed/transactions.parquet`, 7.5 MB) is committed, so the app runs without
downloading anything. To rebuild it from the source file (the download is 45 MB; parsing the xlsx takes a
few minutes):

```bash
./venv/bin/python scripts/build_dataset.py
```

## Data cleaning

`scripts/build_dataset.py` downloads the UCI zip, reads both sheets, and logs every step:

| Step | Rows |
|---|---:|
| Raw (two sheets: 525,461 + 541,910) | 1,067,371 |
| Drop exact duplicates (the sheets overlap on 1–9 Dec 2010, and some lines repeat within a sheet) | −34,335 |
| Drop bad-debt adjustment invoices (`A…`) | −6 |
| Drop lines with price ≤ 0 (stock write-offs, samples) | −6,014 |
| Drop cancellations with a positive quantity | −1 |
| **Clean** | **1,027,015** |

Rows aren't dropped for being cancellations or non-product lines. Instead they're flagged:
- `is_cancellation`: the invoice number starts with `C`.
- `is_product`: the stock code matches `^\d{5}[A-Z]*$`. This excludes POST, DOT, M, bank charges,
  Amazon fees, gift vouchers and similar.

Each stock code gets one canonical description (its most common one), and a few country names are
cleaned up (EIRE → Ireland, RSA → South Africa, USA → United States).

## Metric definitions

All metrics live in [`retail/metrics.py`](retail/metrics.py): pure pandas functions with no Streamlit
code, unit-tested on hand-computed fixtures.

- **Revenue**: gross product sales, quantity × price in GBP, excluding cancellations and non-product lines.
  **Orders**: distinct sales invoices. **AOV**: revenue ÷ orders.
- **Customers**: distinct customer IDs. About 23% of sales lines have no customer ID. They count towards
  revenue, products and countries, but not towards RFM or cohorts.
- **Change vs previous period**: compared with the window of the same length just before the selected
  one. It's hidden when that window starts before the data does.
- **Cancellation rate**: value of cancelled product lines ÷ gross product sales. Two orders placed and
  cancelled in full account for 34% of all cancelled value; the Returns page shows them.
- **RFM**:
  - The snapshot date is the day after the selected period ends.
  - Scores are quintiles taken on rank, so ties and small selections never produce duplicate bin edges.
  - Segments come from the standard 10-segment map of R and F scores.
- **Cohorts**: a customer's cohort is the month of their first purchase in the **full** dataset, so
  narrowing the date filter never relabels a returning customer as new. The Dec 2009 cohort also holds
  customers who were already buying before the data starts.
- **Market basket**: pairs are computed from a sparse invoice × product matrix (`Xᵀ·X`) after dropping
  products below minimum support. This takes about 0.2s on 40k orders and avoids the dense one-hot
  matrix that apriori libraries build.
- The **last month is partial** (the data ends on 9 Dec 2011). Trend charts mark it with a hollow point.

## Project layout

```
app.py                 entry point: navigation + global sidebar filters
views/                 one file per page
retail/metrics.py      all calculations (pure pandas, tested)
retail/data.py         Streamlit caching layer: one shared DataFrame, aggregates cached per filter
retail/charts.py       Plotly builders sharing one palette in light and dark themes
retail/countries.py    country names -> ISO-3 codes and regions
scripts/               dataset build, Power BI export, PBIP generator + validator
powerbi/               Power BI kit (see powerbi/BUILD_GUIDE.md)
tests/                 metrics, page smoke tests (Streamlit AppTest), Power BI export checks
```

## Power BI

[`powerbi/BUILD_GUIDE.md`](powerbi/BUILD_GUIDE.md) explains how to rebuild the dashboard in Power BI.
`python scripts/export_powerbi.py` produces:

- a star schema (1 fact, 4 dimensions, 3 precomputed analysis tables) as CSVs and as one Excel workbook
  you can upload at app.powerbi.com;
- 18 DAX measures (`powerbi/measures.dax`);
- tie-out numbers computed with the app's own metric code, so you can confirm Power BI matches;
- a generated **Power BI Project** (`RetailAnalytics.pbip`: a TMDL model and a 7-page PBIR report).

`scripts/validate_pbip.py` checks the project against Microsoft's published JSON schemas and checks that
every field reference exists. It hasn't been opened in Power BI Desktop yet, so treat it as a starting
point. The data files aren't committed because they're large; the export regenerates them in about
3 minutes.

## Deploying to Render

`render.yaml` defines a free-plan Python web service:

- **Build**: installs `requirements.txt`, then converts the parquet file to an uncompressed Arrow file.
  The app memory-maps that file at startup.
- **Start**: `streamlit run app.py` on `$PORT`, with health check `/_stcore/health`.

Memory was profiled for Render's 512 MB instance:
- The app peaks at about 310 MB on macOS after visiting every page in both themes (it started at 540 MB),
  and at about 370 MB on the live Render instance.
- Most of the saving came from calculations copying only the columns they need, integer-code basket
  counting, numpy bincounts for the landing-page metrics, and the memory-mapped Arrow load.
- `MALLOC_ARENA_MAX=2` stops glibc from growing a memory arena per session thread.

## Tests

```bash
./venv/bin/pytest
```

- **Metrics:** hand-worked fixtures covering KPIs, deltas, partial months, Pareto, RFM scoring and
  segments, cohorts (including the filter-relabelling case), basket support/confidence/lift, and returns.
- **App:** every page rendered with default filters, a narrow filter (Portugal, one quarter) and an empty
  one (Iceland on a Saturday).
- **Power BI export:** foreign-key integrity, column order vs the model, totals equal to the app's KPIs,
  the workbook round-trip, and PBIP validation.

## Data source

Chen, D. (2012). *Online Retail II*. UCI Machine Learning Repository.
https://archive.ics.uci.edu/dataset/502/online+retail+ii. Licensed under CC BY 4.0.
