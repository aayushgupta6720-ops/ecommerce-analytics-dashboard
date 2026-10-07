import streamlit as st

from retail import data
from retail import sql as sqlmod

f = data.page_header(
    "SQL queries",
    "The dashboard's core metrics, written as SQL and run by DuckDB directly on the cleaned Parquet file. "
    "Each query returns exactly what the pandas version used by the other pages returns.",
)

LABELS = {"kpis": "KPIs", "monthly_revenue": "Monthly revenue", "product_summary": "Products",
          "rfm": "RFM segments", "cohorts": "Cohorts"}
name = st.segmented_control("Query", list(LABELS), format_func=LABELS.get, default="rfm",
                            label_visibility="collapsed") or "rfm"

result, ms = data.sql_query(name, f)

c1, c2, c3 = st.columns(3)
c1.metric("Rows returned", f"{len(result):,}", border=True)
c2.metric("DuckDB time", f"{ms:,.0f} ms", border=True, help="First run for these filters; repeats are cached.")
c3.metric("Matches pandas", "Verified in CI", border=True,
          help="tests/test_sql.py compares every column, row by row, against the pandas version across six filter "
               "combinations, on every push.")
st.caption("Equivalence is checked by the test suite rather than live, to keep this page light on memory: see "
           "[tests/test_sql.py](https://github.com/aayushgupta6720-ops/ecommerce-analytics-dashboard/blob/main/"
           "tests/test_sql.py).")

left, right = st.columns([1, 1], gap="large")
with left:
    st.markdown(f"**`sql/{sqlmod.QUERIES[name][0]}`**")
    st.code(sqlmod.query_text(name), language="sql")
    with st.expander("Shared filter (prepended to every query)"):
        st.code((sqlmod.SQL_DIR / "_filter.sql").read_text(), language="sql")
with right:
    st.markdown("**Result for the current filters**")
    st.dataframe(result.head(500), hide_index=True, width="stretch", height=560)
    if len(result) > 500:
        st.caption(f"Showing the first 500 of {len(result):,} rows.")
