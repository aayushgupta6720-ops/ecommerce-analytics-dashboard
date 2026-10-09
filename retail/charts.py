"""Plotly figure builders sharing one look in light and dark themes.

Palette: the dataviz reference palette. Only the first three categorical slots are
used (they validate all-pairs in both modes); every magnitude view uses the single
blue sequential ramp. Charts never use two y-axes.
"""

from __future__ import annotations

import math
import string

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.colors import sample_colorscale

FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'

TOKENS = {
    "light": {
        "surface": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "dim": "#d6d5ce", "neg": "#2a78d6", "pos": "#e34948",
        "series": ["#2a78d6", "#eb6834", "#1baf7a"],
        # blue ramp 100 -> 700: near-zero recedes toward the light surface
        "seq": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
    },
    "dark": {
        "surface": "#1a1a19", "text": "#ffffff", "text2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "dim": "#3d3d3a", "neg": "#3987e5", "pos": "#e66767",
        "series": ["#3987e5", "#d95926", "#199e70"],
        # same ramp reversed: near-zero recedes toward the dark surface
        "seq": ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
    },
}


def mode() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:  # outside a Streamlit run (tests, scripts)
        return "light"


def tok() -> dict:
    return TOKENS[mode()]


def colorscale() -> list[list]:
    seq = tok()["seq"]
    return [[i / (len(seq) - 1), c] for i, c in enumerate(seq)]


# ---------------------------------------------------------------- number formats

def money(v: float, decimals: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    for div, suffix in ((1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"£{v / div:,.{decimals}f}{suffix}"
    return f"£{v:,.0f}"


def number(v: float) -> str:
    for div, suffix in ((1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:,.1f}{suffix}"
    return f"{v:,.0f}"


def nice(names: pd.Series, width: int | None = None) -> pd.Series:
    """'POPPY'S PLAYHOUSE' -> "Poppy's Playhouse" (str.title would give "Poppy'S"), optionally shortened."""
    out = names.astype(str).map(string.capwords)
    return out if width is None else out.map(lambda n: n if len(n) <= width else n[: width - 1] + "…")


def pct(v: float, decimals: int = 1) -> str:
    return "–" if v is None or pd.isna(v) else f"{v:.{decimals}%}"


# ---------------------------------------------------------------- layout

def _layout(fig: go.Figure, *, title: str | None = None, height: int = 360, legend: bool = False) -> go.Figure:
    t = tok()
    axis = dict(
        gridcolor=t["grid"], gridwidth=1, linecolor=t["axis"], zeroline=False,
        tickfont=dict(color=t["muted"], size=12), title=dict(font=dict(color=t["text2"], size=12)),
        ticks="", automargin=True,
    )
    fig.update_layout(
        height=height,
        title=dict(text=title, font=dict(color=t["text"], size=15), x=0, xanchor="left", xref="container",
                   y=1, yanchor="top", yref="container", pad=dict(t=6, l=2)) if title else None,
        font=dict(family=FONT, color=t["text2"], size=13),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=4, r=16, t=(84 if legend else 44) if title else (40 if legend else 12), b=4),
        colorway=t["series"],
        showlegend=legend,
        legend=dict(orientation="h", x=0, xanchor="left", y=1.0, yanchor="bottom",
                    font=dict(color=t["text2"]), title=None),
        hoverlabel=dict(bgcolor=t["surface"], bordercolor=t["axis"], font=dict(color=t["text"], family=FONT)),
        bargap=0.35,
        hovermode="closest",
    )
    fig.update_xaxes(**axis, showgrid=False, showline=True)
    fig.update_yaxes(**axis, showgrid=True, showline=False)
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, theme=None, width="stretch", config={"displayModeBar": False})


# ---------------------------------------------------------------- chart builders

def bar_h(labels: pd.Series, values: pd.Series, *, title: str, fmt=money, hover: list[str] | None = None,
          color: str | None = None) -> go.Figure:
    """Ranked horizontal bars, largest on top, value at the bar tip."""
    t = tok()
    n = len(labels)
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=color or t["series"][0], cornerradius=4),
        text=[fmt(v) for v in values], textposition="outside", cliponaxis=False,
        textfont=dict(color=t["text2"], size=12),
        hovertext=hover if hover is not None else labels, hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig = _layout(fig, title=title, height=max(160, 28 * n + 60))
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=t["text2"]))
    fig.update_xaxes(showgrid=True, showline=False, showticklabels=False, range=[0, float(values.max() or 1) * 1.28])
    return fig


def bar_v(x: pd.Series, y: pd.Series, *, title: str, fmt=money, height: int = 300, hover: list[str] | None = None,
          highlight: pd.Series | None = None, yfmt: str | None = None, yprefix: str = "") -> go.Figure:
    """Columns from a single baseline. `highlight` (bool series) grays the False bars."""
    t = tok()
    colors = t["series"][0] if highlight is None else [t["series"][0] if h else t["dim"] for h in highlight]
    fig = go.Figure(go.Bar(
        x=x, y=y, marker=dict(color=colors, cornerradius=4),
        hovertext=hover if hover is not None else [f"{a}: {fmt(b)}" for a, b in zip(x, y)],
        hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig = _layout(fig, title=title, height=height)
    fig.update_yaxes(tickformat=yfmt or "~s", tickprefix=yprefix)
    return fig


def timeline(df: pd.DataFrame, x: str, y: str, *, title: str, fmt=money, partial: str | None = "partial",
             yfmt: str = "~s", tickprefix: str = "£", xfmt: str = "%b %Y") -> go.Figure:
    """Single-series line; partial periods get a hollow marker and a note."""
    t = tok()
    is_partial = df[partial] if partial and partial in df else pd.Series(False, index=df.index)
    fig = go.Figure(go.Scatter(
        x=df[x], y=df[y], mode="lines+markers",
        line=dict(color=t["series"][0], width=2, shape="linear"),
        marker=dict(
            size=[9 if p else 7 for p in is_partial],
            color=[t["surface"] if p else t["series"][0] for p in is_partial],
            line=dict(color=[t["series"][0] if p else t["surface"] for p in is_partial], width=2),
        ),
        hovertext=[f"{d.strftime(xfmt)}{' (partial month)' if p else ''}<br>{fmt(v)}"
                   for d, v, p in zip(df[x], df[y], is_partial)],
        hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig = _layout(fig, title=title)
    fig.update_yaxes(tickformat=yfmt, tickprefix=tickprefix, rangemode="tozero")
    for _, row in df[is_partial].iterrows():
        fig.add_annotation(x=row[x], y=row[y], text="partial month", showarrow=False, yshift=-18,
                           xanchor="right", font=dict(size=11, color=t["muted"]))
    return fig


def lines_by_year(df: pd.DataFrame, *, title: str) -> go.Figure:
    """Month-of-year on x, one line per year, legend + end labels. Colors are pinned per year."""
    t = tok()
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    year_slot = {2010: 0, 2011: 1, 2009: 2}  # color follows the year, not its rank in the filter
    fig = go.Figure()
    for year, part in df.groupby("year"):
        part = part.sort_values("month_num")
        color = t["series"][year_slot.get(int(year), 2)]
        fig.add_trace(go.Scatter(
            x=[months[m - 1] for m in part["month_num"]], y=part["revenue"], name=str(year),
            mode="lines+markers", line=dict(color=color, width=2),
            marker=dict(size=[9 if p else 7 for p in part["partial"]],
                        color=[t["surface"] if p else color for p in part["partial"]],
                        line=dict(color=[color if p else t["surface"] for p in part["partial"]], width=2)),
            hovertext=[f"{m:%b %Y}{' (partial)' if p else ''}: {money(v)}"
                       for m, v, p in zip(part["month"], part["revenue"], part["partial"])],
            hovertemplate="%{hovertext}<extra></extra>",
        ))
        last = part.iloc[-1]
        fig.add_annotation(x=months[int(last["month_num"]) - 1], y=last["revenue"], text=str(year),
                           showarrow=False, xshift=22, font=dict(size=12, color=t["text2"]))
    fig = _layout(fig, title=title, legend=True)
    fig.update_xaxes(categoryorder="array", categoryarray=months)
    fig.update_yaxes(tickformat="~s", tickprefix="£", rangemode="tozero")
    return fig


def heatmap(z: pd.DataFrame, *, title: str, xlabel: str = "", ylabel: str = "", zfmt: str = ",.0f",
            hover_name: str = "value", height: int = 320, zmax: float | None = None) -> go.Figure:
    t = tok()
    fig = go.Figure(go.Heatmap(
        z=z.to_numpy(), x=[str(c) for c in z.columns], y=[str(i) for i in z.index],
        colorscale=colorscale(), zmin=0, zmax=zmax, xgap=2, ygap=2,
        colorbar=dict(thickness=10, outlinewidth=0, tickfont=dict(color=t["muted"], size=11), tickformat=zfmt),
        hovertemplate=f"{ylabel} %{{y}} · {xlabel} %{{x}}<br>{hover_name}: %{{z:{zfmt}}}<extra></extra>",
        hoverongaps=False,
    ))
    fig = _layout(fig, title=title, height=height)
    fig.update_xaxes(showline=False, title_text=xlabel, type="category")
    fig.update_yaxes(showgrid=False, title_text=ylabel, autorange="reversed", type="category")
    return fig


def choropleth(df: pd.DataFrame, *, title: str) -> go.Figure:
    t = tok()
    mapped = df[df["iso3"].notna()]
    fig = go.Figure(go.Choropleth(
        locations=mapped["iso3"], z=mapped["revenue"], colorscale=colorscale(),
        marker=dict(line=dict(color=t["surface"], width=0.5)),
        colorbar=dict(thickness=10, outlinewidth=0, tickprefix="£", tickformat="~s",
                      tickfont=dict(color=t["muted"], size=11)),
        hovertext=[f"{c}<br>{money(r)} · {o:,} orders" for c, r, o in
                   zip(mapped["country"], mapped["revenue"], mapped["orders"])],
        hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig = _layout(fig, title=title, height=440)
    fig.update_geos(
        showframe=False, showcoastlines=False, projection_type="natural earth",
        bgcolor="rgba(0,0,0,0)", landcolor=t["grid"], showland=True, showcountries=True,
        countrycolor=t["surface"], lataxis_range=[-50, 80],
    )
    return fig


def treemap(df: pd.DataFrame, *, label: str, size: str, color: str, title: str, hover: list[str]) -> go.Figure:
    """Tiles sized by `size`, shaded on the sequential ramp by `color` (0-1).

    Colours are resolved here rather than via marker.colorscale, because Plotly ignores
    root.color when a colorscale is set and paints a gray frame around the tiles.
    """
    t = tok()
    values = df[color].astype(float)
    span = (values.max() - values.min()) or 1.0
    fills = sample_colorscale(colorscale(), ((values - values.min()) / span).tolist())
    ink = [_ink_on(c) for c in fills]
    fig = go.Figure(go.Treemap(
        labels=df[label], parents=[""] * len(df), values=df[size],
        marker=dict(colors=fills, line=dict(color=t["surface"], width=2), pad=dict(t=0, l=0, r=0, b=0)),
        texttemplate="%{label}<br>%{value:,} customers", textfont=dict(family=FONT, size=13, color=ink),
        hovertext=hover, hovertemplate="%{hovertext}<extra></extra>", tiling=dict(pad=0), branchvalues="total",
        pathbar=dict(visible=False), root=dict(color="rgba(0,0,0,0)"),
    ))
    fig = _layout(fig, title=title, height=380)
    fig.update_layout(margin=dict(l=0, r=0, t=44, b=0))
    return fig


def _ink_on(fill: str) -> str:
    """White or near-black label ink, whichever reads on the tile (relative luminance)."""
    r, g, b = (int(v) / 255 for v in fill[fill.index("(") + 1:-1].split(",")[:3])
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#0b0b0b" if lum > 0.45 else "#ffffff"


def grouped_bars_h(labels: pd.Series, series: dict[str, pd.Series], *, title: str, fmt=pct) -> go.Figure:
    """Two or three measures of the SAME unit side by side (e.g. share of customers vs share of revenue)."""
    t = tok()
    fig = go.Figure()
    for i, (name, values) in enumerate(series.items()):
        fig.add_trace(go.Bar(
            y=labels, x=values, name=name, orientation="h", marker=dict(color=t["series"][i], cornerradius=4),
            hovertext=[f"{lab} · {name}: {fmt(v)}" for lab, v in zip(labels, values)],
            hovertemplate="%{hovertext}<extra></extra>",
        ))
    fig = _layout(fig, title=title, height=max(220, 44 * len(labels) + 80), legend=True)
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    fig.update_yaxes(autorange="reversed", showgrid=False, tickfont=dict(color=t["text2"]))
    fig.update_xaxes(showgrid=True, showline=False, tickformat=".0%")
    return fig


def scatter_highlight(df: pd.DataFrame, x: str, y: str, *, highlight: pd.Series, title: str,
                      xlabel: str, ylabel: str, hover: list[str], log_y: bool = False,
                      highlight_name: str = "Selected") -> go.Figure:
    """Emphasis scatter: the selected group in slot 1, everything else recedes to gray."""
    t = tok()
    fig = go.Figure()
    for is_hl, color, name in ((False, t["dim"], "Other customers"), (True, t["series"][0], highlight_name)):
        part = df[highlight == is_hl]
        fig.add_trace(go.Scattergl(
            x=part[x], y=part[y], mode="markers", name=name,
            marker=dict(size=8, color=color, opacity=0.85 if is_hl else 0.6, line=dict(color=t["surface"], width=1)),
            hovertext=[h for h, keep in zip(hover, highlight == is_hl) if keep],
            hovertemplate="%{hovertext}<extra></extra>",
        ))
    fig = _layout(fig, title=title, height=400, legend=True)
    fig.update_xaxes(title_text=xlabel, showgrid=True)
    if log_y:
        ticks = [v for v in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000) if v <= max(1, df[y].max()) * 2]
        fig.update_yaxes(title_text=ylabel, type="log", tickvals=ticks, ticktext=[f"{v:,}" for v in ticks])
    else:
        fig.update_yaxes(title_text=ylabel)
    return fig


def pareto_curve(p: pd.DataFrame, point: float, *, title: str) -> go.Figure:
    t = tok()
    step = max(1, len(p) // 400)  # thin the line for rendering, keep the ends
    thin = pd.concat([p.iloc[::step], p.iloc[[-1]]]).drop_duplicates("stock_code") if len(p) else p
    fig = go.Figure(go.Scatter(
        x=thin["product_share"], y=thin["cum_revenue_share"], mode="lines",
        line=dict(color=t["series"][0], width=2), fill="tozeroy", fillcolor=_rgba(t["series"][0], 0.10),
        hovertext=[f"Top {ps:.0%} of products → {rs:.0%} of revenue"
                   for ps, rs in zip(thin["product_share"], thin["cum_revenue_share"])],
        hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig = _layout(fig, title=title, height=340)
    fig.add_shape(type="line", x0=0, x1=point, y0=0.8, y1=0.8, line=dict(color=t["axis"], width=1))
    fig.add_shape(type="line", x0=point, x1=point, y0=0, y1=0.8, line=dict(color=t["axis"], width=1))
    fig.add_trace(go.Scatter(x=[point], y=[0.8], mode="markers", hoverinfo="skip",
                             marker=dict(size=9, color=t["series"][0], line=dict(color=t["surface"], width=2))))
    fig.add_annotation(x=point, y=0.8, text=f"{point:.0%} of products = 80% of revenue", showarrow=False,
                       xanchor="left", xshift=10, yshift=-12, font=dict(size=12, color=t["text2"]))
    fig.update_xaxes(tickformat=".0%", title_text="Share of products (ranked by revenue)", range=[0, 1])
    fig.update_yaxes(tickformat=".0%", title_text="Cumulative share of revenue", range=[0, 1.02])
    return fig


def multi_line(x, series: dict[str, pd.Series], *, title: str, xtitle: str = "", ytitle: str = "",
               yfmt: str = "~s", yprefix: str = "", xfmt: str | None = None, ink: str | None = None,
               diagonal: bool = False, shade: tuple | None = None, height: int = 380, hover_fmt=money) -> go.Figure:
    """Several series on one axis. `ink` names the series drawn in neutral ink (e.g. actuals); the rest take
    categorical slots in order. `shade` = (x0, x1, label) marks a region such as an unseen test period."""
    t = tok()
    fig = go.Figure()
    if shade:
        fig.add_vrect(x0=shade[0], x1=shade[1], fillcolor=t["grid"], opacity=0.5, line_width=0, layer="below")
        fig.add_annotation(x=shade[0], y=1, yref="paper", text=shade[2], showarrow=False, xanchor="left", xshift=6,
                           yshift=-4, yanchor="top", font=dict(size=11, color=t["muted"]))
    if diagonal:
        fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1, line=dict(color=t["axis"], width=1))
    slot = 0
    for name, values in series.items():
        if name == ink:
            color = t["text"]
        else:
            color = t["series"][slot % len(t["series"])]
            slot += 1
        fig.add_trace(go.Scatter(
            x=x, y=values, name=name, mode="lines", line=dict(color=color, width=2),
            hovertext=[f"{name}: {hover_fmt(v)}" if pd.notna(v) else "" for v in values],
            hovertemplate="%{hovertext}<extra></extra>", connectgaps=False,
        ))
    fig = _layout(fig, title=title, height=height, legend=True)
    fig.update_xaxes(title_text=xtitle, tickformat=xfmt)
    fig.update_yaxes(title_text=ytitle, tickformat=yfmt, tickprefix=yprefix, rangemode="tozero")
    fig.update_layout(hovermode="x unified" if not diagonal else "closest")
    return fig


def grouped_columns(x, series: dict[str, pd.Series], *, title: str, fmt=money, yfmt: str = "~s", yprefix: str = "",
                    xtitle: str = "", height: int = 340) -> go.Figure:
    """Two or three measures in the SAME unit side by side per category (e.g. predicted vs actual)."""
    t = tok()
    fig = go.Figure()
    for i, (name, values) in enumerate(series.items()):
        fig.add_trace(go.Bar(
            x=x, y=values, name=name, marker=dict(color=t["series"][i], cornerradius=4),
            hovertext=[f"{lab} · {name}: {fmt(v)}" for lab, v in zip(x, values)],
            hovertemplate="%{hovertext}<extra></extra>",
        ))
    fig = _layout(fig, title=title, height=height, legend=True)
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    fig.update_xaxes(title_text=xtitle, type="category")
    fig.update_yaxes(tickformat=yfmt, tickprefix=yprefix)
    return fig


def signed_bars_h(labels: pd.Series, values: pd.Series, *, title: str, neg_label: str, pos_label: str,
                  fmt=lambda v: f"{v:+.2f}") -> go.Figure:
    """Diverging bars around zero: blue for one direction, red for the other, sorted by size."""
    t = tok()
    order = values.abs().sort_values().index
    labels, values = labels.loc[order], values.loc[order]
    fig = go.Figure(go.Bar(
        x=values, y=labels, orientation="h",
        marker=dict(color=[t["pos"] if v > 0 else t["neg"] for v in values], cornerradius=4),
        text=[fmt(v) for v in values], textposition="outside", cliponaxis=False,
        textfont=dict(color=t["text2"], size=12),
        hovertext=[f"{lab}: {fmt(v)} ({pos_label if v > 0 else neg_label})" for lab, v in zip(labels, values)],
        hovertemplate="%{hovertext}<extra></extra>",
    ))
    fig = _layout(fig, title=title, height=max(200, 30 * len(labels) + 80))
    span = float(values.abs().max() or 1) * 1.35
    fig.update_xaxes(range=[-span, span], zeroline=True, zerolinecolor=t["axis"], showgrid=True, showline=False)
    fig.update_yaxes(showgrid=False, tickfont=dict(color=t["text2"]))
    return fig


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"
