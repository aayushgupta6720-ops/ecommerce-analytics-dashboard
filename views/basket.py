import streamlit as st

from retail import charts as ch
from retail import data

f = data.page_header(
    "Market basket",
    "Which products end up in the same order. Rules read *customers who bought A also bought B*.",
)

with st.expander("How to read support, confidence and lift", expanded=False):
    st.markdown(
        "- **Support**: share of all orders that contain both products.\n"
        "- **Confidence**: of the orders containing A, the share that also contain B.\n"
        "- **Lift**: how much more often A and B appear together than if they were unrelated. "
        "1 = no relationship; 5 = five times more often than chance."
    )

c1, c2, c3 = st.columns(3)
min_support = c1.select_slider("Minimum support", options=[0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05],
                               value=0.01, format_func=lambda v: f"{v:.2%}",
                               help="Lower values find rarer pairs but take longer.")
min_conf = c2.slider("Minimum confidence", 0.0, 1.0, 0.3, 0.05, format="%.2f")
min_lift = c3.slider("Minimum lift", 1.0, 30.0, 2.0, 0.5)

with st.spinner("Finding product pairs…"):
    rules = data.compute("basket_rules", f, min_support=min_support)
n_orders = data.compute("kpis", f)["orders"]
view = rules[(rules["confidence"] >= min_conf) & (rules["lift"] >= min_lift)] if len(rules) else rules

m1, m2, m3 = st.columns(3)
m1.metric("Orders analysed", f"{n_orders:,}", border=True)
m2.metric("Rules found", f"{len(view):,}", border=True)
m3.metric("Strongest lift", f"{view['lift'].max():.1f}×" if len(view) else "–", border=True)
if data.empty_state(view, "rules at these thresholds (try lowering support, confidence or lift)"):
    st.stop()

# A->B and B->A share a lift; show each pair once, in its higher-confidence direction.
pairs = view.assign(key=[tuple(sorted(p)) for p in zip(view["antecedent"], view["consequent"])])
pairs = pairs.sort_values("confidence", ascending=False).drop_duplicates("key").nlargest(12, "lift")
ch.show(ch.bar_h(ch.nice(pairs["antecedent_desc"], 34) + "  →  " + ch.nice(pairs["consequent_desc"], 34), pairs["lift"],
                 title="Strongest pairs by lift", fmt=lambda v: f"{v:.1f}×",
                 hover=[f"{a} → {b}<br>lift {l:.1f}× · confidence {c:.0%} · in {n:,} orders"
                        for a, b, l, c, n in zip(pairs["antecedent_desc"], pairs["consequent_desc"],
                                                 pairs["lift"], pairs["confidence"], pairs["pair_baskets"])]))
st.caption("Many of the strongest pairs are colour or design variants of the same item, like the Poppy's "
           "Playhouse rooms or matching bowls. That points to bundles or \"complete the set\" prompts.")

left, right = st.columns([2, 3], gap="large")
with left:
    antecedents = (view.groupby(["antecedent", "antecedent_desc"]).size().reset_index(name="n")
                   .sort_values("n", ascending=False))
    labels = dict(zip(antecedents["antecedent"],
                      antecedents["antecedent"] + " · " + ch.nice(antecedents["antecedent_desc"])))
    code = st.selectbox("Customers who bought…", list(labels), format_func=labels.get)
    st.caption("Products with the most rules at the current thresholds are listed first.")
with right:
    also = view[view["antecedent"] == code].nlargest(10, "confidence")
    ch.show(ch.bar_h(ch.nice(also["consequent_desc"], 40), also["confidence"], title="…also bought", fmt=ch.pct,
                     hover=[f"{d}<br>{c:.0%} of these orders · lift {l:.1f}×"
                            for d, c, l in zip(also["consequent_desc"], also["confidence"], also["lift"])]))

st.dataframe(
    view, hide_index=True, width="stretch", height=380,
    column_config={
        "antecedent": "If code", "antecedent_desc": "If they bought",
        "consequent": "Then code", "consequent_desc": "They also bought",
        "pair_baskets": st.column_config.NumberColumn("Orders with both", format="%,d"),
        "support": st.column_config.NumberColumn("Support", format="percent"),
        "confidence": st.column_config.NumberColumn("Confidence", format="percent"),
        "lift": st.column_config.NumberColumn("Lift", format="%.1f×"),
    },
)
st.download_button("Download rules (CSV)", view.to_csv(index=False), "basket_rules.csv", "text/csv",
                   icon=":material/download:")
