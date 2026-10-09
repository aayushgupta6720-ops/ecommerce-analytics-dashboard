"""Golden-set evaluation for the Ask the data page.

Each case pairs a question with the request it should become and a reference figure computed by the
existing metric functions (kpis, monthly_revenue, product_summary, rfm...), independently of
metrics.breakdown. A case passes when the model's request, once resolved, matches the expected one
field by field AND the answer equals the reference figure.

    python scripts/eval_ask.py              # live: one Gemini call per case (GEMINI_API_KEY, or .env)
    python scripts/eval_ask.py --offline    # no API calls: the expected requests stand in for the model,
                                            # which checks the reference figures and the answer code
    python scripts/eval_ask.py --only 3,7   # a subset (1-based case numbers)

Passing cases marked as examples are written to data/processed/ask_examples.json for the page's gallery.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from retail import ask  # noqa: E402
from retail import metrics as m  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data" / "processed" / "transactions.parquet"
END = date(2011, 12, 9)  # last day of data


@dataclass
class Expect:
    value: float | None = None
    previous: float | None = None
    rows: list[tuple] | None = None          # the table's leading rows, in order: (key, value)
    contains: dict | None = None             # rows that must appear somewhere: key -> value


@dataclass
class Case:
    question: str
    request: dict                             # what the model should produce (unlisted fields take defaults)
    check: object = None                      # (df, ctx) -> Expect
    example: bool = False


def req(**fields) -> dict:
    base = {"answerable": True, "reason": "", "metric": "revenue", "start": "", "end": "", "countries": [],
            "product": "", "customer_id": "", "group_by": "none", "sort": "natural", "limit": 10, "compare": "none"}
    return base | fields


def sl(df, start, end, countries=()):
    return m.filter_frame(df, date.fromisoformat(start), date.fromisoformat(end), tuple(countries))


def kpi(metric, start, end, countries=()):
    return lambda df, ctx: Expect(value=m.kpis(sl(df, start, end, countries))[metric])


def non_uk(ctx):
    return [c for c in ctx.countries if c != "United Kingdom"]


def monthly(metric, start, end):
    def check(df, ctx):
        mon = m.monthly_revenue(sl(df, start, end), date.fromisoformat(start), date.fromisoformat(end))
        return Expect(rows=list(zip(mon["month"], mon[metric])))
    return check


def compare_check(metric, start, end, prev_start, prev_end, countries=()):
    def check(df, ctx):
        return Expect(value=m.kpis(sl(df, start, end, countries))[metric],
                      previous=m.kpis(sl(df, prev_start, prev_end, countries))[metric])
    return check


def top_countries(df, ctx):
    c = m.country_summary(sl(df, "2011-01-01", "2011-12-09", non_uk(ctx))).head(5)
    return Expect(rows=list(zip(c["country"], c["revenue"])))


def bottom_countries(df, ctx):
    c = m.country_summary(sl(df, "2011-01-01", "2011-12-09")).sort_values(["orders", "country"]).head(5)
    return Expect(rows=list(zip(c["country"], c["orders"])))


def top_units(df, ctx):
    p = m.product_summary(sl(df, "2010-11-01", "2010-11-30")).sort_values(["units", "stock_code"],
                                                                         ascending=[False, True]).head(3)
    return Expect(rows=list(zip(p["stock_code"], p["units"])))


def best_month(df, ctx):
    mon = m.monthly_revenue(sl(df, "2010-01-01", "2010-12-31"), date(2010, 1, 1), date(2010, 12, 31))
    best = mon.loc[mon["revenue"].idxmax()]
    return Expect(rows=[(best["month"], best["revenue"])])


def weekday_orders(df, ctx):
    grid = m.weekday_hour(df).sum(axis=1)
    return Expect(rows=[(d, v) for d, v in grid.items() if v > 0])


def busiest_hour(df, ctx):
    hours = m.weekday_hour(df).sum(axis=0)
    return Expect(rows=[(int(hours.idxmax()), hours.max())])


def champions(df, ctx):
    seg = m.segment_summary(m.rfm(df, END + timedelta(days=1))).set_index("segment")
    return Expect(contains={"Champions": seg.loc["Champions", "customers"]})


def cakestand(df, ctx):
    p = m.product_summary(sl(df, "2011-01-01", "2011-12-09")).set_index("stock_code")
    return Expect(value=p.loc["22423", "revenue"])


def customer_spend(df, ctx):
    r = m.rfm(sl(df, "2011-01-01", "2011-12-09"), END + timedelta(days=1)).set_index("customer_id")
    return Expect(value=r.loc[14646, "monetary"])


def top_customers_germany(df, ctx):
    r = m.rfm(sl(df, "2011-01-01", "2011-12-09", ["Germany"]), END + timedelta(days=1))
    r = r.sort_values(["monetary", "customer_id"], ascending=[False, True]).head(5)
    return Expect(rows=list(zip(r["customer_id"], r["monetary"])))


def products_sold(df, ctx):
    return Expect(value=len(m.product_summary(sl(df, "2010-01-01", "2010-12-31"))))


def quarterly_australia(df, ctx):
    mon = m.monthly_revenue(sl(df, "2009-12-01", "2011-12-09", ["Australia"]), date(2009, 12, 1), END)
    q = mon.groupby(mon["month"].dt.to_period("Q").dt.start_time)["revenue"].sum()
    return Expect(rows=list(q.items()))


def cancel_monthly(df, ctx):
    c = m.cancellation_monthly(sl(df, "2011-01-01", "2011-12-09"))
    return Expect(rows=list(zip(c["month"], c["cancel_rate"])))


CASES = [
    Case("What was the net revenue in 2011?", req(start="2011-01-01", end="2011-12-31"),
         kpi("revenue", "2011-01-01", "2011-12-09"), example=True),
    Case("How many orders came from France in 2010?",
         req(metric="orders", start="2010-01-01", end="2010-12-31", countries=["France"]),
         kpi("orders", "2010-01-01", "2010-12-31", ["France"])),
    Case("average order value in Germany in Q1 2011",
         req(metric="aov", start="2011-01-01", end="2011-03-31", countries=["Germany"]),
         kpi("aov", "2011-01-01", "2011-03-31", ["Germany"])),
    Case("How many customers bought something in December 2010?",
         req(metric="customers", start="2010-12-01", end="2010-12-31"),
         kpi("customers", "2010-12-01", "2010-12-31")),
    Case("What was the cancellation rate in 2011?", req(metric="cancel_rate", start="2011-01-01", end="2011-12-31"),
         kpi("cancel_rate", "2011-01-01", "2011-12-09")),
    Case("net units sold to the Netherlands last year",
         req(metric="units", start="2010-01-01", end="2010-12-31", countries=["Netherlands"]),
         kpi("units", "2010-01-01", "2010-12-31", ["Netherlands"])),
    Case("gross sales in the UK this year",
         req(metric="gross_sales", start="2011-01-01", end="2011-12-09", countries=["United Kingdom"]),
         kpi("gross_sales", "2011-01-01", "2011-12-09", ["United Kingdom"])),
    Case("Show monthly revenue for 2011", req(start="2011-01-01", end="2011-12-31", group_by="month"),
         monthly("revenue", "2011-01-01", "2011-12-09"), example=True),
    Case("Top 5 countries by revenue outside the UK in 2011",
         req(start="2011-01-01", end="2011-12-31", countries=["International"], group_by="country", sort="desc",
             limit=5), top_countries, example=True),
    Case("Which 3 products sold the most units in November 2010?",
         req(metric="units", start="2010-11-01", end="2010-11-30", group_by="product", sort="desc", limit=3),
         top_units, example=True),
    Case("What was the best month for revenue in 2010?",
         req(start="2010-01-01", end="2010-12-31", group_by="month", sort="desc", limit=1), best_month),
    Case("orders by day of the week", req(metric="orders", group_by="weekday"), weekday_orders),
    Case("How many Champions are there?", req(metric="customers", group_by="segment"), champions, example=True),
    Case("Revenue from the regency cakestand in 2011",
         req(start="2011-01-01", end="2011-12-31", product="regency cakestand"), cakestand, example=True),
    Case("How much did customer 14646 spend in 2011?",
         req(start="2011-01-01", end="2011-12-31", customer_id="14646"), customer_spend),
    Case("How did orders over the last 3 months compare with a year earlier?",
         req(metric="orders", start="2011-09-10", end="2011-12-09", compare="last_year"),
         compare_check("orders", "2011-09-10", "2011-12-09", "2010-09-10", "2010-12-09"), example=True),
    Case("Compare revenue in Germany for Q3 2011 with the previous period",
         req(start="2011-07-01", end="2011-09-30", countries=["Germany"], compare="previous_period"),
         compare_check("revenue", "2011-07-01", "2011-09-30", "2011-03-31", "2011-06-30", ["Germany"])),
    Case("monthly cancellation rate in 2011",
         req(metric="cancel_rate", start="2011-01-01", end="2011-12-31", group_by="month"), cancel_monthly),
    Case("Who were the top 5 customers by spend in Germany in 2011?",
         req(start="2011-01-01", end="2011-12-31", countries=["Germany"], group_by="customer", sort="desc", limit=5),
         top_customers_germany),
    Case("How many different products were sold in 2010?",
         req(metric="products", start="2010-01-01", end="2010-12-31"), products_sold),
    Case("quarterly revenue for Australia", req(countries=["Australia"], group_by="quarter"), quarterly_australia),
    Case("Which 5 countries placed the fewest orders in 2011?",
         req(metric="orders", start="2011-01-01", end="2011-12-31", group_by="country", sort="asc", limit=5),
         bottom_countries),
    Case("What hour of the day gets the most orders?",
         req(metric="orders", group_by="hour", sort="desc", limit=1), busiest_hour),
    Case("Predict next month's revenue", req(answerable=False)),
    Case("Which products are usually bought together?", req(answerable=False)),
    Case("What's the weather in London today?", req(answerable=False)),
    Case("What is the retention rate of the January 2011 cohort?", req(answerable=False)),
    Case("Write a poem about our sales team", req(answerable=False)),
]

SPEC_FIELDS = ["metric", "start", "end", "countries", "stock_codes", "customer_id", "group_by", "sort", "compare"]


def key(v) -> str:
    return v.date().isoformat() if isinstance(v, pd.Timestamp) else str(v)


def close(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    a, b = float(a), float(b)
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)


def spec_diff(got: ask.Spec, want: ask.Spec) -> list[str]:
    out = []
    for name in SPEC_FIELDS + (["limit"] if want.sort != "natural" else []):
        a, b = getattr(got, name), getattr(want, name)
        if name in ("countries", "stock_codes"):
            a, b = sorted(a), sorted(b)
        if a != b:
            out.append(f"{name}: got {a!r}, want {b!r}")
    return out


def answer_diff(result: ask.Result, expect: Expect) -> list[str]:
    out = []
    if expect.value is not None and not close(result.value, expect.value):
        out.append(f"value {result.value!r} != reference {expect.value!r}")
    if expect.previous is not None and not close(result.previous, expect.previous):
        out.append(f"previous {result.previous!r} != reference {expect.previous!r}")
    if expect.rows is not None or expect.contains is not None:
        table = result.table
        if table is None:
            return out + ["no table"]
        keys = [key(v) for v in table.iloc[:, 0]]
        values = table[result.spec.metric].tolist()
        if expect.rows is not None:
            want = [(key(k), v) for k, v in expect.rows]
            got = list(zip(keys, values))[: len(want)]
            if len(got) != len(want) or any(gk != wk or not close(gv, wv) for (gk, gv), (wk, wv) in zip(got, want)):
                out.append(f"rows {got[:4]}… != reference {want[:4]}…")
        for k, v in (expect.contains or {}).items():
            if k not in keys or not close(values[keys.index(k)], v):
                out.append(f"row {k!r} missing or != {v!r}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="use the expected requests instead of the model")
    parser.add_argument("--only", help="comma-separated 1-based case numbers")
    parser.add_argument("--pause", type=float, default=4.5, help="seconds between model calls (per-minute limit)")
    args = parser.parse_args()

    ask.load_env()
    api_key = os.environ.get("GEMINI_API_KEY", "")
    model = ask.model_name(os.environ.get("ASK_MODEL"))
    if not args.offline and not api_key:
        print("GEMINI_API_KEY isn't set (put it in .env). Use --offline to check without the model.")
        return 2

    df = pd.read_parquet(DATA)
    ctx = ask.build_context(df)
    picked = {int(i) for i in args.only.split(",")} if args.only else None
    passed, examples, ran = 0, [], 0
    for n, case in enumerate(CASES, 1):
        if picked and n not in picked:
            continue
        if ran and not args.offline:
            time.sleep(args.pause)
        ran += 1
        problems: list[str] = []
        if args.offline:
            raw = case.request
        else:
            try:
                raw = ask.parse_question(case.question, api_key=api_key, model=model, ctx=ctx)
            except ask.AskError as error:
                raw, problems = None, [f"model error: {error}"]
        if raw is not None:
            got = ask.resolve(raw, ctx)
            if case.request.get("answerable") is False:
                if not (isinstance(got, ask.Problem) and got.declined):
                    problems.append(f"should be declined, got {got}")
            else:
                want = ask.resolve(case.request, ctx)
                assert not isinstance(want, ask.Problem), f"case {n}: expected request doesn't resolve: {want}"
                if isinstance(got, ask.Problem):
                    problems.append(f"not answered: {got.message}")
                else:
                    problems += spec_diff(got[0], want[0])
                    if case.check is not None:
                        problems += answer_diff(ask.answer(got[0], df, first=ctx.first), case.check(df, ctx))
        ok = not problems
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {n:2}. {case.question}")
        for p in problems:
            print(f"        {p}")
        if ok and case.example:
            examples.append({"question": case.question, "request": raw})

    print(f"\n{passed}/{ran} passed ({'offline' if args.offline else model})")
    if examples and not picked:
        ask.EXAMPLES_PATH.write_text(json.dumps(examples, indent=2) + "\n")
        print(f"Wrote {len(examples)} examples to {ask.EXAMPLES_PATH}")
    return 0 if passed == ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
