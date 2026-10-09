"""Ask the data: request validation, answers, the Gemini call (stubbed; no network), access and quota."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from retail import ask
from retail import metrics as m


@pytest.fixture
def ctx(tiny) -> ask.DataContext:
    return ask.build_context(tiny)


def resolved(raw: dict, ctx) -> tuple[ask.Spec, list[str]]:
    out = ask.resolve(raw, ctx)
    assert not isinstance(out, ask.Problem), out
    return out


# ---------------------------------------------------------------- resolve

def test_context(ctx):
    assert (ctx.first, ctx.last) == (date(2010, 1, 4), date(2010, 2, 16))
    assert ctx.countries == ("France", "Germany", "United Kingdom")
    assert ctx.customers == {1, 2}
    assert ctx.products["stock_code"].iat[0] == "10002"  # best seller first (20 + 40 - 20 = 40)


def test_defaults_to_all_data(ctx):
    spec, notes = resolved({"metric": "orders"}, ctx)
    assert (spec.start, spec.end, spec.countries, spec.group_by, spec.sort) == (
        ctx.first, ctx.last, (), "none", "natural")
    assert notes == []


def test_period_is_clamped_with_a_note_and_reversed_dates_are_swapped(ctx):
    spec, notes = resolved({"start": "2010-12-31", "end": "2010-01-01"}, ctx)
    assert (spec.start, spec.end) == (ctx.first, ctx.last)
    assert len(notes) == 2 and "starts on 4 Jan 2010" in notes[0] and "ends on 16 Feb 2010" in notes[1]


def test_period_outside_the_data_is_a_problem(ctx):
    problem = ask.resolve({"start": "2012-01-01", "end": "2012-03-31"}, ctx)
    assert isinstance(problem, ask.Problem) and "covers 4 Jan 2010 – 16 Feb 2010" in problem.message


def test_countries_aliases_regions_and_near_misses(ctx):
    spec, _ = resolved({"countries": ["uk", "France", "france"]}, ctx)
    assert spec.countries == ("United Kingdom", "France")
    spec, notes = resolved({"countries": ["Frence"]}, ctx)
    assert spec.countries == ("France",) and notes == ["Read “Frence” as France."]
    spec, notes = resolved({"countries": ["Europe"]}, ctx)
    assert spec.countries == ("France", "Germany") and "UK is counted on its own" in notes[0]
    spec, _ = resolved({"countries": ["outside the UK"]}, ctx)
    assert spec.countries == ("France", "Germany")
    problem = ask.resolve({"countries": ["Narnia"]}, ctx)
    assert isinstance(problem, ask.Problem) and "Narnia" in problem.message


CATALOGUE = pd.DataFrame({
    "stock_code": ["22423", "21843", "20725", "85099B", "47566"],
    "description": ["REGENCY CAKESTAND 3 TIER", "RED RETROSPOT CAKE STAND", "LUNCH BAG RED RETROSPOT",
                    "JUMBO BAG RED RETROSPOT", "PARTY BUNTING"],
})


@pytest.mark.parametrize("term, codes", [
    ("cake stands", {"22423", "21843"}),   # plural; each word may sit inside another ("CAKESTAND")
    ("cakestand", {"22423", "21843"}),     # run together: also finds "CAKE STAND"
    ("cake stand", {"22423", "21843"}),
    ("red bag", {"20725", "85099B"}),      # all words, any order
    ("lunch bags", {"20725"}),
    ("85099b", {"85099B"}),                # a stock code
    ("unicorn", set()),
])
def test_match_products(term, codes):
    assert set(ask.match_products(term, CATALOGUE)["stock_code"]) == codes


def test_product_filter(ctx):
    spec, notes = resolved({"product": "item 10003"}, ctx)
    assert spec.stock_codes == ("10003",) and spec.product_label == "Item 10003 (10003)" and notes == []
    spec, notes = resolved({"product": "item"}, ctx)
    assert len(spec.stock_codes) == 3 and "matches 3 products" in notes[0]
    problem = ask.resolve({"product": "item 10004"}, ctx)
    assert isinstance(problem, ask.Problem) and problem.suggestions  # close descriptions are suggested


def test_customer_filter(ctx):
    assert resolved({"customer_id": "2"}, ctx)[0].customer_id == 2
    assert resolved({"customer_id": "1.0"}, ctx)[0].customer_id == 1
    assert resolved({"customer_id": ""}, ctx)[0].customer_id is None
    assert "no customer 99" in ask.resolve({"customer_id": "99"}, ctx).message
    assert isinstance(ask.resolve({"customer_id": "abc"}, ctx), ask.Problem)


def test_groupings_sorting_limit_and_compare(ctx):
    spec, _ = resolved({"group_by": "country", "limit": 500}, ctx)
    assert (spec.sort, spec.limit) == ("desc", ask.MAX_LIMIT)  # rankings list the highest first by default
    spec, _ = resolved({"group_by": "month", "sort": "sideways", "limit": "x"}, ctx)
    assert (spec.sort, spec.limit) == ("natural", 10)
    spec, notes = resolved({"group_by": "month", "compare": "last_year"}, ctx)
    assert spec.compare == "none" and "single figures only" in notes[0]
    assert isinstance(ask.resolve({"group_by": "colour"}, ctx), ask.Problem)
    assert isinstance(ask.resolve({"metric": "profit"}, ctx), ask.Problem)


def test_segment_rules(ctx):
    assert resolved({"group_by": "segment", "metric": "customers"}, ctx)[0].group_by == "segment"
    assert "customers or net revenue" in ask.resolve({"group_by": "segment", "metric": "orders"}, ctx).message
    assert isinstance(ask.resolve({"group_by": "segment", "product": "item"}, ctx), ask.Problem)


def test_declined_and_unreadable(ctx):
    problem = ask.resolve({"answerable": False, "reason": "That needs a forecast."}, ctx)
    assert problem.declined and problem.message == "That needs a forecast."
    assert ask.resolve({"answerable": False}, ctx).message
    assert isinstance(ask.resolve(["not", "a", "dict"], ctx), ask.Problem)


def test_request_round_trip(ctx):
    spec, _ = resolved({"metric": "aov", "start": "2010-01-10", "countries": ["France"], "product": "item",
                        "group_by": "product", "sort": "asc", "limit": 3}, ctx)
    assert resolved(spec.as_request(), ctx)[0] == spec


def test_describe(ctx):
    spec, _ = resolved({"metric": "units", "start": "2010-02-01", "end": "2010-02-16", "countries": ["France"],
                        "group_by": "product", "limit": 5}, ctx)
    assert ask.describe(spec, ctx) == ("Units (net) · by product · 1 Feb 2010 – 16 Feb 2010 · France · "
                                       "top 5, highest first")
    spec, _ = resolved({"compare": "previous_period", "customer_id": "1"}, ctx)
    assert ask.describe(spec, ctx) == ("Net revenue · all data (4 Jan 2010 – 16 Feb 2010) · all countries · "
                                       "customer 1 · vs the previous period")


# ---------------------------------------------------------------- answer

def test_single_values_match_kpis(tiny, ctx):
    k = m.kpis(tiny)
    for metric in ["revenue", "gross_sales", "orders", "customers", "aov", "units", "cancelled_value", "cancel_rate"]:
        spec, _ = resolved({"metric": metric}, ctx)
        assert ask.answer(spec, tiny).value == pytest.approx(k[metric]), metric
    spec, _ = resolved({"metric": "products"}, ctx)
    assert ask.answer(spec, tiny).value == 3


def test_filters_narrow_the_answer(tiny, ctx):
    spec, _ = resolved({"metric": "revenue", "product": "item 10002"}, ctx)
    assert ask.answer(spec, tiny).value == 40  # 20 + 40 sold, 20 cancelled
    spec, _ = resolved({"metric": "revenue", "customer_id": "2"}, ctx)
    assert ask.answer(spec, tiny).value == 20
    spec, _ = resolved({"metric": "aov", "countries": ["Germany"], "start": "2010-01-04", "end": "2010-01-31"}, ctx)
    assert ask.answer(spec, tiny).value is None  # no orders: undefined, not zero
    spec, _ = resolved({"metric": "orders", "countries": ["Germany"], "start": "2010-01-04", "end": "2010-01-31"}, ctx)
    assert ask.answer(spec, tiny).value == 0


def test_comparisons(tiny, ctx):
    # February (1-16 Feb: 5 + 30 + 40 - 20 = 55) vs the 16 days before it (16-31 Jan: France's 20).
    spec, _ = resolved({"start": "2010-02-01", "end": "2010-02-16", "compare": "previous_period"}, ctx)
    result = ask.answer(spec, tiny, first=ctx.first)
    assert (result.value, result.previous, result.previous_period) == (55, 20, (date(2010, 1, 16),
                                                                               date(2010, 1, 31)))
    assert result.change == pytest.approx(1.75)
    spec, _ = resolved({"compare": "last_year"}, ctx)
    result = ask.answer(spec, tiny, first=ctx.first)
    assert result.previous is None and "before the data" in result.notes[0]


def test_ranked_tables(tiny, ctx):
    spec, _ = resolved({"metric": "revenue", "group_by": "country", "limit": 2}, ctx)
    table = ask.answer(spec, tiny).table
    assert table["country"].tolist() == ["United Kingdom", "Germany"]  # 45, then 40 (France 20 is cut)
    spec, _ = resolved({"metric": "revenue", "group_by": "country", "sort": "asc", "limit": 1}, ctx)
    assert ask.answer(spec, tiny).table["country"].tolist() == ["France"]
    spec, _ = resolved({"metric": "orders", "group_by": "month"}, ctx)
    assert ask.answer(spec, tiny).table["orders"].tolist() == [2, 2]  # natural (time) order, all periods


def test_ratio_rankings_leave_out_small_groups(tiny, ctx, monkeypatch):
    monkeypatch.setattr(ask, "MIN_ORDERS_TO_RANK", 2)
    spec, _ = resolved({"metric": "aov", "group_by": "country"}, ctx)
    result = ask.answer(spec, tiny)
    assert result.table["country"].tolist() == ["United Kingdom"]  # France and Germany have 1 order each
    assert list(result.table.columns) == ["country", "aov", "revenue", "orders"]
    assert "Left out 2 country groups" in result.notes[0]


def test_segments(tiny, ctx):
    spec, _ = resolved({"metric": "customers", "group_by": "segment"}, ctx)
    table = ask.answer(spec, tiny).table
    assert table["customers"].sum() == 2 and table["customer_share"].sum() == pytest.approx(1)


def test_group_labels():
    table = pd.DataFrame({"quarter": pd.to_datetime(["2011-04-01"]), "hour": [8], "stock_code": ["22423"],
                          "description": ["REGENCY CAKESTAND 3 TIER"], "customer_id": [14646]})
    assert ask.group_labels(table, "quarter").tolist() == ["2011 Q2"]
    assert ask.group_labels(table, "hour").tolist() == ["08:00"]
    assert ask.group_labels(table, "product").tolist() == ["Regency Cakestand 3 Tier (22423)"]
    assert ask.group_labels(table, "customer").tolist() == ["Customer 14646"]


def test_format_value():
    assert ask.format_value("revenue", 1234.5) == "£1,234.50"
    assert ask.format_value("orders", 1234) == "1,234"
    assert ask.format_value("cancel_rate", 0.0365) == "3.65%"
    assert ask.format_value("aov", None) == "–"


# ---------------------------------------------------------------- the Gemini call (stubbed)

def reply(request: dict, thought: bool = False) -> dict:
    parts = ([{"text": "thinking…", "thought": True}] if thought else []) + [{"text": json.dumps(request)}]
    return {"candidates": [{"content": {"parts": parts}}]}


class FakePost:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, body, api_key, timeout):
        self.calls.append((url, body, api_key))
        return self.responses.pop(0)


def test_parse_question_sends_the_schema_and_reads_the_request(ctx):
    post = FakePost((200, reply({"metric": "orders"}, thought=True)))
    raw = ask.parse_question("how many orders?", api_key="k-123", model="gemini-x", ctx=ctx, post=post)
    assert raw == {"metric": "orders"}
    url, body, key = post.calls[0]
    assert url.endswith("/models/gemini-x:generateContent") and "k-123" not in url and key == "k-123"
    assert body["generationConfig"]["responseSchema"] is ask.REQUEST_SCHEMA
    assert body["generationConfig"]["temperature"] == 0
    assert "United Kingdom" in body["systemInstruction"]["parts"][0]["text"]


def test_parse_question_trims_long_questions(ctx):
    post = FakePost((200, reply({})))
    ask.parse_question("x" * 1000, api_key="k", model="m", ctx=ctx, post=post)
    assert len(post.calls[0][1]["contents"][0]["parts"][0]["text"]) == ask.MAX_QUESTION_CHARS


def test_quota_errors_stop_at_once(ctx):
    post = FakePost((429, {}), (200, reply({})))
    with pytest.raises(ask.AskError, match="quota"):
        ask.parse_question("q", api_key="secret-key", model="m", ctx=ctx, post=post)
    assert len(post.calls) == 1


def test_one_retry_for_busy_server_or_dropped_connection(ctx):
    post = FakePost((503, {}), (200, reply({"metric": "units"})))
    assert ask.parse_question("q", api_key="k", model="m", ctx=ctx, post=post) == {"metric": "units"}
    post = FakePost((0, {}), (0, {}))
    with pytest.raises(ask.AskError, match="Couldn't reach"):
        ask.parse_question("q", api_key="k", model="m", ctx=ctx, post=post)
    post = FakePost((400, {"error": {"message": "bad"}}))
    with pytest.raises(ask.AskError, match=r"\(400\)") as error:
        ask.parse_question("q", api_key="secret-key", model="m", ctx=ctx, post=post)
    assert "secret-key" not in str(error.value)


@pytest.mark.parametrize("payload", [
    {}, {"candidates": []}, {"candidates": [{"content": {"parts": [{"text": "not json"}]}}]},
    {"candidates": [{"content": {"parts": [{"text": "[1, 2]"}]}}]},
])
def test_unusable_replies(ctx, payload):
    with pytest.raises(ask.AskError, match="usable request"):
        ask.parse_question("q", api_key="k", model="m", ctx=ctx, post=FakePost((200, payload)))


def test_model_name():
    assert ask.model_name("gemini-3.5-flash-lite") == "gemini-3.5-flash-lite"
    assert ask.model_name("") == ask.DEFAULT_MODEL
    assert ask.model_name("x/../y?z") == ask.DEFAULT_MODEL  # it goes into the URL


def test_system_prompt_examples_resolve(ctx):
    prompt = ask.system_prompt(ctx)
    assert str(ctx.last) in prompt and "Q: Will sales grow next year?" in prompt
    for _, fields in ask._EXAMPLES:
        assert json.loads(ask._example_request(fields))["metric"] in ask.METRICS


# ---------------------------------------------------------------- access, quota, config

def test_password_ok():
    assert ask.password_ok("open sesame", "open sesame")
    assert not ask.password_ok("open", "open sesame")
    assert not ask.password_ok("", "")  # no password set means locked, not open


def test_daily_counter():
    counter = ask.DailyCounter(cap=2)
    day = date(2026, 1, 1)
    assert [counter.take(day) for _ in range(3)] == [True, True, False]
    assert counter.take(date(2026, 1, 2))  # a new day starts again


def test_load_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nASK_TEST_A=one\nexport ASK_TEST_B=\"two\"\nASK_TEST_C=from-file\nnot a pair\n")
    monkeypatch.delenv("ASK_TEST_A", raising=False)
    monkeypatch.delenv("ASK_TEST_B", raising=False)
    monkeypatch.setenv("ASK_TEST_C", "already-set")
    ask.load_env(env)
    import os
    assert (os.environ["ASK_TEST_A"], os.environ["ASK_TEST_B"], os.environ["ASK_TEST_C"]) == (
        "one", "two", "already-set")
    ask.load_env(tmp_path / "missing.env")  # no file: nothing happens


def test_saved_examples_resolve_on_the_real_data():
    examples = ask.load_examples()
    assert len(examples) >= 5
    df = pd.read_parquet(Path(__file__).resolve().parents[1] / "data" / "processed" / "transactions.parquet")
    ctx = ask.build_context(df)
    for example in examples:
        out = ask.resolve(example["request"], ctx)
        assert not isinstance(out, ask.Problem), (example["question"], out)
