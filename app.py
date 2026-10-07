"""E-Commerce Product Analytics Dashboard (UCI Online Retail II).

Run: streamlit run app.py
The sidebar filters are defined here, once, and every page reads them via data.current_filters().
"""

from datetime import timedelta

import streamlit as st

from retail import data

st.set_page_config(page_title="Retail Analytics", page_icon=":material/storefront:", layout="wide")

PAGES = [
    st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page("views/products.py", title="Products", icon=":material/inventory_2:"),
    st.Page("views/geography.py", title="Geography", icon=":material/public:"),
    st.Page("views/customers.py", title="Customers (RFM)", icon=":material/group:"),
    st.Page("views/cohorts.py", title="Cohort retention", icon=":material/grid_on:"),
    st.Page("views/basket.py", title="Market basket", icon=":material/shopping_basket:"),
    st.Page("views/returns.py", title="Returns & cancellations", icon=":material/assignment_return:"),
    st.Page("views/predictions.py", title="Predictions", icon=":material/insights:"),
    st.Page("views/sql.py", title="SQL queries", icon=":material/code:"),
]
page = st.navigation(PAGES)

lo, hi = data.bounds()
PERIODS = {
    "All data (Dec 2009 – Dec 2011)": (lo, hi),
    "Calendar 2011 (to 9 Dec)": (max(lo, hi.replace(month=1, day=1)), hi),
    "Calendar 2010": (hi.replace(year=hi.year - 1, month=1, day=1), hi.replace(year=hi.year - 1, month=12, day=31)),
    "Last 90 days of data": (hi - timedelta(days=89), hi),
    "Custom range": None,
}

with st.sidebar:
    st.markdown("### Filters")
    period = st.selectbox("Period", list(PERIODS), key="period")
    if PERIODS[period] is None:
        picked = st.date_input("Date range", value=(lo, hi), min_value=lo, max_value=hi, key="custom_range",
                               format="DD/MM/YYYY")
        start, end = (picked[0], picked[1]) if len(picked) == 2 else (picked[0], picked[0])
    else:
        start, end = PERIODS[period]

    options = data.countries_by_revenue()
    chosen = st.multiselect("Countries", options, key="countries", placeholder="All countries",
                            help="Leave empty for all countries. Sorted by total revenue.")
    exclude_uk = st.toggle("Exclude United Kingdom", key="exclude_uk",
                           help="The UK is ~85% of revenue; exclude it to compare the international markets.")
    countries = tuple(chosen) if chosen else ()
    if exclude_uk:
        countries = tuple(c for c in (countries or options) if c != "United Kingdom")
        if not countries:
            st.warning("Only the UK was selected, so excluding it leaves nothing. Showing all other countries.")
            countries = tuple(c for c in options if c != "United Kingdom")

    st.session_state["filters"] = data.Filters(start, end, countries)

    st.divider()
    st.caption(
        "Data: [UCI Online Retail II](https://archive.ics.uci.edu/dataset/502/online+retail+ii) "
        "(Chen, 2012, CC BY 4.0). A UK online gift retailer, 1M invoice lines, Dec 2009 – Dec 2011. "
        "Revenue is product sales net of cancellations, in GBP."
    )

page.run()
