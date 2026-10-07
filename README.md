# E-Commerce Product Analytics Dashboard

[![CI](https://github.com/aayushgupta6720-ops/ecommerce-analytics-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/aayushgupta6720-ops/ecommerce-analytics-dashboard/actions/workflows/ci.yml)

An interactive analytics dashboard for the [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii)
dataset: 1,067,371 invoice lines from a UK online gift retailer, December 2009 to December 2011. It's built
with Streamlit and Plotly, and there's a matching Power BI kit.

**Live demo:** https://ecommerce-analytics-dashboard-aapp.onrender.com. It runs on Render's free plan,
which sleeps after 15 minutes idle, so the first visit can take about a minute to wake up.

![Overview page](docs/screenshots/overview.png)

## What's in it

Nine pages. The seven analysis pages respond to the sidebar filters (period, countries, *Exclude United Kingdom*); the SQL page runs its queries with those filters too.

| Page | What it answers |
|---|---|
| **Overview** | Revenue, orders, customers, AOV, units and cancellation rate, with change vs the previous period. Monthly trend with a year-over-year view. Weekday × hour heatmap. Top countries. |
| **Products** | Top N products by revenue, units or orders. Pareto curve (22% of products bring in 80% of revenue). Per-product drill-down: monthly sales, price points, countries. |
| **Geography** | Choropleth, country table, highest average order values, revenue by region. |
| **Customers (RFM)** | Recency/Frequency/Monetary scores and 10 segments. Customer share vs revenue share (Champions are 14% of customers and 52% of revenue). Every-customer scatter. CSV export. |
| **Cohort retention** | Monthly acquisition cohorts × months since first order: retention %, active customers or revenue. |
| **Market basket** | Pairwise association rules (support, confidence, lift) with adjustable thresholds and a *customers who bought X also bought* lookup. |
| **Returns & cancellations** | Cancellation rate over time, largest single cancellations, most-cancelled products, and cancellations by country and by customer. |
| **Predictions** | Revenue forecast, churn risk and customer lifetime value, each trained on 2010 and scored on the following year against a baseline. |
| **SQL queries** | The core metrics (KPIs, monthly revenue, products, RFM, cohorts) as SQL, run live by DuckDB on the Parquet file. |

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

- **Revenue**: product sales **net of cancellations**, quantity × price in GBP, excluding non-product lines.
  Cancelled lines carry negative revenue, so an order placed and then cancelled in full nets to zero. Before
  this rule, one 80,995-unit order that was cancelled the same day ranked as the #4 product by revenue.
  **Gross sales** (before cancellations) appear on the Returns page. **Orders**: distinct sales invoices.
  **AOV**: net revenue ÷ orders. **Units** are net of cancelled units.
- **Customers**: distinct customer IDs. About 23% of sales lines have no customer ID. They count towards
  revenue, products and countries, but not towards RFM or cohorts.
- **Change vs previous period**: compared with the window of the same length just before the selected
  one. It's hidden when that window starts before the data does.
- **Cancellation rate**: value of cancelled product lines ÷ gross sales. Two orders placed and
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

## Predictions

Each model is trained on data up to 9 Dec 2010 and scored on the following 365 days, which it never saw,
against a simple baseline. The models are implemented with numpy/scipy in [`retail/models.py`](retail/models.py),
so the 512 MB server needs no ML framework; `scripts/train_models.py` fits them and writes the results the page
reads, and CI checks those results are reproducible.

| Model | Result on the holdout year | Baseline |
|---|---|---|
| **Revenue forecast**, 12 months ahead | Same month last year misses by **8.4%** (WAPE) | 3-month average 48.7%, last month 80.0% |
| **Churn** (no purchase in the next 90 days), logistic regression | AUC **0.759**; 77% of the riskiest 20% churned (base rate 49%) | "Longest since last order": AUC 0.707 |
| **Customer lifetime value**, BG/NBD + Gamma-Gamma | Purchase error 2.36 per customer; revenue ranking (Spearman) 0.588 | Calibration-year rate 2.63; last year's spend 0.618 |

What the evaluation showed, and the page says plainly:
- **Seasonality dominates.** With one earlier year to learn from, "same month last year" is the honest benchmark.
- **Churn rates swing with the season.** About 42% of customers lapse in the 90 days after a September cutoff,
  against 63–69% after December–June cutoffs. So the churn model trains on the same season a year earlier: it
  ranks customers well, but its probabilities need re-basing each season.
- **The lifetime-value model is for ranking, not totals.** It cuts purchase-count error for customers with little
  history (2.47 vs 3.14), ranks about as well as last year's spend, and over-predicts total purchases by
  42%.
  BG/NBD also gives every one-time buyer P(alive) = 1, so expected purchases is used for ranking instead.
- Both statistical models are tested by **parameter recovery**: simulate customers from known parameters, fit,
  and check the fit recovers them and predicts the simulated future.

## SQL

The metrics are also written as SQL in [`sql/`](sql/), and DuckDB runs them directly on the Parquet file:
KPIs, monthly revenue, product summary, RFM scoring (including rank-based quintiles) and cohorts.
[`tests/test_sql.py`](tests/test_sql.py) checks that every query returns exactly what the pandas version returns,
row by row, across six filter combinations, one of which is empty. The SQL page shows each query and runs it live
for the current filters.

## Project layout

```
app.py                 entry point: navigation + global sidebar filters
views/                 one file per page
retail/metrics.py      all calculations (pure pandas, tested)
retail/data.py         Streamlit caching layer: one shared DataFrame, aggregates cached per filter
retail/charts.py       Plotly builders sharing one palette in light and dark themes
retail/countries.py    country names -> ISO-3 codes and regions
retail/models.py       BG/NBD, Gamma-Gamma, logistic regression, forecast baselines, evaluation helpers
retail/sql.py          runs sql/*.sql with DuckDB on the Parquet file
sql/                   the metrics as SQL (KPIs, monthly revenue, products, RFM, cohorts)
scripts/               dataset build, model training, Power BI export, PBIP generator + validator
powerbi/               Power BI kit (see powerbi/BUILD_GUIDE.md)
tests/                 metrics, SQL-vs-pandas equivalence, models, page smoke tests, Power BI export checks
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

## Tests and CI

```bash
./venv/bin/ruff check .   # lint
./venv/bin/pytest          # tests
```

GitHub Actions runs the same checks on every push and pull request (`.github/workflows/ci.yml`):
1. Lint with ruff.
2. Regenerate the Power BI kit and fail if the committed measures, project files or tie-out numbers
   no longer match the app's code.
3. Validate the Power BI project against Microsoft's published schemas.
4. Re-train the models and fail if their metrics differ from the committed results.
5. Run the full test suite.

The tests cover:
- **Metrics:** hand-worked fixtures covering KPIs, deltas, partial months, Pareto, RFM scoring and
  segments, cohorts (including the filter-relabelling case), basket support/confidence/lift, and returns.
- **App:** every page rendered with default filters, a narrow filter (Portugal, one quarter) and an empty
  one (Iceland on a Saturday).
- **SQL:** each query equals its pandas counterpart, row by row, across six filter combinations.
- **Models:** parameter recovery on simulated customers for BG/NBD and Gamma-Gamma, coefficient recovery for
  the logistic regression, AUC against a brute-force count, and hand-checked dataset builders.
- **Power BI export:** foreign-key integrity, column order vs the model, totals equal to the app's KPIs,
  the workbook round-trip, and PBIP validation.

## Data source

Chen, D. (2012). *Online Retail II*. UCI Machine Learning Repository.
https://archive.ics.uci.edu/dataset/502/online+retail+ii. Licensed under CC BY 4.0.
