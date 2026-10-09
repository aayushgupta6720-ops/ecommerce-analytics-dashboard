"""Streamlit data layer: one shared copy of the transactions, cached aggregates keyed by filter values.

Cached functions take a `Filters` tuple (dates + country names), never a DataFrame, so
Streamlit hashes a few small values instead of a 1M-row frame on every rerun.
"""

from __future__ import annotations

import ctypes
import gc
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import pyarrow as pa
import pyarrow.feather as feather
import streamlit as st

from retail import metrics

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "processed" / "transactions.parquet"
# Uncompressed Arrow copy made at build time (scripts/build_dataset.py --feather-only). Memory-mapping
# it costs about half the RAM of decoding the zstd Parquet, which matters on a 512MB instance.
FEATHER_PATH = DATA_PATH.with_suffix(".feather")

# Arrow's default pool (mimalloc/jemalloc) keeps freed memory reserved; the system allocator lets
# _release_freed_memory() hand it back, which matters on the 512MB Render instance.
pa.set_memory_pool(pa.system_memory_pool())


class Filters(NamedTuple):
    start: date
    end: date
    countries: tuple[str, ...] = ()  # empty = all countries

    def describe(self) -> str:
        where = "All countries" if not self.countries else (
            ", ".join(self.countries) if len(self.countries) <= 3 else f"{len(self.countries)} countries")
        return f"{self.start:%d %b %Y} – {self.end:%d %b %Y} · {where}"


@st.cache_resource(show_spinner="Loading 1M transactions…")
def load_data() -> pd.DataFrame:
    """Shared across sessions and treated as read-only."""
    if FEATHER_PATH.exists():
        df = feather.read_table(FEATHER_PATH, memory_map=True).to_pandas(self_destruct=True)
    else:
        df = pd.read_parquet(DATA_PATH)
    _release_freed_memory()
    return df


def _release_freed_memory() -> None:
    """Hand freed memory back to the OS; glibc otherwise keeps it as a high-water mark."""
    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except OSError:
            pass


@st.cache_data(show_spinner=False)
def bounds() -> tuple[date, date]:
    ts = load_data()["invoice_date"]
    return ts.min().date(), ts.max().date()


@st.cache_data(show_spinner=False)
def countries_by_revenue() -> list[str]:
    return metrics.country_summary(load_data())["country"].tolist()


def filtered(f: Filters) -> pd.DataFrame:
    return metrics.filter_frame(load_data(), f.start, f.end, f.countries)


@st.cache_data(max_entries=256, show_spinner=False)
def compute(metric: str, f: Filters, **params):
    """Run `retail.metrics.<metric>` on the filtered frame; results are cached per filter + params."""
    result = getattr(metrics, metric)(filtered(f), **params)
    _release_freed_memory()  # only runs on a cache miss, after the big temporaries are freed
    return result


@st.cache_data(show_spinner=False)
def acquisition_months() -> pd.Series:
    return metrics.first_purchase_month(load_data())


@st.cache_data(max_entries=64, show_spinner=False)
def cohorts(f: Filters) -> pd.DataFrame:
    result = metrics.cohort_table(filtered(f), acquisition_months())
    _release_freed_memory()
    return result


def rfm(f: Filters) -> pd.DataFrame:
    return compute("rfm", f, snapshot=f.end + timedelta(days=1))


MODELS_DIR = DATA_PATH.parent / "models"  # written by scripts/train_models.py


@st.cache_data(show_spinner=False)
def model_metrics() -> dict:
    return json.loads((MODELS_DIR / "metrics.json").read_text())


@st.cache_data(show_spinner=False)
def model_table(name: str) -> pd.DataFrame:
    return pd.read_parquet(MODELS_DIR / f"{name}.parquet")


@st.cache_data(show_spinner="Working out the recommendations…")
def insights_bundle() -> dict:
    """Everything the Insights page needs, computed once on the full dataset."""
    from retail import insights
    df = load_data()
    snapshot = bounds()[1] + timedelta(days=1)
    current = model_table("churn_current")
    out = {
        "snapshot": snapshot,
        "years": insights.data_years(df),
        "winback": insights.winback(df, snapshot),
        "champions": insights.champions(df, snapshot, current),
        "champion_share": insights.segment_share(df, snapshot, "Champions"),
        "peak": insights.peak_season(df),
        "guests": insights.guests(df),
        "bundles": insights.bundle_opportunities(df),
    }
    _release_freed_memory()
    return out


@st.cache_data(show_spinner=False)
def bulk_cancellations(threshold: int) -> dict:
    from retail import insights
    return insights.bulk_cancellations(load_data(), threshold)


@st.cache_resource(show_spinner=False)
def duck():
    """One DuckDB connection per process; each query uses its own cursor (safe across session threads)."""
    from retail import sql
    return sql.connect(DATA_PATH)


@st.cache_data(max_entries=64, show_spinner=False)
def sql_query(name: str, f: Filters) -> tuple[pd.DataFrame, float]:
    """Run sql/<name>.sql for these filters; returns the result and how long DuckDB took (ms)."""
    from retail import sql
    started = time.perf_counter()
    result = sql.run(duck().cursor(), name, f.start, f.end, f.countries)
    elapsed = (time.perf_counter() - started) * 1000
    _release_freed_memory()
    return result, elapsed


@st.cache_data(show_spinner=False)
def ask_context():
    """Countries, products and customers that Ask-the-data requests are checked against."""
    from retail import ask
    ctx = ask.build_context(load_data())
    _release_freed_memory()
    return ctx


@st.cache_resource(show_spinner=False)
def ask_counter(cap: int):
    """Site-wide count of today's free-text questions (shared by every session)."""
    from retail import ask
    return ask.DailyCounter(cap)


@st.cache_data(max_entries=128, show_spinner=False)
def ask_answer(spec):
    """The answer to a validated Ask-the-data request (retail.ask.Spec)."""
    from retail import ask
    rfm_table = rfm(Filters(spec.start, spec.end, spec.countries)) if spec.group_by == "segment" else None
    result = ask.answer(spec, load_data(), first=bounds()[0], rfm_table=rfm_table)
    _release_freed_memory()
    return result


def kpis_with_deltas(f: Filters) -> tuple[dict, dict, Filters | None]:
    """Current KPIs, deltas vs the previous period of equal length (None if it predates the data)."""
    current = compute("kpis", f)
    prev_start, prev_end = metrics.previous_period(f.start, f.end)
    if prev_start < bounds()[0]:
        return current, metrics.kpi_deltas(current, None), None
    prev = Filters(prev_start, prev_end, f.countries)
    return current, metrics.kpi_deltas(current, compute("kpis", prev)), prev


def current_filters() -> Filters:
    """Set by app.py's sidebar before the page runs."""
    if "filters" not in st.session_state:
        start, end = bounds()
        st.session_state["filters"] = Filters(start, end)
    return st.session_state["filters"]


def page_header(title: str, blurb: str) -> Filters:
    f = current_filters()
    st.header(title)
    st.caption(f"{blurb}  \n**Showing:** {f.describe()}")
    return f


def empty_state(df: pd.DataFrame | dict, what: str = "data") -> bool:
    """Show a friendly message and return True when the filters leave nothing to plot."""
    is_empty = (df.get("orders", 0) == 0) if isinstance(df, dict) else len(df) == 0
    if is_empty:
        st.info(f"No {what} for these filters. Widen the date range or add countries in the sidebar.")
    return is_empty
