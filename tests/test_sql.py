"""The SQL queries in sql/ must return exactly what the pandas metrics return, for any filter."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from retail import metrics, sql

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "all data": (date(2009, 12, 1), date(2011, 12, 9), ()),
    "calendar 2011": (date(2011, 1, 1), date(2011, 12, 9), ()),
    "France": (date(2009, 12, 1), date(2011, 12, 9), ("France",)),
    "Germany Q1 2011": (date(2011, 1, 1), date(2011, 3, 31), ("Germany",)),
    "two countries, mid-month window": (date(2010, 3, 15), date(2010, 9, 20), ("Netherlands", "EIRE", "Ireland")),
    "empty (Iceland on a Saturday)": (date(2011, 3, 5), date(2011, 3, 5), ("Iceland",)),
}


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return pd.read_parquet(ROOT / "data" / "processed" / "transactions.parquet")


@pytest.fixture(scope="module")
def con():
    c = sql.connect()
    yield c
    c.close()


def frame(df, case):
    start, end, countries = CASES[case]
    return metrics.filter_frame(df, start, end, countries)


def assert_same(expected: pd.DataFrame, got: pd.DataFrame, key: list[str], columns: list[str]):
    assert len(got) == len(expected)
    if expected.empty:
        return
    e = expected.sort_values(key).reset_index(drop=True)
    g = got.sort_values(key).reset_index(drop=True)
    for col in columns:
        ev, gv = e[col].to_numpy(), g[col].to_numpy()
        if np.issubdtype(np.asarray(ev).dtype, np.number) and np.issubdtype(np.asarray(gv).dtype, np.number):
            np.testing.assert_allclose(gv.astype(float), ev.astype(float), rtol=1e-9, atol=1e-6, err_msg=col)
        else:
            assert list(map(str, gv)) == list(map(str, ev)), col


@pytest.mark.parametrize("case", CASES)
def test_kpis(df, con, case):
    start, end, countries = CASES[case]
    expected = metrics.kpis(frame(df, case))
    got = sql.run(con, "kpis", start, end, countries).iloc[0]
    for k, v in expected.items():
        assert got[k] == pytest.approx(v, rel=1e-9, abs=1e-6), k


@pytest.mark.parametrize("case", CASES)
def test_monthly_revenue(df, con, case):
    start, end, countries = CASES[case]
    expected = metrics.monthly_revenue(frame(df, case), start, end)
    got = sql.run(con, "monthly_revenue", start, end, countries)
    got["month"] = pd.to_datetime(got["month"]).astype("datetime64[s]")
    expected = expected.assign(month=pd.to_datetime(expected["month"]).astype("datetime64[s]"))
    assert_same(expected, got, ["month"], ["revenue", "orders", "customers", "partial"])


@pytest.mark.parametrize("case", CASES)
def test_product_summary(df, con, case):
    start, end, countries = CASES[case]
    expected = metrics.product_summary(frame(df, case))
    got = sql.run(con, "product_summary", start, end, countries)
    assert_same(expected, got, ["stock_code"], ["description", "revenue", "units", "orders", "customers"])


@pytest.mark.parametrize("case", CASES)
def test_rfm(df, con, case):
    start, end, countries = CASES[case]
    expected = metrics.rfm(frame(df, case), end + timedelta(days=1))
    got = sql.run(con, "rfm", start, end, countries)
    assert_same(expected, got, ["customer_id"],
                ["country", "recency", "frequency", "monetary", "r_score", "f_score", "m_score", "segment"])


@pytest.mark.parametrize("case", CASES)
def test_cohorts(df, con, case):
    start, end, countries = CASES[case]
    expected = metrics.cohort_table(frame(df, case), metrics.first_purchase_month(df))
    got = sql.run(con, "cohorts", start, end, countries)
    got["cohort"] = pd.to_datetime(got["cohort"]).astype("datetime64[s]")
    expected = expected.assign(cohort=pd.to_datetime(expected["cohort"]).astype("datetime64[s]"))
    assert_same(expected, got, ["cohort", "period"], ["customers", "revenue", "cohort_size", "retention"])


def test_every_query_has_a_pandas_counterpart():
    for file, counterpart in sql.QUERIES.values():
        assert (sql.SQL_DIR / file).exists(), file
        assert callable(getattr(metrics, counterpart)), counterpart
