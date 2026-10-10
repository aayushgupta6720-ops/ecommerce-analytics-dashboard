"""Insight functions: hand-checked on small frames."""

import pandas as pd
import pytest

from retail import insights, metrics
from tests.conftest import make_frame


def test_data_years(tiny):
    # Jan 4 to Feb 16 2010 inclusive is 44 days
    assert insights.data_years(tiny) == pytest.approx(44 / 365.25)


def test_guests(tiny):
    g = insights.guests(tiny)
    # 6 product sales lines, one from a guest (order 1004, 2 x 20 = 40 of 125 gross)
    assert g["line_share"] == pytest.approx(1 / 6)
    assert (g["gross"], g["gross_share"]) == (40, pytest.approx(40 / 125))
    assert (g["orders"], g["orders_share"]) == (1, 0.25)


def test_bulk_cancellations():
    df = make_frame([
        ("1", "2010-01-04 10:00", "10001", 2000, 1.0, 1, "France"),
        ("C2", "2010-01-04 11:00", "10001", -2000, 1.0, 1, "France"),   # big, cancelled in full: 2,000
        ("3", "2010-01-05 10:00", "10002", 10, 5.0, 2, "France"),
        ("C4", "2010-01-06 10:00", "10002", -10, 5.0, 2, "France"),     # small cancellation: 50
    ])
    b = insights.bulk_cancellations(df, unit_threshold=1000)
    assert (b["cancelled_lines"], b["cancelled_value"]) == (1, 2000)
    assert b["share_of_cancelled"] == pytest.approx(2000 / 2050)
    assert b["sales_lines_to_confirm"] == 1


def test_peak_season_share():
    # 24 full months, Dec 2009 - Nov 2011: 100 a month, Sep-Nov 300 a month
    rows = []
    for i, month in enumerate(pd.date_range("2009-12-01", periods=24, freq="MS")):
        price = 300.0 if month.month in (9, 10, 11) else 100.0
        rows.append((str(1000 + i), f"{month:%Y-%m}-01 10:00", "10001", 1, price, 1, "France"))
    rows.append(("9999", "2011-12-05 10:00", "10001", 1, 100.0, 1, "France"))  # partial last month, ignored
    p = insights.peak_season(make_frame(rows))
    assert len(p["years"]) == 2
    for y in p["years"]:
        assert y["peak_share"] == pytest.approx(900 / (9 * 100 + 900))
        assert y["top_month_multiple"] == pytest.approx(300 / (1800 / 12))


def test_winback_and_champions_follow_rfm():
    # 10 customers: c1-c2 bought often long ago (lapsed), the rest bought recently.
    rows, inv = [], 0
    for c in (1, 2):
        for d in ("2010-01-05", "2010-02-05", "2010-03-05", "2010-04-05", "2010-05-05", "2010-06-05"):
            inv += 1
            rows.append((str(inv), f"{d} 10:00", "10001", 10, 10.0 * c, c, "France"))
    for c in range(3, 11):
        for d in ("2011-11-01", "2011-11-20")[: 1 + (c % 2)]:
            inv += 1
            rows.append((str(inv), f"{d} 10:00", "10002", 1, 5.0, c, "France"))
    df = make_frame(rows)
    snap = pd.Timestamp("2011-12-01").date()
    r = metrics.rfm(df, snap).set_index("customer_id")
    w = insights.winback(df, snap)
    assert set(w["segment"]) <= set(insights.LAPSED_SEGMENTS)
    assert set(w["customer_id"]) == set(r.index[r["segment"].isin(insights.LAPSED_SEGMENTS)])
    assert w["last_active_year_revenue"].is_monotonic_decreasing
    # customer 2 spent 6 x 10 x 20 = 1,200 in the 365 days up to their last order (Jun 2010)
    assert w.set_index("customer_id").loc[2, "last_active_year_revenue"] == 1200
    ch = insights.champions(df, snap)
    assert set(ch["customer_id"]) == set(r.index[r["segment"] == "Champions"])


def test_bundle_opportunities():
    # baskets: {A,B} {A,B} {A,C} {B} {C}; A=20001 B=20002 C=20003, every line worth 1
    baskets = {"1": ["20001", "20002"], "2": ["20001", "20002"], "3": ["20001", "20003"],
               "4": ["20002"], "5": ["20003"]}
    df = make_frame([(inv, f"2010-01-0{inv} 10:00", code, 1, 1.0, 1, "France")
                     for inv, codes in baskets.items() for code in codes])
    b = insights.bundle_opportunities(df, top_n=5, min_support=0.4)
    assert len(b) == 1  # A->B and B->A are one pair
    row = b.iloc[0]
    assert (row["antecedent"], row["consequent"]) == ("20001", "20002")
    assert row["orders_without_b"] == 1      # order 3 had A without B
    assert row["value_per_b_order"] == 1.0   # B: 3 orders, 3 revenue
    assert row["missed_value"] == 1.0


def test_the_winback_baseline_is_who_came_back_without_a_campaign():
    # Two lapsed customers a year before the snapshot; one orders again within that year.
    rows, inv = [], 0
    for c in (1, 2):
        for d in ("2009-12-05", "2010-01-05", "2010-02-05", "2010-03-05"):
            inv += 1
            rows.append((str(inv), f"{d} 10:00", "10001", 10, 10.0, c, "France"))
    for c in range(3, 11):  # recent buyers, so 1 and 2 rank as lapsed
        for d in ("2010-11-01", "2010-11-20"):
            inv += 1
            rows.append((str(inv), f"{d} 10:00", "10002", 1, 5.0, c, "France"))
    rows.append((str(inv + 1), "2011-05-01 10:00", "10001", 10, 10.0, 1, "France"))  # customer 1 comes back
    base = insights.winback_baseline(make_frame(rows), pd.Timestamp("2011-12-01").date())
    assert base["customers"] == 2 and base["returned_share"] == 0.5
    assert base["spend_ratio"] == pytest.approx(100 / 400)  # 1 order of 100 against 4 the year before


def test_same_range_spots_variants_and_matching_pieces():
    from retail.metrics import same_range

    assert same_range("POPPY'S PLAYHOUSE KITCHEN", "POPPY'S PLAYHOUSE BEDROOM")
    assert same_range("PINK  POLKADOT CUP", "BLUE POLKADOT CUP")
    assert same_range("RED STRIPE CERAMIC DRAWER KNOB", "BLUE SPOT CERAMIC DRAWER KNOB")
    assert not same_range("RECYCLING BAG RETROSPOT", "TOY TIDY PINK POLKADOT")
    assert not same_range("JUMBO BAG RED RETROSPOT", "LUNCH BOX I LOVE LONDON")
