"""Metric tests on tiny hand-built frames; expected values are worked out by hand in the comments."""

from datetime import date

import pandas as pd
import pytest

from retail import metrics as m
from tests.conftest import make_frame


def test_filter_frame_is_inclusive_and_filters_country(tiny):
    assert len(m.filter_frame(tiny, date(2010, 1, 1), date(2010, 1, 31), ("France",))) == 1
    # end date is inclusive of the whole day (the 11:00 order on Feb 15 is kept)
    assert "1004" in set(m.filter_frame(tiny, date(2010, 2, 15), date(2010, 2, 15))["invoice"])


def test_kpis(tiny):
    k = m.kpis(tiny)
    # product sales lines: 10 + 20 + 20 + 5 + 30 + 40; POST line and the cancellation excluded
    assert k["revenue"] == 125
    assert k["orders"] == 4
    assert k["customers"] == 2  # guest line has no id
    assert k["aov"] == pytest.approx(31.25)
    assert k["units"] == 13
    assert k["cancelled_value"] == 20
    assert k["cancel_rate"] == pytest.approx(0.16)


def test_kpi_deltas():
    cur = {"revenue": 125.0, "cancel_rate": 0.16}
    prev = {"revenue": 100.0, "cancel_rate": 0.10}
    d = m.kpi_deltas(cur, prev)
    assert d["revenue"] == pytest.approx(0.25)
    assert d["cancel_rate"] == pytest.approx(0.06)  # percentage points, not relative
    assert m.kpi_deltas(cur, None) == {"revenue": None, "cancel_rate": None}


def test_previous_period():
    assert m.previous_period(date(2010, 2, 1), date(2010, 2, 28)) == (date(2010, 1, 4), date(2010, 1, 31))


def test_monthly_revenue_flags_partial_months(tiny):
    out = m.monthly_revenue(tiny, date(2010, 1, 1), date(2010, 2, 15)).set_index("month")
    assert out.loc["2010-01-01", "revenue"] == 50
    assert out.loc["2010-02-01", "revenue"] == 75
    assert not out.loc["2010-01-01", "partial"]
    assert out.loc["2010-02-01", "partial"]  # window stops on the 15th


def test_weekday_hour(tiny):
    grid = m.weekday_hour(tiny)
    assert list(grid.index) == m.WEEKDAYS
    assert list(grid.columns) == [9, 10, 11, 12, 13, 14]
    assert grid.loc["Mon", 10] == 1 and grid.loc["Mon", 11] == 1
    assert grid.loc["Wed", 9] == 1 and grid.loc["Wed", 14] == 1
    assert grid.to_numpy().sum() == 4


def test_products_and_pareto(tiny):
    top = m.top_products(tiny, by="revenue", n=3)
    assert list(top["stock_code"]) == ["10002", "10001", "10003"]
    row = top.set_index("stock_code").loc["10001"]
    assert (row["revenue"], row["units"], row["orders"], row["customers"]) == (35, 7, 3, 2)

    p = m.pareto(tiny)
    assert list(p["cum_revenue_share"].round(2)) == [0.48, 0.76, 1.0]
    assert m.pareto_point(p, 0.8) == 1.0
    assert m.pareto_point(p, 0.5) == pytest.approx(2 / 3)


def test_country_summary(tiny):
    c = m.country_summary(tiny).set_index("country")
    assert c.loc["United Kingdom", "revenue"] == 65
    assert c.loc["United Kingdom", "share"] == pytest.approx(0.52)
    assert c.loc["United Kingdom", "iso3"] == "GBR"
    assert c.loc["Germany", "aov"] == 40


def test_quintile_scores():
    assert list(m._quintile(pd.Series(range(1, 11)))) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    assert list(m._quintile(pd.Series(range(1, 11)), ascending=False)) == [5, 5, 4, 4, 3, 3, 2, 2, 1, 1]


def test_segment_lookup_covers_every_score():
    assert len(m.SEGMENT_LOOKUP) == 25
    assert set(m.SEGMENT_LOOKUP.values()) == set(m.SEGMENTS)
    assert m.SEGMENT_LOOKUP[(5, 5)] == "Champions"
    assert m.SEGMENT_LOOKUP[(1, 1)] == "Hibernating"
    assert m.SEGMENT_LOOKUP[(1, 5)] == "Can't Lose"


def test_rfm(tiny):
    r = m.rfm(tiny, date(2010, 2, 17)).set_index("customer_id")
    # customer 1: last bought Feb 10 (7 days), 2 orders, 10+20+5+30 = 65
    assert (r.loc[1, "recency"], r.loc[1, "frequency"], r.loc[1, "monetary"]) == (7, 2, 65)
    # customer 2: last bought Jan 20 (28 days), 1 order, 20
    assert (r.loc[2, "recency"], r.loc[2, "frequency"], r.loc[2, "monetary"]) == (28, 1, 20)
    # two customers -> rank pct 0.5 / 1.0 -> scores 3 / 5
    assert (r.loc[1, "r_score"], r.loc[1, "f_score"], r.loc[1, "segment"]) == (5, 5, "Champions")
    assert (r.loc[2, "r_score"], r.loc[2, "f_score"], r.loc[2, "segment"]) == (3, 3, "Need Attention")

    s = m.segment_summary(m.rfm(tiny, date(2010, 2, 17))).set_index("segment")
    assert s.loc["Champions", "revenue_share"] == pytest.approx(65 / 85)


def test_cohorts_use_full_history(tiny):
    acquired = m.first_purchase_month(tiny)
    c = m.cohort_table(tiny, acquired).set_index(["cohort", "period"])
    jan = pd.Timestamp("2010-01-01")
    assert c.loc[(jan, 0), "customers"] == 2
    assert c.loc[(jan, 1), "customers"] == 1
    assert c.loc[(jan, 1), "retention"] == 0.5
    assert c.loc[(jan, 0), "revenue"] == 50
    # Filtering to February must not relabel customer 1 (acquired in January) as a new Feb cohort.
    feb_only = m.filter_frame(tiny, date(2010, 2, 1), date(2010, 2, 28))
    assert m.cohort_table(feb_only, acquired).empty


def test_basket_rules():
    # baskets: {A,B} {A,B} {A,C} {B} {C}; A=20001 B=20002 C=20003
    df = make_frame([
        ("1", "2010-01-01 10:00", "20001", 1, 1.0, 1, "France"),
        ("1", "2010-01-01 10:00", "20002", 1, 1.0, 1, "France"),
        ("2", "2010-01-02 10:00", "20001", 1, 1.0, 1, "France"),
        ("2", "2010-01-02 10:00", "20002", 1, 1.0, 1, "France"),
        ("3", "2010-01-03 10:00", "20001", 1, 1.0, 1, "France"),
        ("3", "2010-01-03 10:00", "20003", 1, 1.0, 1, "France"),
        ("4", "2010-01-04 10:00", "20002", 1, 1.0, 1, "France"),
        ("5", "2010-01-05 10:00", "20003", 1, 1.0, 1, "France"),
    ])
    rules = m.basket_rules(df, min_support=0.4)  # needs >= 2 baskets: only the A,B pair qualifies
    assert set(zip(rules["antecedent"], rules["consequent"])) == {("20001", "20002"), ("20002", "20001")}
    ab = rules.set_index(["antecedent", "consequent"]).loc[("20001", "20002")]
    assert ab["support"] == pytest.approx(0.4)
    assert ab["confidence"] == pytest.approx(2 / 3)
    assert ab["lift"] == pytest.approx((2 / 3) / (3 / 5))
    assert ab["antecedent_desc"] == "ITEM 20001"


def test_returns(tiny):
    monthly = m.cancellation_monthly(tiny).set_index("month")
    assert monthly.loc["2010-01-01", "cancel_rate"] == 0
    assert monthly.loc["2010-02-01", "cancel_rate"] == pytest.approx(20 / 75)

    p = m.product_returns(tiny, min_units_sold=1).set_index("stock_code")
    assert list(p.index) == ["10002"]
    assert p.loc["10002", "return_rate"] == pytest.approx(1 / 3)

    c = m.customer_returns(tiny, min_orders=1).set_index("customer_id")
    assert c.loc[1, "cancel_rate"] == pytest.approx(20 / 65)

    by_country = m.country_returns(tiny).set_index("country")
    assert by_country.loc["United Kingdom", "cancelled"] == 20


def test_empty_frame_does_not_crash(tiny):
    empty = tiny.iloc[0:0]
    assert m.kpis(empty)["revenue"] == 0
    assert m.rfm(empty, date(2010, 1, 1)).empty
    assert m.cohort_table(empty, m.first_purchase_month(tiny)).empty
    assert m.basket_rules(empty).empty
    assert m.weekday_hour(empty).shape[0] == 7


def test_largest_cancellations(tiny):
    top = m.largest_cancellations(tiny, n=3)
    assert len(top) == 1  # only one cancelled product line in the fixture
    assert (top.loc[0, "quantity"], top.loc[0, "value"], top.loc[0, "share_of_cancelled"]) == (1, 20, 1.0)
