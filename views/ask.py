"""Ask the data: a plain-English question in, an answer computed by the dashboard's own metric code out."""

import os
import re

import streamlit as st

from retail import ask, data
from retail import charts as ch

ask.load_env()
API_KEY = os.environ.get("GEMINI_API_KEY", "")
PASSWORD = os.environ.get("ASK_PASSWORD", "")
MODEL = ask.model_name(os.environ.get("ASK_MODEL"))
DAILY_CAP = int(os.environ.get("ASK_DAILY_CAP") or 300)
SESSION_CAP = 30
MAX_ATTEMPTS = 5
SORT_LABELS = {"natural": "Natural order", "desc": "Highest first", "asc": "Lowest first"}
FORMATS = {"money": ch.money, "count": ch.number, "pct": ch.pct}


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]  # str.capitalize would turn "RFM" into "Rfm"

st.header("Ask the data")
st.caption(
    "Ask in plain English. A language model turns the question into a request (a measure, a period, filters and "
    "a grouping), then the dashboard's own metric code computes the answer, with the same definitions as every "
    "other page. The period and countries come from your question, not the sidebar."
)
ctx = data.ask_context()
examples = ask.load_examples()
state = st.session_state


def sync_form(spec: ask.Spec) -> None:
    """Show `spec` in the Adjust form (only called before the form's widgets are drawn, or in a callback)."""
    state.update({
        "adj_metric": spec.metric, "adj_period": (spec.start, spec.end), "adj_countries": list(spec.countries),
        "adj_product": spec.product, "adj_customer": "" if spec.customer_id is None else str(spec.customer_id),
        "adj_group": spec.group_by, "adj_sort": spec.sort, "adj_limit": spec.limit, "adj_compare": spec.compare,
    })


def use(resolved, question: str | None = None, source: str = "question") -> None:
    """Make a resolved request (or a Problem) the page's current answer."""
    state["ask_current"] = (question, resolved)
    if source != "example":
        state["ask_example"] = None
    if not isinstance(resolved, ask.Problem):
        sync_form(resolved[0])


def pick_example() -> None:
    question = state.get("ask_example")
    request = next((e["request"] for e in examples if e["question"] == question), None)
    if request is not None:
        use(ask.resolve(request, ctx), question, source="example")


def apply_form() -> None:
    period = state.get("adj_period") or (ctx.first, ctx.last)
    request = {
        "answerable": True, "metric": state["adj_metric"], "start": str(period[0]), "end": str(period[-1]),
        "countries": state["adj_countries"], "product": state["adj_product"], "customer_id": state["adj_customer"],
        "group_by": state["adj_group"], "sort": state["adj_sort"], "limit": state["adj_limit"],
        "compare": state["adj_compare"],
    }
    use(ask.resolve(request, ctx), source="form")


if "ask_current" not in state:
    if examples:
        state["ask_example"] = examples[0]["question"]
        pick_example()
    else:
        use((ask.Spec("revenue", ctx.first, ctx.last), []), source="form")
if "adj_metric" not in state:  # Streamlit drops widget values after a visit to another page
    current = state["ask_current"][1]
    sync_form(ask.Spec("revenue", ctx.first, ctx.last) if isinstance(current, ask.Problem) else current[0])


# ---------------------------------------------------------------- the question box

def question_box() -> None:
    if not API_KEY or not PASSWORD:
        st.info("Free-text questions are switched off here (no API key or password is set). The example "
                "questions and the form below still work.", icon=":material/lock:")
        return
    if not state.get("ask_unlocked"):
        attempts = state.get("ask_attempts", 0)
        if attempts >= MAX_ATTEMPTS:
            st.error("Too many wrong passwords for this session.", icon=":material/lock:")
            return
        with st.form("ask_unlock", border=True):
            st.markdown("**Free-text questions are password-protected**, because each one uses a free daily "
                        "quota. Without the password, try the example questions and the form below.")
            given = st.text_input("Password", type="password", key="ask_password")
            if st.form_submit_button("Unlock", key="ask_unlock_button"):
                if ask.password_ok(given, PASSWORD):
                    state["ask_unlocked"] = True
                    st.rerun()
                state["ask_attempts"] = attempts + 1
                st.error("Wrong password.")
        return

    with st.form("ask_form", border=True):
        question = st.text_input("Your question", key="ask_question", max_chars=ask.MAX_QUESTION_CHARS,
                                 placeholder="e.g. Top 5 products by revenue in Germany in 2011")
        submitted = st.form_submit_button("Ask", type="primary", key="ask_button", icon=":material/search:")
    if not (submitted and question.strip()):
        return
    if state.get("ask_count", 0) >= SESSION_CAP:
        st.warning(f"That's {SESSION_CAP} questions in this session, the limit. The form below still works.")
        return
    if not data.ask_counter(DAILY_CAP).take():
        st.warning("The site's question limit for today is reached. The examples and the form below still work.")
        return
    state["ask_count"] = state.get("ask_count", 0) + 1
    try:
        with st.spinner("Reading your question…"):
            raw = ask.parse_question(question, api_key=API_KEY, model=MODEL, ctx=ctx)
    except ask.AskError as error:
        st.error(str(error))
        return
    use(ask.resolve(raw, ctx), question)


question_box()

if examples:
    st.pills("Example questions", [e["question"] for e in examples], key="ask_example", on_change=pick_example)


# ---------------------------------------------------------------- the answer

def escape(text: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~<$])", r"\\\1", text)


SHARE_LABELS = {"customer_share": "Share of customers", "revenue_share": "Share of revenue"}
NUMBER_FORMATS = {"money": "£%,.2f", "count": "%,.0f", "pct": "percent"}


def column(name: str, group_by: str):
    if name == "group":
        return st.column_config.TextColumn(cap(ask.GROUP_LABELS[group_by]))
    if name == "partial":
        return st.column_config.CheckboxColumn("Partial period")
    if name in SHARE_LABELS:
        return st.column_config.NumberColumn(SHARE_LABELS[name], format="percent")
    label, kind = ask.METRICS[name]
    return st.column_config.NumberColumn(label, format=NUMBER_FORMATS[kind])


def show_value(result: ask.Result) -> None:
    spec = result.spec
    delta = None
    if result.change is not None:
        delta = f"{result.change * 100:+.1f} pp" if spec.metric == "cancel_rate" else f"{result.change:+.1%}"
    worse_when_up = spec.metric in ("cancel_rate", "cancelled_value")
    st.metric(ask.METRICS[spec.metric][0], ask.format_value(spec.metric, result.value), delta, border=True,
              delta_color="inverse" if worse_when_up else "normal")
    if result.previous_period:
        start, end = result.previous_period
        st.caption(f"Compared with {ask.format_value(spec.metric, result.previous)} "
                   f"in {start.day} {start:%b %Y} – {end.day} {end:%b %Y}.")


def show_table(result: ask.Result) -> None:
    spec, table = result.spec, result.table
    if table.empty:
        st.info("Nothing matches this request: there are no sales in that slice.")
        return
    label, kind = ask.METRICS[spec.metric]
    fmt = FORMATS[kind]
    labels = ask.group_labels(table, spec.group_by)
    values = table[spec.metric]
    title = f"{label} by {ask.GROUP_LABELS[spec.group_by]}"
    prefix = "£" if kind == "money" else ""
    yfmt = ".0%" if kind == "pct" else "~s"
    partial = table["partial"] if "partial" in table else None
    if len(table) == 1 and spec.sort != "natural":
        st.metric(f"{'Highest' if spec.sort == 'desc' else 'Lowest'}: {labels.iat[0]}",
                  ask.format_value(spec.metric, values.iat[0]), border=True)
    elif spec.sort == "natural" and spec.group_by in ("month", "day"):
        ch.show(ch.timeline(table, spec.group_by, spec.metric, title=title, fmt=fmt, yfmt=yfmt, tickprefix=prefix,
                            xfmt="%b %Y" if spec.group_by == "month" else "%d %b %Y"))
    elif spec.sort == "natural" and spec.group_by in ("year", "quarter", "weekday", "hour"):
        ch.show(ch.bar_v(labels, values, title=title, fmt=fmt, yfmt=yfmt, yprefix=prefix,
                         highlight=None if partial is None else ~partial))
    elif values.min() >= 0:
        ch.show(ch.bar_h(ch.nice(labels, 48) if spec.group_by == "product" else labels, values, title=title,
                         fmt=fmt, hover=[f"{a}: {ask.format_value(spec.metric, v)}" for a, v in zip(labels, values)]))
    if partial is not None and partial.any():
        st.caption("Partial periods (hollow markers, grey bars) are only partly covered by the data or the period.")

    shown = table.drop(columns=[c for c in table.columns[: table.columns.get_loc(spec.metric)]])
    if partial is not None and not partial.any():
        shown = shown.drop(columns="partial")
    shown.insert(0, "group", labels)
    st.dataframe(shown, hide_index=True, width="stretch",
                 column_config={c: column(c, spec.group_by) for c in shown.columns})
    st.download_button("Download CSV", table.to_csv(index=False), file_name="answer.csv", mime="text/csv",
                       icon=":material/download:", on_click="ignore")


question, resolved = state["ask_current"]
st.divider()
if question:
    st.markdown(f"**You asked:** {escape(question)}")
if isinstance(resolved, ask.Problem):
    show = st.info if resolved.declined else st.warning
    show(resolved.message, icon=":material/help:")
    if resolved.suggestions:
        st.caption("Did you mean: " + ", ".join(resolved.suggestions) + "?")
else:
    spec, notes = resolved
    result = data.ask_answer(spec)
    st.markdown(f"**How I read it:** {ask.describe(spec, ctx)}")
    for note in notes + result.notes:
        st.caption(note)
    if result.table is None:
        show_value(result)
    else:
        show_table(result)
    st.caption("Computed by `metrics.breakdown`: revenue is net of cancellations, orders are distinct sales "
               "invoices, and customers are identified customers, as on the Overview page.")


# ---------------------------------------------------------------- the adjust form

st.divider()
with st.expander("Adjust the request, or build one yourself", expanded=not state.get("ask_unlocked")):
    with st.form("ask_adjust", border=False):
        c1, c2, c3 = st.columns(3)
        c1.selectbox("Measure", list(ask.METRICS), key="adj_metric", format_func=lambda m: ask.METRICS[m][0])
        c2.selectbox("Group by", list(ask.GROUPINGS), key="adj_group",
                     format_func=lambda g: cap(ask.GROUP_LABELS[g]))
        c3.date_input("Period", key="adj_period", min_value=ctx.first, max_value=ctx.last, format="DD/MM/YYYY")
        c1, c2, c3 = st.columns(3)
        c1.multiselect("Countries", list(ctx.countries), key="adj_countries", placeholder="All countries")
        c2.text_input("Product words or stock code", key="adj_product", placeholder="e.g. cake stand")
        c3.text_input("Customer number", key="adj_customer", placeholder="e.g. 14646")
        c1, c2, c3 = st.columns(3)
        c1.selectbox("Order", list(ask.SORTS), key="adj_sort", format_func=SORT_LABELS.get,
                     help="Rankings (countries, products, customers, segments) always list the highest first "
                          "unless you pick Lowest first.")
        c2.number_input("Rows (top / bottom)", min_value=1, max_value=ask.MAX_LIMIT, step=1, key="adj_limit")
        c3.selectbox("Compare with", list(ask.COMPARES), key="adj_compare",
                     format_func=lambda c: cap(ask.COMPARE_LABELS[c]), help="For single figures only.")
        st.form_submit_button("Show answer", on_click=apply_form, key="adj_apply")
