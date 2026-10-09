"""Smoke tests: every page renders without an exception, with default and narrow filters."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from retail.data import Filters

APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["views/overview.py", "views/products.py", "views/geography.py", "views/customers.py",
         "views/cohorts.py", "views/basket.py", "views/returns.py", "views/predictions.py", "views/sql.py",
         "views/insights.py", "views/ask.py"]


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


# ---------------------------------------------------------------- Ask the data

def open_ask(monkeypatch, key: str = "", password: str = "") -> AppTest:
    # Set explicitly, so a developer's local .env (which never overrides set variables) can't leak in.
    monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.setenv("ASK_PASSWORD", password)
    return run_page("views/ask.py")


def page_text(at: AppTest) -> str:
    return " ".join(str(e.value) for e in [*at.markdown, *at.caption, *at.info, *at.warning, *at.error])


def test_ask_without_a_key_offers_examples_and_the_form(monkeypatch):
    at = open_ask(monkeypatch)
    assert not at.exception, at.exception
    assert "switched off" in page_text(at)
    assert "How I read it" in page_text(at)  # the first example is answered on arrival
    # Click another example.
    question = "Which 3 products sold the most units in November 2010?"
    at.button_group(key="ask_example").set_value(question).run()
    assert not at.exception, at.exception
    assert "Units (net) · by product · 1 Nov 2010 – 30 Nov 2010" in page_text(at)
    # Build a request with the form instead: orders by country in France and Germany.
    at.selectbox(key="adj_metric").set_value("orders")
    at.selectbox(key="adj_group").set_value("country")
    at.multiselect(key="adj_countries").set_value(["France", "Germany"])
    at.button(key="adj_apply").click().run()
    assert not at.exception, at.exception
    assert "Orders · by country" in page_text(at)
    assert at.session_state["ask_example"] is None


def test_ask_password_gate_and_a_stubbed_question(monkeypatch):
    calls = []

    def fake_parse(question, **kwargs):
        calls.append(question)
        return {"answerable": True, "metric": "units", "start": "2011-01-01", "end": "2011-03-31",
                "countries": ["Germany"], "group_by": "month"}

    monkeypatch.setattr("retail.ask.parse_question", fake_parse)
    at = open_ask(monkeypatch, key="test-key", password="pw")
    assert "password-protected" in page_text(at)
    at.text_input(key="ask_password").input("wrong")
    at.button(key="ask_unlock_button").click().run()
    assert "Wrong password" in page_text(at) and not calls
    at.text_input(key="ask_password").input("pw")
    at.button(key="ask_unlock_button").click().run()
    assert at.session_state["ask_unlocked"]
    at.text_input(key="ask_question").input("units in Germany by month, Q1 2011")
    at.button(key="ask_button").click().run()
    assert not at.exception, at.exception
    assert calls == ["units in Germany by month, Q1 2011"]
    text = page_text(at)
    assert "Units (net) · by month · 1 Jan 2011 – 31 Mar 2011 · Germany" in text
    assert at.selectbox(key="adj_metric").value == "units"  # the Adjust form shows the reading


def test_ask_declined_question_is_explained(monkeypatch):
    monkeypatch.setattr("retail.ask.parse_question",
                        lambda q, **kw: {"answerable": False, "reason": "That needs a forecast."})
    at = open_ask(monkeypatch, key="test-key", password="pw")
    at.text_input(key="ask_password").input("pw")
    at.button(key="ask_unlock_button").click().run()
    at.text_input(key="ask_question").input("forecast 2012")
    at.button(key="ask_button").click().run()
    assert not at.exception, at.exception
    assert "That needs a forecast." in page_text(at)
