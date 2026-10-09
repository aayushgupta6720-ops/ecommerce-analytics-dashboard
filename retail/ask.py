"""Ask the data: a plain-English question -> a structured request -> an answer from retail.metrics.

The language model's only job is to fill in REQUEST_SCHEMA (a measure, a period, filters and a grouping).
`resolve` checks that request against the real data and `answer` computes the result with
metrics.breakdown, which uses the same definitions as every other page. The model never produces numbers.
No Streamlit imports, so everything here is unit-testable.
"""

from __future__ import annotations

import difflib
import hmac
import json
import os
import re
import string
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from retail import metrics
from retail.countries import region

DEFAULT_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
EXAMPLES_PATH = Path(__file__).resolve().parents[1] / "data" / "processed" / "ask_examples.json"
MAX_QUESTION_CHARS = 300
MAX_LIMIT = 50
MIN_ORDERS_TO_RANK = 10  # when ranking by a ratio, smaller groups are left out as too noisy
MAX_PRODUCT_MATCHES = 1000

# measure -> (label, number format)
METRICS = {
    "revenue": ("Net revenue", "money"),
    "gross_sales": ("Gross sales", "money"),
    "orders": ("Orders", "count"),
    "customers": ("Customers", "count"),
    "aov": ("Average order value", "money"),
    "units": ("Units (net)", "count"),
    "products": ("Distinct products sold", "count"),
    "cancelled_value": ("Cancelled value", "money"),
    "cancel_rate": ("Cancellation rate", "pct"),
}
RATIO_METRICS = {"aov", "cancel_rate"}
GROUPINGS = (*metrics.GROUPINGS, "segment")
GROUP_LABELS = {"none": "no grouping", "year": "year", "quarter": "quarter", "month": "month", "day": "day",
                "weekday": "weekday", "hour": "hour of day", "country": "country", "product": "product",
                "customer": "customer", "segment": "RFM segment"}
CATEGORY_GROUPINGS = {"country", "product", "customer", "segment"}
SEGMENT_METRICS = {"customers", "revenue"}
SORTS = ("natural", "desc", "asc")
COMPARES = ("none", "previous_period", "last_year")
COMPARE_LABELS = {"none": "no comparison", "previous_period": "vs the previous period",
                  "last_year": "vs the same period a year earlier"}
# Context columns shown next to a ratio, so its parts can be checked.
CONTEXT_COLUMNS = {"aov": ["revenue", "orders"], "cancel_rate": ["gross_sales", "cancelled_value"]}

COUNTRY_ALIASES = {
    "uk": "United Kingdom", "u.k.": "United Kingdom", "britain": "United Kingdom",
    "great britain": "United Kingdom", "gb": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom",
    "us": "United States", "usa": "United States", "u.s.": "United States", "america": "United States",
    "united states of america": "United States",
    "holland": "Netherlands", "the netherlands": "Netherlands", "eire": "Ireland",
    "uae": "United Arab Emirates", "emirates": "United Arab Emirates", "korea": "South Korea",
    "rsa": "South Africa", "czechia": "Czech Republic", "jersey": "Channel Islands",
    "guernsey": "Channel Islands",
}
REGION_ALIASES = {"europe": "Europe", "asia": "Asia-Pacific", "asia-pacific": "Asia-Pacific",
                  "asia pacific": "Asia-Pacific", "apac": "Asia-Pacific", "americas": "Americas",
                  "the americas": "Americas", "middle east": "Middle East & Africa",
                  "middle east & africa": "Middle East & Africa", "middle east and africa": "Middle East & Africa"}
INTERNATIONAL = {"international", "outside the uk", "outside uk", "non-uk", "overseas", "abroad", "foreign",
                 "all except the uk", "excluding the uk"}


# ---------------------------------------------------------------- the request

@dataclass(frozen=True)
class Spec:
    """A validated request. Frozen, so it can key Streamlit's cache."""
    metric: str
    start: date
    end: date
    countries: tuple[str, ...] = ()
    product: str = ""                   # the product words as asked
    stock_codes: tuple[str, ...] = ()   # the products they matched
    product_label: str = ""
    customer_id: int | None = None
    group_by: str = "none"
    sort: str = "natural"
    limit: int = 10
    compare: str = "none"

    def as_request(self) -> dict:
        """Back to the model's request format (used by the form and the examples file)."""
        return {"answerable": True, "reason": "", "metric": self.metric, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "countries": list(self.countries), "product": self.product,
                "customer_id": "" if self.customer_id is None else str(self.customer_id),
                "group_by": self.group_by, "sort": self.sort, "limit": self.limit, "compare": self.compare}


@dataclass(frozen=True)
class Problem:
    """Why a request can't be answered, in words to show the user."""
    message: str
    suggestions: tuple[str, ...] = ()
    declined: bool = False  # the model judged the question out of scope


@dataclass
class DataContext:
    """What `resolve` checks a request against."""
    first: date
    last: date
    countries: tuple[str, ...]
    products: pd.DataFrame  # stock_code, description; best sellers first
    customers: frozenset[int]


def build_context(df: pd.DataFrame) -> DataContext:
    products = metrics.breakdown(df, "product").sort_values("revenue", ascending=False, ignore_index=True)
    ids = df["customer_id"].to_numpy(dtype="int64", na_value=-1)
    countries = metrics.breakdown(df, "country")["country"]
    return DataContext(
        first=df["invoice_date"].min().date(), last=df["invoice_date"].max().date(),
        countries=tuple(sorted(countries)), products=products[["stock_code", "description"]],
        customers=frozenset(np.unique(ids[ids >= 0]).tolist()),
    )


def _day(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10]) if value else None
    except ValueError:
        return None


def resolve(raw: dict, ctx: DataContext) -> tuple[Spec, list[str]] | Problem:
    """Check the model's request against the data. Returns (spec, notes for the user) or a Problem."""
    if not isinstance(raw, dict):
        return Problem("I couldn't read that question. Try rephrasing it.")
    if raw.get("answerable") is False:
        reason = str(raw.get("reason") or "").strip()
        return Problem(reason or "This page can't answer that question.", declined=True)
    notes: list[str] = []

    metric = raw.get("metric") or "revenue"
    if metric not in METRICS:
        return Problem(f"I don't know the measure “{metric}”.")

    start, end = _parse_date(raw.get("start")), _parse_date(raw.get("end"))
    if start and end and start > end:
        start, end = end, start
    start, end = start or ctx.first, end or ctx.last
    if end < ctx.first or start > ctx.last:
        return Problem(f"The data covers {_day(ctx.first)} – {_day(ctx.last)}, "
                       f"so there's nothing for {_day(start)} – {_day(end)}.")
    if start < ctx.first:
        notes.append(f"The data starts on {_day(ctx.first)}, so the period starts there.")
    if end > ctx.last:
        notes.append(f"The data ends on {_day(ctx.last)}, so the period ends there.")
    start, end = max(start, ctx.first), min(end, ctx.last)

    countries = _countries(raw.get("countries") or [], ctx, notes)
    if isinstance(countries, Problem):
        return countries

    term = str(raw.get("product") or "").strip()
    stock_codes: tuple[str, ...] = ()
    product_label = ""
    if term:
        matched = match_products(term, ctx.products)
        if matched.empty:
            close = difflib.get_close_matches(term.upper(), ctx.products["description"].tolist(), n=5, cutoff=0.5)
            return Problem(f"No product description matches “{term}”.",
                           suggestions=tuple(string.capwords(c) for c in close))
        if len(matched) > MAX_PRODUCT_MATCHES:
            return Problem(f"“{term}” matches {len(matched):,} products. Try more specific words.")
        stock_codes = tuple(sorted(matched["stock_code"]))
        if len(matched) == 1:
            product_label = f"{string.capwords(matched['description'].iat[0])} ({matched['stock_code'].iat[0]})"
        else:
            product_label = f"{len(matched)} products matching “{term}”"
            examples = ", ".join(string.capwords(d) for d in matched["description"].head(3))
            notes.append(f"“{term}” matches {len(matched)} products, e.g. {examples}. They're counted together.")

    customer_id = None
    raw_customer = str(raw.get("customer_id") or "").strip()
    if raw_customer and raw_customer != "0":
        try:
            customer_id = int(float(raw_customer))
        except ValueError:
            return Problem(f"“{raw_customer}” isn't a customer number.")
        if customer_id not in ctx.customers:
            return Problem(f"There's no customer {customer_id} in the data.")

    group_by = raw.get("group_by") or "none"
    if group_by not in GROUPINGS:
        return Problem(f"I can't group by “{group_by}”.")
    if group_by == "segment":
        if metric not in SEGMENT_METRICS:
            return Problem("RFM segments can be broken down by customers or net revenue only.")
        if stock_codes or customer_id is not None:
            return Problem("RFM segments can't be combined with a product or customer filter.")

    sort = raw.get("sort") if raw.get("sort") in SORTS else "natural"
    if group_by in CATEGORY_GROUPINGS and sort == "natural":
        sort = "desc"
    try:
        limit = min(max(int(raw.get("limit") or 10), 1), MAX_LIMIT)
    except (TypeError, ValueError):
        limit = 10
    compare = raw.get("compare") if raw.get("compare") in COMPARES else "none"
    if compare != "none" and group_by != "none":
        notes.append("Comparisons are shown for single figures only, so it's left out here.")
        compare = "none"

    return Spec(metric=metric, start=start, end=end, countries=countries, product=term, stock_codes=stock_codes,
                product_label=product_label, customer_id=customer_id, group_by=group_by, sort=sort, limit=limit,
                compare=compare), notes


def _countries(names: list, ctx: DataContext, notes: list[str]) -> tuple[str, ...] | Problem:
    known = {c.lower(): c for c in ctx.countries}
    out: list[str] = []
    for name in names:
        key = str(name).strip().lower()
        if not key:
            continue
        if key in known:
            out.append(known[key])
        elif COUNTRY_ALIASES.get(key) in ctx.countries:
            out.append(COUNTRY_ALIASES[key])
        elif key in INTERNATIONAL:
            members = [c for c in ctx.countries if c != "United Kingdom"]
            out.extend(members)
            notes.append(f"Outside the UK means {len(members)} countries here.")
        elif key in REGION_ALIASES:
            members = [c for c in ctx.countries if region(c) == REGION_ALIASES[key]]
            out.extend(members)
            uk = " (the UK is counted on its own)" if REGION_ALIASES[key] == "Europe" else ""
            notes.append(f"{REGION_ALIASES[key]} means {len(members)} countries here{uk}.")
        else:
            close = difflib.get_close_matches(key, list(known), n=3, cutoff=0.75)
            if not close:
                loose = difflib.get_close_matches(key, list(known), n=3, cutoff=0.4)
                return Problem(f"There's no country called “{name}” in the data.",
                               suggestions=tuple(known[c] for c in loose))
            out.append(known[close[0]])
            notes.append(f"Read “{name}” as {known[close[0]]}.")
    return tuple(dict.fromkeys(out))


def match_products(term: str, products: pd.DataFrame) -> pd.DataFrame:
    """Products whose stock code equals `term`, or whose description has all its words.

    Words also match without a plural ending ("stands" finds "STAND"), and the words run together
    match descriptions written without spaces ("cake stand" finds "CAKESTAND").
    """
    exact = products[products["stock_code"].str.upper() == term.strip().upper()]
    if not exact.empty:
        return exact
    words = re.findall(r"[A-Z0-9]+", term.upper())
    if not words:
        return products.iloc[0:0]
    desc = products["description"].str.upper()
    hit = np.ones(len(products), dtype=bool)
    for word in words:
        forms = {word} | ({word[:-1]} if len(word) > 3 and word.endswith("S") else set()) | (
            {word[:-2]} if len(word) > 4 and word.endswith("ES") else set())
        hit &= np.logical_or.reduce([desc.str.contains(w, regex=False).to_numpy() for w in forms])
    compact = desc.str.replace(r"[^A-Z0-9]", "", regex=True)
    hit |= compact.str.contains("".join(words), regex=False).to_numpy()
    return products[hit]


def describe(spec: Spec, ctx: DataContext | None = None) -> str:
    """The request in one line of plain words, written by code (not the model)."""
    parts = [METRICS[spec.metric][0]]
    if spec.group_by != "none":
        parts.append(f"by {GROUP_LABELS[spec.group_by]}")
    if ctx is not None and (spec.start, spec.end) == (ctx.first, ctx.last):
        parts.append(f"all data ({_day(spec.start)} – {_day(spec.end)})")
    else:
        parts.append(f"{_day(spec.start)} – {_day(spec.end)}")
    if not spec.countries:
        parts.append("all countries")
    else:
        parts.append(", ".join(spec.countries) if len(spec.countries) <= 3 else f"{len(spec.countries)} countries")
    if spec.product_label:
        parts.append(spec.product_label)
    if spec.customer_id is not None:
        parts.append(f"customer {spec.customer_id}")
    if spec.sort != "natural" and spec.group_by != "segment":  # all segments are always shown
        parts.append(f"{'top' if spec.sort == 'desc' else 'bottom'} {spec.limit}, "
                     f"{'highest' if spec.sort == 'desc' else 'lowest'} first")
    if spec.compare != "none":
        parts.append(COMPARE_LABELS[spec.compare])
    return " · ".join(parts)


# ---------------------------------------------------------------- the answer

@dataclass
class Result:
    spec: Spec
    value: float | None = None                  # a single figure (no grouping)
    previous: float | None = None               # the comparison figure
    previous_period: tuple[date, date] | None = None
    change: float | None = None                 # relative change (percentage points for cancel_rate)
    table: pd.DataFrame | None = None           # grouped answer: key columns, the measure, context columns
    notes: list[str] = field(default_factory=list)


def row_mask(df: pd.DataFrame, spec: Spec) -> np.ndarray:
    mask = metrics.filter_mask(df, spec.start, spec.end, spec.countries)
    if spec.stock_codes:
        mask &= df["stock_code"].isin(spec.stock_codes).to_numpy()
    if spec.customer_id is not None:
        mask &= df["customer_id"].eq(spec.customer_id).to_numpy(dtype=bool, na_value=False)
    return mask


def _single(df: pd.DataFrame, spec: Spec) -> float | None:
    row = metrics.breakdown(df, "none", row_mask(df, spec))
    if row.empty:
        return None if spec.metric in RATIO_METRICS else 0.0
    value = row[spec.metric].iat[0]
    return None if pd.isna(value) else float(value)


def comparison_period(spec: Spec) -> tuple[date, date]:
    if spec.compare == "previous_period":
        return metrics.previous_period(spec.start, spec.end)
    year = pd.DateOffset(years=1)
    return (pd.Timestamp(spec.start) - year).date(), (pd.Timestamp(spec.end) - year).date()


def answer(spec: Spec, df: pd.DataFrame, first: date | None = None,
           rfm_table: pd.DataFrame | None = None) -> Result:
    """Compute the answer. `rfm_table` (metrics.rfm for the spec's filters) is only used for segments."""
    result = Result(spec)
    if spec.group_by == "none":
        result.value = _single(df, spec)
        if spec.compare != "none":
            prev_start, prev_end = comparison_period(spec)
            first = first or df["invoice_date"].min().date()
            if prev_start < first:
                result.notes.append(f"No comparison: the earlier period would start before the data "
                                    f"does ({_day(first)}).")
            else:
                result.previous_period = (prev_start, prev_end)
                result.previous = _single(df, replace(spec, start=prev_start, end=prev_end))
                if result.value is not None and result.previous is not None:
                    result.change = metrics.kpi_deltas({spec.metric: result.value},
                                                       {spec.metric: result.previous})[spec.metric]
        return result

    if spec.group_by == "segment":
        if rfm_table is None:
            rfm_table = metrics.rfm(metrics.filter_frame(df, spec.start, spec.end, spec.countries),
                                    snapshot=spec.end + timedelta(days=1))
        summary = metrics.segment_summary(rfm_table)
        share = "customer_share" if spec.metric == "customers" else "revenue_share"
        table = summary[["segment", spec.metric, share]]
        key_cols = ["segment"]
    else:
        full = metrics.breakdown(df, spec.group_by, row_mask(df, spec), spec.start, spec.end)
        key_cols = list(full.columns[: full.columns.get_loc("revenue")])
        extra = [c for c in CONTEXT_COLUMNS.get(spec.metric, []) if c != spec.metric]
        table = full[key_cols + [spec.metric] + extra + (["partial"] if "partial" in full else [])]
        if spec.metric in RATIO_METRICS and spec.sort != "natural":
            small = (full["orders"] < MIN_ORDERS_TO_RANK).to_numpy()
            if small.any():
                result.notes.append(f"Left out {int(small.sum())} {GROUP_LABELS[spec.group_by]} groups with "
                                    f"fewer than {MIN_ORDERS_TO_RANK} orders: their ratios are too noisy to rank.")
                table = table[~small]
    if spec.sort != "natural":
        table = table[table[spec.metric].notna()]
        table = table.sort_values([spec.metric, key_cols[0]], ascending=[spec.sort == "asc", True], kind="stable")
        if spec.group_by != "segment":
            table = table.head(spec.limit)
    result.table = table.reset_index(drop=True)
    return result


def group_labels(table: pd.DataFrame, group_by: str) -> pd.Series:
    """Readable labels for a result table's groups."""
    if group_by in ("year", "quarter", "month", "day"):
        ts = table[group_by]
        return {"year": ts.dt.strftime("%Y"),
                "quarter": ts.dt.year.astype(str) + " Q" + ts.dt.quarter.astype(str),
                "month": ts.dt.strftime("%b %Y"), "day": ts.dt.strftime("%d %b %Y")}[group_by]
    if group_by == "hour":
        return table["hour"].map(lambda h: f"{h:02d}:00")
    if group_by == "product":
        return table["description"].map(string.capwords) + " (" + table["stock_code"] + ")"
    if group_by == "customer":
        return "Customer " + table["customer_id"].astype(str)
    return table[{"weekday": "weekday", "country": "country", "segment": "segment"}[group_by]].astype(str)


def format_value(metric: str, value: float | None) -> str:
    if value is None or pd.isna(value):
        return "–"
    kind = METRICS[metric][1]
    return f"£{value:,.2f}" if kind == "money" else f"{value:.2%}" if kind == "pct" else f"{value:,.0f}"


# ---------------------------------------------------------------- the language model

REQUEST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "answerable": {"type": "BOOLEAN"},
        "reason": {"type": "STRING", "description": "Only when not answerable: one short sentence on why, "
                                                    "naming the dashboard page that could help."},
        "metric": {"type": "STRING", "enum": list(METRICS)},
        "start": {"type": "STRING", "description": "First day, YYYY-MM-DD; empty for the start of the data."},
        "end": {"type": "STRING", "description": "Last day, YYYY-MM-DD; empty for the end of the data."},
        "countries": {"type": "ARRAY", "items": {"type": "STRING"}},
        "product": {"type": "STRING", "description": "Product words or a stock code; empty if none."},
        "customer_id": {"type": "STRING", "description": "Customer number; empty if none."},
        "group_by": {"type": "STRING", "enum": list(GROUPINGS)},
        "sort": {"type": "STRING", "enum": list(SORTS)},
        "limit": {"type": "INTEGER"},
        "compare": {"type": "STRING", "enum": list(COMPARES)},
    },
    "required": ["answerable", "reason", "metric", "start", "end", "countries", "product", "customer_id",
                 "group_by", "sort", "limit", "compare"],
    "propertyOrdering": ["answerable", "reason", "metric", "start", "end", "countries", "product", "customer_id",
                         "group_by", "sort", "limit", "compare"],
}

_EXAMPLES = [
    ("What was net revenue in France in 2011?",
     dict(metric="revenue", start="2011-01-01", end="2011-12-31", countries=["France"])),
    ("top 5 products by units sold in November 2010",
     dict(metric="units", start="2010-11-01", end="2010-11-30", group_by="product", sort="desc", limit=5)),
    ("monthly orders from Germany and Austria last year",
     dict(metric="orders", start="2010-01-01", end="2010-12-31", countries=["Germany", "Austria"],
          group_by="month")),
    ("How did revenue over the last 3 months compare with a year earlier?",
     dict(metric="revenue", start="2011-09-10", end="2011-12-09", compare="last_year")),
    ("which customers spent the most on cake stands?",
     dict(metric="revenue", product="cake stand", group_by="customer", sort="desc")),
    ("Will sales grow next year?",
     dict(answerable=False, reason="That needs a forecast; the Predictions page has the revenue forecast.")),
]


def _example_request(fields: dict) -> str:
    request = {"answerable": True, "reason": "", "metric": "revenue", "start": "", "end": "", "countries": [],
               "product": "", "customer_id": "", "group_by": "none", "sort": "natural", "limit": 10,
               "compare": "none"}
    return json.dumps(request | fields)


def system_prompt(ctx: DataContext) -> str:
    examples = "\n".join(f"Q: {q}\n{_example_request(f)}" for q, f in _EXAMPLES)
    return f"""You turn questions about one online shop's sales data into a JSON request. Never answer with \
numbers yourself: the dashboard computes the answer from your request.

The data: invoice lines of a UK online gift retailer from {ctx.first} to {ctx.last}. Treat {ctx.last} (the last \
day of data) as "today". Money is in GBP.

Fields:
- metric: revenue = net revenue (sales minus cancellations; use for "revenue", "sales", "turnover", "spent", \
"made"); gross_sales = sales before cancellations; orders = distinct orders (invoices); customers = distinct \
identified customers; aov = average order value; units = units/items/quantity sold, net of cancellations; \
products = number of distinct products sold; cancelled_value = value of cancellations/returns; cancel_rate = \
cancelled value as a share of gross sales.
- start, end: the period, inclusive, YYYY-MM-DD. Leave both empty when no period is mentioned. A year runs \
1 Jan - 31 Dec, a month first to last day, Q1 = Jan-Mar, Q2 = Apr-Jun, Q3 = Jul-Sep, Q4 = Oct-Dec. Relative \
phrases count back from {ctx.last}: "this year" = {ctx.last.year}-01-01 to {ctx.last}; "last year" = the \
calendar year {ctx.last.year - 1}; "last N months" = N months ending on {ctx.last}; "last N days" likewise.
- countries: names exactly as in this list, or a region (Europe, Asia-Pacific, Americas, Middle East & Africa), \
or "International" for every country except the UK ("outside the UK", "excluding the UK", "overseas"). Europe \
doesn't include the UK. Empty means all countries. {", ".join(ctx.countries)}.
- product: the product words from the question (e.g. "cake stand", "red lunch bag") or a stock code. Empty if \
no particular product is named.
- customer_id: a customer number if one is named, else empty.
- group_by: none for a single figure; year, quarter, month, day, weekday or hour for trends and for "which \
month/day/hour"; country, product or customer for rankings and lists; segment for RFM customer segments \
(Champions, Loyal Customers, At Risk...; only with customers or revenue).
- sort: desc for top/best/most/highest; asc for bottom/worst/least/lowest; natural otherwise.
- limit: rows for top/bottom questions; default 10, max 50; 1 for "which month was best" and similar.
- compare: previous_period for "vs the previous period"; last_year for "vs last year", "year on year", "same \
period last year"; otherwise none. Only for single figures (group_by none).
- answerable: false when the question isn't about this shop's sales figures, or needs something these fields \
can't express: forecasts or predictions, customer lifetime value or churn (Predictions page), products bought \
together (Market basket page), cohort retention (Cohort retention page), recommendations (Insights & actions \
page). Then give a one-sentence reason naming the page, and defaults elsewhere (metric revenue, group_by none, \
sort natural, limit 10, compare none, empty strings and lists).

Examples:
{examples}"""


class AskError(Exception):
    """A failure to show the user as is. Messages never contain the API key."""


Post = Callable[[str, dict, str, float], tuple[int, dict]]


def _post(url: str, body: dict, api_key: str, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": api_key})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except ValueError:
            return error.code, {}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, {}


def model_name(value: str | None) -> str:
    """ASK_MODEL if it looks like a model name (it goes into the URL), else the default."""
    return value if value and re.fullmatch(r"[A-Za-z0-9.\-]+", value) else DEFAULT_MODEL


def parse_question(question: str, *, api_key: str, model: str, ctx: DataContext, post: Post | None = None,
                   timeout: float = 20.0) -> dict:
    """One Gemini call: the question -> a request dict (not yet validated; see `resolve`)."""
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt(ctx)}]},
        "contents": [{"role": "user", "parts": [{"text": question.strip()[:MAX_QUESTION_CHARS]}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                             "responseSchema": REQUEST_SCHEMA},
    }
    url = GEMINI_URL.format(model=model_name(model))
    send = post or _post
    for attempt in (1, 2):
        status, payload = send(url, body, api_key, timeout)
        if status == 200:
            break
        if status == 429:
            raise AskError("Today's question quota is used up. Try again tomorrow, or use the examples and "
                           "the form below.")
        if attempt == 1 and status in (0, 500, 502, 503, 504):
            continue  # one quick retry for a dropped connection or a busy server
        if status == 0:
            raise AskError("Couldn't reach the language model. Try again in a minute.")
        raise AskError(f"The language model returned an error ({status}). Try again, or use the form below.")
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        raw = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
    except (KeyError, IndexError, TypeError, ValueError):
        raise AskError("The language model didn't return a usable request. Try rephrasing the question.") from None
    if not isinstance(raw, dict):
        raise AskError("The language model didn't return a usable request. Try rephrasing the question.")
    return raw


# ---------------------------------------------------------------- access and quota

def load_env(path: Path = ENV_PATH) -> None:
    """Read KEY=VALUE lines from a local .env (gitignored), never overriding variables already set."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("\"'"))


def password_ok(given: str, expected: str) -> bool:
    """Constant-time comparison; an unset password never matches."""
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


class DailyCounter:
    """Questions asked today across every session. Kept in memory, so it resets when the server restarts."""

    def __init__(self, cap: int):
        self.cap = cap
        self.day: date | None = None
        self.count = 0
        self._lock = threading.Lock()

    def take(self, today: date | None = None) -> bool:
        today = today or date.today()
        with self._lock:
            if today != self.day:
                self.day, self.count = today, 0
            if self.count >= self.cap:
                return False
            self.count += 1
            return True


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict]:
    """Saved example questions with their requests (written by scripts/eval_ask.py)."""
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return []
