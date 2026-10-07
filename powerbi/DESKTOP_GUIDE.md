# Building the report in Power BI Desktop (Windows)

A 7-page report built from `data/RetailAnalytics.xlsx`, showing the same numbers as the Streamlit app.
It takes about 1–2 hours the first time. Menu names are from recent Power BI Desktop versions; if one
differs slightly, look for the closest match.

## 0. Put the files on the PC

1. Copy `RetailAnalytics_PowerBI_kit.zip` to the Windows PC.
2. Right-click it, choose **Extract All…**, and set the destination to `C:\RetailAnalytics`.
3. Check that you now have `C:\RetailAnalytics\powerbi\data\RetailAnalytics.xlsx`.

## Optional shortcut: try the ready-made project (5 minutes)

`C:\RetailAnalytics\powerbi\RetailAnalytics.pbip` is a generated Power BI Project. It contains the model,
measures and 7 pages of visuals. It has never been opened in Power BI, so it may not load.

1. In Power BI Desktop, choose **File → Open** and select `RetailAnalytics.pbip`.
2. If it opens, choose **Home → Transform data → Edit parameters**. Check that **DataFolder** is
   `C:\RetailAnalytics\powerbi\data\`, then click **Refresh**.
3. If both steps work, skip to step 9 (theme) and polish the pages from step 10.
4. If it shows any error, copy the message and send it to me, then build it manually from step 1.

## 1. Settings, before loading anything

1. Open a new blank report.
2. Go to **File → Options and settings → Options → Current File → Data Load** and untick:
   - **Autodetect new relationships after data is loaded**, so you create the right ones yourself
   - **Auto date/time**, because the model has its own date table

## 2. Load the data

1. Choose **Home → Get data → Excel workbook** and open `C:\RetailAnalytics\powerbi\data\RetailAnalytics.xlsx`.
2. In the Navigator, tick the **8 tables** (the items with the table icon, not the sheet icon):
   - `fact_sales`
   - `dim_date`, `dim_product`, `dim_customer`, `dim_country`
   - `cohort_retention`, `basket_rules`, `product_returns`
3. Click **Transform Data**, not Load.
4. In Power Query, check the data type icon in each column header and fix any that are wrong:

   | Type | Columns |
   |---|---|
   | **Text** (ABC) | every `invoice`, `stock_code`, `country`, `description`, `segment`, `*_desc`, `*_name`, `quarter`, `year_month`, `cohort_label`, `iso3`, `region`, `abc_class` |
   | **Date** (not Date/Time) | `fact_sales[invoice_date]`, `dim_date[date]`, `dim_date[month_start]`, `dim_customer[acquisition_month]`, `cohort_retention[cohort]` |
   | **True/False** | `fact_sales[is_cancellation]`, `dim_date[is_partial_month]` |
   | **Decimal number** | `price`, `revenue`, `monetary`, `retention`, `support`, `confidence`, `lift`, `cancelled_value`, `return_rate` |
   | **Whole number** | everything else, e.g. `customer_id`, `quantity`, `hour`, `period`, `year` |

   To change a type, click the column, then choose **Transform → Data type**. If it asks, pick **Replace current**.

   `invoice` must be **Text**. Cancellation invoices look like `C489449`, so a number type would turn them into errors.
5. Choose **Home → Close & Apply**. Loading the 1M rows takes a minute or two.

## 3. Relationships

1. Open **Model view** (third icon on the left).
2. Choose **Home → Manage relationships → New relationship** and create these five. Each should be
   **Many to one (\*:1)** with cross-filter direction **Single**.

   | From | To |
   |---|---|
   | `fact_sales[invoice_date]` | `dim_date[date]` |
   | `fact_sales[stock_code]` | `dim_product[stock_code]` |
   | `fact_sales[customer_id]` | `dim_customer[customer_id]` |
   | `fact_sales[country]` | `dim_country[country]` |
   | `product_returns[stock_code]` | `dim_product[stock_code]` |

   `cohort_retention` and `basket_rules` stay unconnected on purpose.

## 4. Date table, sorting, hidden helpers

1. **Mark the date table.** Select `dim_date` in the Data pane, then choose **Table tools → Mark as date
   table** and pick the `date` column.
2. **Set sort columns.** In **Table view** (second icon), select each column below and choose
   **Column tools → Sort by column**:

   | Column | Sort by |
   |---|---|
   | `dim_date[month_name]` | `month_num` |
   | `dim_date[weekday_name]` | `weekday_num` |
   | `dim_customer[segment]` | `segment_order` |

3. **Hide helper columns.** Right-click `month_num`, `weekday_num` and `segment_order`, and choose **Hide in report view**.
4. **Stop summing ID-like numbers.** Select each column below and set **Column tools → Summarization** to
   **Don't summarize**:
   - `fact_sales`: `customer_id`, `hour`
   - `dim_date`: `year`, `week`
   - `dim_product`: `revenue_rank`
   - `cohort_retention`: `period`
   - `basket_rules`: `pair_baskets`, `support`, `confidence`, `lift`
5. **Set column formats** under **Column tools → Format**:
   - **Currency £** (English (United Kingdom)): `revenue`, `price`, `monetary`, `cancelled_value`
   - **Percentage**: `retention`, `support`, `confidence`, `return_rate`

## 5. Add all 18 measures at once

1. Open **DAX query view**, the last icon on the left.
2. Open `C:\RetailAnalytics\powerbi\measures_query.dax` in Notepad, copy everything, and paste it into
   the query editor.
3. Click **Run**. A four-row table appears.
4. Compare it with the numbers in step 11. They should match exactly.
5. Click **Update model with changes** in the ribbon (or the *Update model* link above `DEFINE`) to add
   all 18 measures to the model.
6. If that button isn't there (older Desktop), create each measure yourself:
   1. Select its home table.
   2. Choose **New measure**.
   3. Paste the matching block from `measures.dax`.

## 6. Format the measures

In **Model view**, expand the tables in the Data pane. Ctrl-click the measures that share a format, then
set it in the **Properties** pane:

| Format | Measures |
|---|---|
| **Currency £, 0 decimals** | Revenue, Revenue PY, Cancelled Value, Net Revenue, Segment Revenue |
| **Currency £, 2 decimals** | Avg Order Value |
| **Whole number, thousands separator** | Orders, Customers, Units, Units Cancelled, Segment Customers |
| **Percentage, 1 decimal** | Country Revenue Share, Revenue YoY %, Pareto Cumulative %, Cancellation Rate, Segment Customer Share, Segment Revenue Share, Retention % |

Optionally, set **Display folder** to Sales, Time, Products, Returns, Customers or Cohorts to group them.

## 7. Theme

1. Choose **View → Themes → Browse for themes**.
2. Open `C:\RetailAnalytics\powerbi\RetailAnalytics.Report\StaticResources\RegisteredResources\RetailTheme.json`.

This gives the report the same colours as the Streamlit app.

## 8. Pages and slicers

1. Create 7 pages: double-click each page tab to rename it, and click **+** to add pages.
   - Overview
   - Products
   - Geography
   - Customers (RFM)
   - Cohort retention
   - Market basket
   - Returns & cancellations
2. **Slicers** (Overview first, then copy them to Products, Geography and Returns):
   - **Date slicer:** add a *Slicer* visual with `dim_date[date]`. In **Format → Slicer settings → Style**, choose **Between**.
   - **Country slicer:** add a *Slicer* with `dim_country[country]`. Set its style to **Dropdown**.
   - **Copy to other pages:** copy both slicers (Ctrl+C) and paste them on each of those pages. When Power
     BI asks whether to sync the visuals, click **Sync**.
3. Customers (RFM), Cohort retention and Market basket get **no** date/country slicers. Their tables are
   computed over the full period, so slicers wouldn't change them. Add a small text box saying
   "All dates, all countries".

## 9. Build the visuals

Each visual below is written as **visual type**: field wells. Fields are *table[column]*; measures are in **bold**.

### Overview
- **Card** ×5, one each for **Revenue**, **Orders**, **Customers**, **Avg Order Value** and **Cancellation Rate**.
  The newer card visual can hold all five in one visual.
- **Line chart**, titled "Monthly revenue".
  - X-axis: `dim_date[month_start]`. Y-axis: **Revenue**.
  - Optional: add **Revenue PY** as a second line.
- **Matrix**, titled "Orders by weekday and hour".
  - Rows: `dim_date[weekday_name]`. Columns: `fact_sales[hour]`. Values: **Orders**.
  - In **Format → Cell elements**, turn **Background color** on and choose a light-to-dark blue gradient.
  - Turn off row and column subtotals.
- **Clustered bar chart**, titled "Top 10 countries by revenue".
  - Y-axis: `dim_country[country]`. X-axis: **Revenue**.
  - Filters pane → `country` → **Top N**, show top 10 by **Revenue**, then apply.
  - Sort descending by Revenue (**⋯ → Sort axis**).

### Products
- **Clustered bar chart**, titled "Top 10 products by revenue".
  - Y-axis: `dim_product[description]`. X-axis: **Revenue**. Filter to Top N 10 by Revenue.
- **Table** with `dim_product[stock_code]`, `description`, `abc_class`, **Revenue**, **Units**, **Orders**
  and **Pareto Cumulative %**, sorted by Revenue descending.
- **Line chart**, titled "Revenue concentration (80/20)".
  - X-axis: `dim_product[revenue_rank]`. In **Format → X-axis**, set the type to **Continuous**.
  - Y-axis: **Pareto Cumulative %**.

### Geography
- **Map**: use **Azure Map** (or **Filled map**).
  - Location: `dim_country[country]`. Bubble size (or colour saturation): **Revenue**.
  - If map visuals are disabled: turn on **File → Options → Global → Security → Use Map and Filled Map
    visuals**. If your organisation blocks them, skip the map.
- **Table** with `dim_country[country]`, `region`, **Revenue**, **Country Revenue Share**, **Orders**,
  **Customers** and **Avg Order Value**, sorted by Revenue.
- Tip: the UK is about 85% of revenue. Use the country slicer to exclude it and compare the other markets.

### Customers (RFM)
- **Treemap**: Category `dim_customer[segment]`, Values **Segment Customers**, Tooltips **Segment Revenue Share**.
- **Clustered bar chart**, titled "Customer share vs revenue share".
  - Y-axis: `dim_customer[segment]`. X-axis: **Segment Customer Share** and **Segment Revenue Share**.
- **Table** with `segment`, **Segment Customers**, **Segment Customer Share**, **Segment Revenue** and
  **Segment Revenue Share**.

### Cohort retention
- **Matrix**: Rows `cohort_retention[cohort_label]`, Columns `cohort_retention[period]`, Values **Retention %**.
  - Filters on this visual → `period` → **Advanced filtering** → *is greater than* 0. Month 0 is always 100%.
  - Add a background colour gradient, as on the Overview heatmap.
- **Clustered column chart**, titled "Average retention by month": X-axis `period`, Y-axis **Retention %**.
- **Text box**: "The Dec 2009 cohort includes customers from before the data starts, so it retains best."

### Market basket
- **Slicer** on `basket_rules[antecedent_desc]`: Dropdown style, single select, with search turned on.
  Title it "Customers who bought…".
- **Table** with `antecedent_desc`, `consequent_desc`, `pair_baskets`, `support`, `confidence` and `lift`,
  sorted by `lift` descending.

### Returns & cancellations
- **Card** ×3 for **Cancelled Value**, **Cancellation Rate** and **Net Revenue**.
- **Line chart**: X-axis `dim_date[month_start]`, Y-axis **Cancellation Rate**.
- **Clustered column chart**: X-axis `dim_date[month_start]`, Y-axis **Cancelled Value**.
  Keep this separate from the line chart; don't combine them with a second axis.
- **Table** with `product_returns[description]`, `units_sold`, `units_cancelled`, `cancelled_value` and
  `return_rate`, sorted by `cancelled_value`.
- **Text box**: "Two orders cancelled in full make up about 34% of cancelled value (9 Dec 2011, 18 Jan 2011)."

## 10. Polish

- Give every visual a clear title: select it, then **Format → General → Title**.
- On cards, set **Display units** to *None* while checking numbers; switch back to *Auto* afterwards.
- Align visuals with **Format → Align** and keep the same margins on every page.

## 11. Check the numbers

With the slicers set as described, the cards should show exactly:

| Slice | Revenue | Orders | Customers | Avg Order Value | Cancellation Rate |
|---|---:|---:|---:|---:|---:|
| All data | £19,642,692.15 | 39,516 | 5,852 | £497.08 | 3.65% |
| Calendar 2011 | £9,471,248.64 | 18,223 | 4,214 | £519.74 | 4.84% |
| France, all dates | £311,090.29 | 598 | 93 | £520.22 | 5.68% |
| Germany, Q1 2011 | £36,984.58 | 81 | 41 | £456.60 | 3.40% |

Other reference values:
- Top product: Regency Cakestand 3 Tier, £330,590.32
- Champions segment: 826 customers
- Jan 2010 cohort, month 1: 21.47%
- Strongest basket rule: Poppy's Playhouse Livingroom → Bedroom, lift 45.95

## 12. Save and show it off

- **Save:** choose **File → Save as** and save `RetailAnalytics.pbix`.
- **Export:** choose **File → Export → Export to PDF** for a shareable copy. Take screenshots of each page
  for your GitHub README and LinkedIn project media.
- **Publishing:** this needs a Power BI license (the prompt you saw in the browser). It isn't needed for
  a portfolio; the .pbix file, the PDF and the screenshots are enough.
