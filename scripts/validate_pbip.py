"""Structural checks for the generated Power BI Project (the most that can be done without Power BI).

1. Every JSON file with a "$schema" is validated against Microsoft's published schemas
   (github.com/microsoft/json-schemas, fetched into .cache/json-schemas on first run).
2. The TMDL model is parsed just enough to check that every column/measure referenced by a
   measure, relationship, sortByColumn or report visual exists, and that indentation is tabs.

Usage: python scripts/validate_pbip.py   (exit code 1 on any problem)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

ROOT = Path(__file__).resolve().parents[1]
PBI = ROOT / "powerbi"
SCHEMAS = ROOT / ".cache" / "json-schemas"
SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/"


def ensure_schemas() -> Path:
    fabric = SCHEMAS / "fabric"
    if not fabric.exists():
        SCHEMAS.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                        "https://github.com/microsoft/json-schemas", str(SCHEMAS), "-q"], check=True)
        subprocess.run(["git", "-C", str(SCHEMAS), "sparse-checkout", "set", "--no-cone", "/fabric/"], check=True)
    return fabric


def build_registry(fabric: Path) -> Registry:
    resources = []
    for path in fabric.rglob("*.json"):
        try:
            contents = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(contents, dict):
            continue
        resource = Resource(contents=contents, specification=DRAFT7)
        url = SCHEMA_BASE + path.relative_to(SCHEMAS).as_posix()
        resources.append((url, resource))
        if isinstance(contents.get("$id"), str) and contents["$id"] != url:
            resources.append((contents["$id"], resource))
    return Registry().with_resources(resources)


def validate_json(registry: Registry) -> list[str]:
    problems, checked = [], 0
    files = list(PBI.glob("*.pbip")) + [p for p in PBI.rglob("*") if p.is_file() and (
        p.suffix in {".json", ".pbir", ".pbism"} or p.name == ".platform") and "data" not in p.parts]
    for path in files:
        doc = json.loads(path.read_text())
        url = doc.get("$schema") if isinstance(doc, dict) else None
        if not url:
            continue  # e.g. the custom theme file
        try:
            schema = registry.contents(url)
        except Exception:
            problems.append(f"{path.relative_to(ROOT)}: schema not found locally: {url}")
            continue
        validator = Draft7Validator(schema, registry=registry)
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.path)):
            where = "/".join(str(p) for p in err.absolute_path) or "(root)"
            problems.append(f"{path.relative_to(ROOT)}: {where}: {err.message[:300]}")
        checked += 1
    print(f"JSON schema: {checked} files checked, {len(problems)} problems")
    return problems


def unq(name: str) -> str:
    name = name.strip()
    return name[1:-1].replace("''", "'") if name.startswith("'") and name.endswith("'") else name


def parse_tmdl(model_dir: Path) -> tuple[dict, list[str]]:
    """{table: {"columns": set, "measures": {name: expr}, "sort_by": [(col, target)]}}, problems."""
    tables, problems = {}, []
    for path in sorted((model_dir / "tables").glob("*.tmdl")):
        lines = path.read_text().splitlines()
        table = None
        current_measure = None
        for i, line in enumerate(lines, 1):
            tabs, spaces = re.match(r"^(\t*)( *)", line).groups()
            # Structure is tab-indented; spaces are only legal inside expression bodies (3+ tabs deep).
            if spaces and line.strip() and len(tabs) < 3:
                problems.append(f"{path.name}:{i}: indentation must use tabs")
            depth = len(tabs)
            text = line.strip()
            if not text or text.startswith("///"):
                continue
            if depth == 0 and text.startswith("table "):
                table = unq(text[6:])
                tables[table] = {"columns": set(), "measures": {}, "sort_by": []}
            elif depth == 1 and text.startswith("measure "):
                name, _, expr = text[8:].partition("=")
                current_measure = unq(name)
                tables[table]["measures"][current_measure] = expr.strip()
            elif depth == 1 and text.startswith("column "):
                current_measure = None
                tables[table]["columns"].add(unq(text[7:]))
            elif depth == 1:
                current_measure = None
            elif depth >= 3 and current_measure:
                tables[table]["measures"][current_measure] += "\n" + text
            elif depth == 2 and text.startswith("sortByColumn:"):
                col = unq(text.split(":", 1)[1])
                tables[table]["sort_by"].append(col)
    return tables, problems


def check_model(model_dir: Path) -> tuple[dict, list[str]]:
    tables, problems = parse_tmdl(model_dir)
    measures = {m for t in tables.values() for m in t["measures"]}
    for tname, t in tables.items():
        for target in t["sort_by"]:
            if target not in t["columns"]:
                problems.append(f"{tname}: sortByColumn {target} is not a column")
        for mname, expr in t["measures"].items():
            for ref_table, ref_col in re.findall(r"('?[A-Za-z_][\w ]*'?)\[([^\]]+)\]", expr):
                rt = unq(ref_table)
                if rt not in tables:
                    problems.append(f"measure {mname}: unknown table {rt}")
                elif ref_col not in tables[rt]["columns"]:
                    problems.append(f"measure {mname}: {rt}[{ref_col}] is not a column")
            for bare in re.findall(r"(?<![\w'\]])\[([^\]]+)\]", expr):
                if bare not in measures and not bare.startswith("@"):
                    problems.append(f"measure {mname}: [{bare}] is not a measure")
    rel = (model_dir / "relationships.tmdl").read_text()
    for side, ref in re.findall(r"(fromColumn|toColumn): (\S+)", rel):
        tname, _, col = ref.partition(".")
        if unq(tname) not in tables or unq(col) not in tables[unq(tname)]["columns"]:
            problems.append(f"relationship {side} {ref} does not exist")
    print(f"TMDL: {len(tables)} tables, {sum(len(t['columns']) for t in tables.values())} columns, "
          f"{len(measures)} measures checked, {len(problems)} problems")
    return tables, problems


def check_report_fields(report_dir: Path, tables: dict) -> list[str]:
    problems, n = [], 0
    for path in report_dir.rglob("visual.json"):
        text = path.read_text()
        for kind, entity, prop in re.findall(
                r'"(Column|Measure)": \{\s*"Expression": \{\s*"SourceRef": \{\s*"Entity": "([^"]+)"\s*\}\s*\},'
                r'\s*"Property": "([^"]+)"', text):
            n += 1
            t = tables.get(entity)
            if t is None:
                problems.append(f"{path.parent.name}: unknown table {entity}")
            elif kind == "Column" and prop not in t["columns"]:
                problems.append(f"{path.parent.name}: {entity}[{prop}] is not a column")
            elif kind == "Measure" and prop not in t["measures"]:
                problems.append(f"{path.parent.name}: [{prop}] is not a measure on {entity}")
    print(f"Report fields: {n} references checked, {len(problems)} problems")
    return problems


def main() -> int:
    registry = build_registry(ensure_schemas())
    problems = validate_json(registry)
    tables, model_problems = check_model(PBI / "RetailAnalytics.SemanticModel" / "definition")
    problems += model_problems + check_report_fields(PBI / "RetailAnalytics.Report", tables)
    for p in problems:
        print("  -", p)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
