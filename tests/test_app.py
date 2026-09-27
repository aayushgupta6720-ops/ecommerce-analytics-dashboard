"""Smoke tests: every page renders without an exception, with default and narrow filters."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from retail.data import Filters

APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["views/overview.py", "views/products.py", "views/geography.py", "views/customers.py",
         "views/cohorts.py", "views/basket.py", "views/returns.py"]


def run_page(page: str, filters: Filters | None = None) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if filters is not None:
        # Narrow the global filters through the sidebar widgets, like a user would.
        at.selectbox(key="period").set_value("Custom range").run()
        at.date_input(key="custom_range").set_value((filters.start, filters.end)).run()
        at.multiselect(key="countries").set_value(list(filters.countries)).run()
    at.switch_page(page).run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_defaults(page):
    at = run_page(page)
    assert not at.exception, at.exception


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_narrow_filters(page):
    # One small market over one quarter: few orders, few customers, maybe no rules or cohorts.
    at = run_page(page, Filters(date(2011, 4, 1), date(2011, 6, 30), ("Portugal",)))
    assert not at.exception, at.exception


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_empty_selection(page):
    # A single day on a Saturday (the shop takes ~no orders): pages must show an empty state, not crash.
    at = run_page(page, Filters(date(2011, 3, 5), date(2011, 3, 5), ("Iceland",)))
    assert not at.exception, at.exception


def test_exclude_uk_toggle():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.toggle(key="exclude_uk").set_value(True).run()
    assert not at.exception
    assert "United Kingdom" not in at.session_state["filters"].countries
