"""Generate a Power BI Project (PBIP): TMDL semantic model + PBIR report.

Called by scripts/export_powerbi.py. Nothing on macOS can open a PBIP, so this output is
validated structurally (scripts/validate_pbip.py) but has not been opened in Power BI.

Formats:
- Semantic model: TMDL files under RetailAnalytics.SemanticModel/definition/ (definition.pbism version 4.0)
- Report: PBIR files under RetailAnalytics.Report/definition/ (definition.pbir version 4.0)
IDs are uuid5-derived so re-running the export doesn't churn every file in git.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

NAME = "RetailAnalytics"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
S = {
    "pbip": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json",
    "platform": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
    "pbism": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
    "pbir": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
    "version": f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json",
    "report": f"{SCHEMA}/item/report/definition/report/3.0.0/schema.json",
    "pages": f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json",
    "page": f"{SCHEMA}/item/report/definition/page/2.0.0/schema.json",
    "visual": f"{SCHEMA}/item/report/definition/visualContainer/2.0.0/schema.json",
}
M_TYPES = {"string": "type text", "int64": "Int64.Type", "double": "type number", "dateTime": "type date",
           "boolean": "type logical"}
DEFAULT_DATA_FOLDER = "C:\\RetailAnalytics\\powerbi\\data\\"
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]


def gid(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"retail-analytics/{key}"))


def q(name: str) -> str:
    """TMDL object name, quoted when it isn't a plain identifier."""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


def dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- semantic model (TMDL)

def _indent(text: str, tabs: int) -> str:
    return "\n".join("\t" * tabs + line for line in text.splitlines())


def table_tmdl(table, measures) -> str:
    out = [f"/// {table.description}", f"table {q(table.name)}", f"\tlineageTag: {gid('table/' + table.name)}"]
    if table.is_date_table:
        out.append("\tdataCategory: Time")
    out.append("")
    for m in measures:
        out.append(f"\t/// {m.description}")
        if "\n" in m.expression:
            out.append(f"\tmeasure {q(m.name)} =")
            out.append(_indent(m.expression, 3))
        else:
            out.append(f"\tmeasure {q(m.name)} = {m.expression}")
        out += [f"\t\tformatString: {m.fmt}", f"\t\tdisplayFolder: {m.folder}",
                f"\t\tlineageTag: {gid(f'measure/{m.table}/{m.name}')}", ""]
    for c in table.columns:
        out.append(f"\tcolumn {q(c.name)}")
        out.append(f"\t\tdataType: {c.dtype}")
        if c.key:
            out.append("\t\tisKey")
        if c.hidden:
            out.append("\t\tisHidden")
        if c.fmt:
            out.append(f"\t\tformatString: {c.fmt}")
        if c.category:
            out.append(f"\t\tdataCategory: {c.category}")
        out.append(f"\t\tlineageTag: {gid(f'column/{table.name}/{c.name}')}")
        out.append(f"\t\tsummarizeBy: {c.summarize}")
        out.append(f"\t\tsourceColumn: {c.name}")
        if c.sort_by:
            out.append(f"\t\tsortByColumn: {q(c.sort_by)}")
        out.append("")
        if c.dtype == "dateTime":
            out += ["\t\tannotation UnderlyingDateTimeDataType = Date", ""]
        out += ["\t\tannotation SummarizationSetBy = User", ""]
    types = ", ".join(f'{{"{c.name}", {M_TYPES[c.dtype]}}}' for c in table.columns)
    m_code = "\n".join([
        "let",
        f'    Source = Csv.Document(File.Contents(DataFolder & "{table.name}.csv"), '
        '[Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        f'    Typed = Table.TransformColumnTypes(Promoted, {{{types}}}, "en-US")',
        "in",
        "    Typed",
    ])
    out += [f"\tpartition {q(table.name)} = m", "\t\tmode: import", "\t\tsource =", _indent(m_code, 4), ""]
    out += ["\tannotation PBI_ResultType = Table", ""]
    return "\n".join(out)


def write_model(root: Path, tables, relationships, measures) -> None:
    sm = root / f"{NAME}.SemanticModel"
    d = sm / "definition"
    (d / "tables").mkdir(parents=True, exist_ok=True)
    dump(sm / ".platform", {"$schema": S["platform"], "metadata": {"type": "SemanticModel", "displayName": NAME},
                            "config": {"version": "2.0", "logicalId": gid("semanticmodel")}})
    dump(sm / "definition.pbism", {"$schema": S["pbism"], "version": "4.0", "settings": {}})
    (d / "database.tmdl").write_text("database\n\tcompatibilityLevel: 1600\n")
    order = json.dumps(["DataFolder"] + [t.name for t in tables], separators=(",", ":"))
    (d / "model.tmdl").write_text("\n".join([
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        f"annotation PBI_QueryOrder = {order}",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        *[f"ref table {q(t.name)}" for t in tables],
        "",
    ]))
    (d / "expressions.tmdl").write_text("\n".join([
        "/// Folder holding the exported CSVs (keep the trailing backslash). "
        "Change it in Transform data > Edit parameters.",
        f'expression DataFolder = "{DEFAULT_DATA_FOLDER}" meta [IsParameterQuery = true, Type = "Text", '
        "IsParameterQueryRequired = true]",
        f"\tlineageTag: {gid('expression/DataFolder')}",
        "",
        "\tannotation PBI_ResultType = Text",
        "",
    ]))
    rel_lines = []
    for ft, fc, tt, tc in relationships:
        rel_lines += [f"relationship {gid(f'rel/{ft}.{fc}->{tt}.{tc}')}",
                      f"\tfromColumn: {q(ft)}.{q(fc)}", f"\ttoColumn: {q(tt)}.{q(tc)}", ""]
    (d / "relationships.tmdl").write_text("\n".join(rel_lines))
    for old in (d / "tables").glob("*.tmdl"):
        old.unlink()
    for t in tables:
        (d / "tables" / f"{t.name}.tmdl").write_text(table_tmdl(t, [m for m in measures if m.table == t.name]))


# ---------------------------------------------------------------- report (PBIR)

def fld(table: str, prop: str, measure: bool = False) -> dict:
    return {"Measure" if measure else "Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": prop}}


def proj(table: str, prop: str, measure: bool = False, name: str | None = None) -> dict:
    p = {"field": fld(table, prop, measure), "queryRef": f"{table}.{prop}", "nativeQueryRef": prop}
    if name:
        p["displayName"] = name
    return p


def lit(value) -> dict:
    v = f"'{value}'" if isinstance(value, str) else ("true" if value is True else "false" if value is False
                                                      else f"{value}D")
    return {"expr": {"Literal": {"Value": v}}}


def visual(name: str, vtype: str, x: int, y: int, w: int, h: int, z: int, roles: dict, title: str | None = None,
           sort: tuple | None = None, objects: dict | None = None) -> dict:
    v = {"visualType": vtype, "query": {"queryState": {r: {"projections": p} for r, p in roles.items()}},
         "drillFilterOtherVisuals": True}
    if sort:
        table, prop, measure, direction = sort
        v["query"]["sortDefinition"] = {"sort": [{"field": fld(table, prop, measure), "direction": direction}],
                                        "isDefaultSort": False}
    if title:
        v["visualContainerObjects"] = {"title": [{"properties": {"show": lit(True), "text": lit(title)}}]}
    if objects:
        v["objects"] = objects
    return {"$schema": S["visual"], "name": name, "position": {"x": x, "y": y, "z": z, "width": w, "height": h,
                                                                 "tabOrder": z}, "visual": v}


F, D, P, C, R, B, PR = "fact_sales", "dim_date", "dim_product", "dim_customer", "cohort_retention", "basket_rules", \
    "product_returns"


def _slicers(prefix: str) -> list[dict]:
    return [
        visual(f"{prefix}_date", "slicer", 16, 16, 300, 64, 1, {"Values": [proj(D, "date")]}, "Date range",
               objects={"data": [{"properties": {"mode": lit("Between")}}]}),
        visual(f"{prefix}_country", "slicer", 332, 16, 260, 64, 2, {"Values": [proj("dim_country", "country")]},
               "Country", objects={"data": [{"properties": {"mode": lit("Dropdown")}}]}),
    ]


def _cards(prefix: str, measures: list[tuple[str, str]], y: int = 96) -> list[dict]:
    w = (1248 - 16 * (len(measures) - 1)) // len(measures)
    return [visual(f"{prefix}_card{i}", "card", 16 + i * (w + 16), y, w, 96, 10 + i,
                   {"Values": [proj(F, m, measure=True)]}, label) for i, (m, label) in enumerate(measures)]


def pages() -> list[tuple[str, str, list[dict]]]:
    return [
        ("overview", "Overview", _slicers("ov") + _cards("ov", [
            ("Revenue", "Net revenue"), ("Orders", "Orders"), ("Customers", "Customers"),
            ("Avg Order Value", "Avg order value"), ("Cancellation Rate", "Cancel rate")]) + [
            visual("ov_trend", "lineChart", 16, 208, 780, 250, 20,
                   {"Category": [proj(D, "month_start")], "Y": [proj(F, "Revenue", True)]}, "Monthly revenue"),
            visual("ov_countries", "clusteredBarChart", 812, 208, 452, 496, 21,
                   {"Category": [proj("dim_country", "country")], "Y": [proj(F, "Revenue", True)]},
                   "Revenue by country", sort=(F, "Revenue", True, "Descending")),
            visual("ov_heat", "pivotTable", 16, 474, 780, 230, 22,
                   {"Rows": [proj(D, "weekday_name")], "Columns": [proj(F, "hour")],
                    "Values": [proj(F, "Orders", True)]}, "Orders by weekday and hour"),
        ]),
        ("products", "Products", _slicers("pr") + [
            visual("pr_top", "clusteredBarChart", 16, 96, 620, 608, 10,
                   {"Category": [proj(P, "description")], "Y": [proj(F, "Revenue", True)]},
                   "Products by revenue", sort=(F, "Revenue", True, "Descending")),
            visual("pr_table", "tableEx", 652, 96, 612, 608, 11,
                   {"Values": [proj(P, "stock_code"), proj(P, "description"), proj(P, "abc_class"),
                               proj(F, "Revenue", True), proj(F, "Units", True), proj(F, "Orders", True),
                               proj(F, "Pareto Cumulative %", True)]},
                   "Product table", sort=(F, "Revenue", True, "Descending")),
        ]),
        ("geography", "Geography", _slicers("ge") + [
            visual("ge_bar", "clusteredBarChart", 16, 96, 520, 608, 10,
                   {"Category": [proj("dim_country", "country")], "Y": [proj(F, "Revenue", True)]},
                   "Revenue by country", sort=(F, "Revenue", True, "Descending")),
            visual("ge_table", "tableEx", 552, 96, 712, 608, 11,
                   {"Values": [proj("dim_country", "country"), proj("dim_country", "region"),
                               proj(F, "Revenue", True), proj(F, "Country Revenue Share", True),
                               proj(F, "Orders", True), proj(F, "Customers", True),
                               proj(F, "Avg Order Value", True)]},
                   "Countries", sort=(F, "Revenue", True, "Descending")),
        ]),
        ("customers", "Customers (RFM)", [
            visual("cu_tree", "treemap", 16, 16, 600, 340, 1,
                   {"Group": [proj(C, "segment")], "Values": [proj(C, "Segment Customers", True)]},
                   "Customers per segment (as of 10 Dec 2011)"),
            visual("cu_share", "clusteredBarChart", 632, 16, 632, 340, 2,
                   {"Category": [proj(C, "segment")], "Y": [proj(C, "Segment Customer Share", True),
                                                          proj(C, "Segment Revenue Share", True)]},
                   "Customer share vs revenue share"),
            visual("cu_table", "tableEx", 16, 372, 1248, 332, 3,
                   {"Values": [proj(C, "segment"), proj(C, "Segment Customers", True),
                               proj(C, "Segment Customer Share", True), proj(C, "Segment Revenue", True),
                               proj(C, "Segment Revenue Share", True)]}, "Segment summary"),
        ]),
        ("cohorts", "Cohort retention", [
            visual("co_matrix", "pivotTable", 16, 16, 1248, 688, 1,
                   {"Rows": [proj(R, "cohort_label")], "Columns": [proj(R, "period")],
                    "Values": [proj(R, "Retention %", True)]}, "Retention % by months since first order"),
        ]),
        ("basket", "Market basket", [
            visual("ba_slicer", "slicer", 16, 16, 400, 64, 1, {"Values": [proj(B, "antecedent_desc")]},
                   "Customers who bought", objects={"data": [{"properties": {"mode": lit("Dropdown")}}]}),
            visual("ba_rules", "tableEx", 16, 96, 1248, 608, 2,
                   {"Values": [proj(B, "antecedent_desc", name="If they bought"),
                               proj(B, "consequent_desc", name="They also bought"),
                               proj(B, "pair_baskets"), proj(B, "support"), proj(B, "confidence"),
                               proj(B, "lift")]},
                   "Association rules (sorted by lift)", sort=(B, "lift", False, "Descending")),
        ]),
        ("returns", "Returns & cancellations", _slicers("re") + _cards("re", [
            ("Gross Sales", "Gross sales"), ("Cancelled Value", "Cancelled value"),
            ("Cancellation Rate", "Cancellation rate"), ("Revenue", "Net revenue")]) + [
            visual("re_trend", "lineChart", 16, 208, 620, 496, 20,
                   {"Category": [proj(D, "month_start")], "Y": [proj(F, "Cancellation Rate", True)]},
                   "Cancellation rate by month"),
            visual("re_products", "tableEx", 652, 208, 612, 496, 21,
                   {"Values": [proj(PR, "description"), proj(PR, "units_sold"), proj(PR, "units_cancelled"),
                               proj(PR, "cancelled_value"), proj(PR, "return_rate")]},
                   "Most-cancelled products", sort=(PR, "cancelled_value", False, "Descending")),
        ]),
    ]


def theme() -> dict:
    return {
        "name": "Retail Analytics",
        "dataColors": PALETTE,
        "foreground": "#0b0b0b", "foregroundNeutralSecondary": "#52514e", "foregroundNeutralTertiary": "#898781",
        "background": "#fcfcfb", "backgroundLight": "#f3f2ee", "backgroundNeutral": "#e1e0d9",
        "tableAccent": "#2a78d6",
        "good": "#0ca30c", "neutral": "#fab219", "bad": "#d03b3b",
        "maximum": "#0d366b", "center": "#6da7ec", "minimum": "#cde2fb",
    }


def write_report(root: Path) -> None:
    rp = root / f"{NAME}.Report"
    if (rp / "definition").exists():
        shutil.rmtree(rp / "definition")  # pages/visuals are regenerated from scratch
    dump(rp / ".platform", {"$schema": S["platform"], "metadata": {"type": "Report", "displayName": NAME},
                            "config": {"version": "2.0", "logicalId": gid("report")}})
    dump(rp / "definition.pbir", {"$schema": S["pbir"], "version": "4.0",
                                  "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    d = rp / "definition"
    dump(d / "version.json", {"$schema": S["version"], "version": "2.0.0"})
    dump(d / "report.json", {
        "$schema": S["report"],
        "themeCollection": {"customTheme": {"name": "RetailTheme.json", "type": "RegisteredResources",
                                            "reportVersionAtImport": {"visual": "2.0.0", "page": "2.0.0",
                                                                      "report": "3.0.0"}}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "RetailTheme.json", "path": "RetailTheme.json",
                                         "type": "CustomTheme"}]}],
    })
    dump(rp / "StaticResources" / "RegisteredResources" / "RetailTheme.json", theme())
    all_pages = pages()
    dump(d / "pages" / "pages.json", {"$schema": S["pages"], "pageOrder": [p[0] for p in all_pages],
                                      "activePageName": all_pages[0][0]})
    for name, display, visuals in all_pages:
        dump(d / "pages" / name / "page.json", {"$schema": S["page"], "name": name, "displayName": display,
                                                "displayOption": "FitToPage", "height": 720, "width": 1280})
        for v in visuals:
            dump(d / "pages" / name / "visuals" / v["name"] / "visual.json", v)


def write_project(root: Path, tables, relationships, measures) -> None:
    dump(root / f"{NAME}.pbip", {"$schema": S["pbip"], "version": "1.0",
                                 "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
                                 "settings": {"enableAutoRecovery": True}})
    write_model(root, tables, relationships, measures)
    write_report(root)
    n_visuals = sum(len(p[2]) for p in pages())
    print(f"  PBIP: {len(tables)} tables, {len(measures)} measures, {len(relationships)} relationships, "
          f"{len(pages())} pages, {n_visuals} visuals")
