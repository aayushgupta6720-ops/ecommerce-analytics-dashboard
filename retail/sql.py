"""Run the queries in sql/ with DuckDB, directly on the cleaned Parquet file.

Each query mirrors a pandas function in retail/metrics.py (tests/test_sql.py checks they return the
same numbers). DuckDB streams the Parquet file under a 64MB memory cap.
"""

from __future__ import annotations

import re
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
DATA_PATH = ROOT / "data" / "processed" / "transactions.parquet"

# name -> (file, pandas counterpart in retail/metrics.py), in the order the SQL page shows them
QUERIES = {
    "kpis": ("kpis.sql", "kpis"),
    "monthly_revenue": ("monthly_revenue.sql", "monthly_revenue"),
    "product_summary": ("product_summary.sql", "product_summary"),
    "rfm": ("rfm.sql", "rfm"),
    "cohorts": ("cohorts.sql", "cohort_table"),
}


def query_text(name: str) -> str:
    return (SQL_DIR / QUERIES[name][0]).read_text()


def full_sql(name: str) -> str:
    """The query with the shared filter CTE prepended, exactly as DuckDB runs it."""
    body = query_text(name)
    filt = "\n".join(line for line in (SQL_DIR / "_filter.sql").read_text().splitlines()
                     if not line.startswith("--"))
    match = re.search(r"^WITH\s", body, flags=re.M)
    if match:  # join our CTE onto the query's own WITH list
        return f"{body[:match.start()]}WITH {filt},\n{body[match.end():]}"
    return f"WITH {filt}\n{body}"


def connect(path: Path = DATA_PATH) -> duckdb.DuckDBPyConnection:
    # A small memory cap keeps DuckDB from holding big hash tables on the 512MB Render instance;
    # anything larger spills to a temp directory. Measured: about +90MB for all five queries.
    con = duckdb.connect(config={"threads": 1, "memory_limit": "64MB", "preserve_insertion_order": False,
                                 "temp_directory": tempfile.mkdtemp(prefix="duckdb-")})
    con.execute(f"CREATE VIEW transactions AS SELECT * FROM read_parquet('{path.as_posix()}')")
    return con


def run(con: duckdb.DuckDBPyConnection, name: str, start: date, end: date,
        countries: tuple[str, ...] = ()) -> pd.DataFrame:
    params = {
        "start": datetime.combine(start, datetime.min.time()),
        "end_excl": datetime.combine(end + timedelta(days=1), datetime.min.time()),
        "countries": list(countries),
    }
    sql = full_sql(name)
    if "$snapshot" in sql:
        params["snapshot"] = end + timedelta(days=1)  # same convention as the RFM page
    # DuckDB needs a type for an empty list parameter.
    sql = sql.replace("$countries", "$countries::VARCHAR[]")
    return con.execute(sql, params).df()
