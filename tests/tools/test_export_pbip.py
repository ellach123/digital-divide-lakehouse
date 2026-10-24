from pathlib import Path

import pytest

from tools.export_pbip import (
    ExportError,
    build,
    export_pbip,
    parameter_values,
    replace_parameters,
    scan,
    select_files,
)

# Fake values only. Host and token are assembled at runtime so the secret scanners in CI do
# not match these lines.
HOST = "dbc-0000aaaa-1111" + ".cloud.databricks.com"
PATH = "/sql/1.0/warehouses/" + "0123456789abcdef"
TOKEN = "dapi" + "0" * 32
TAG = "11111111-2222-3333-4444-555555555555"

EXPRESSIONS = f"""expression DatabricksHost = "{HOST}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]
\tlineageTag: {TAG}

\tannotation PBI_ResultType = Text

expression HttpPath = "{PATH}" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]
\tlineageTag: {TAG}

\tannotation PBI_ResultType = Text
"""  # noqa: E501

TABLE = f"""table dim_demo
\tlineageTag: {TAG}

\tcolumn code
\t\tdataType: string
\t\tlineageTag: {TAG}
\t\tsourceColumn: code

\tpartition dim_demo = m
\t\tmode: import
\t\tsource =
\t\t\t\tlet
\t\t\t\t  Source = DatabricksMultiCloud.Catalogs(DatabricksHost, HttpPath, [])
\t\t\t\tin
\t\t\t\t  Source
"""


def make_project(root: Path, name: str = "demo") -> Path:
    files = {
        f"{name}.pbip": '{"version": "1.0"}',
        ".gitignore": "**/.pbi/localSettings.json\n",
        f"{name}.Report/.platform": f'{{\n  "config": {{\n    "logicalId": "{TAG}"\n  }}\n}}',
        f"{name}.Report/definition.pbir": '{"version": "4.0"}',
        f"{name}.Report/definition/report.json": '{"pages": []}',
        f"{name}.Report/.pbi/localSettings.json": '{"user": "someone"}',
        f"{name}.Report/StaticResources/SharedResources/BaseThemes/theme.json": "{}",
        f"{name}.SemanticModel/.platform": f'{{\n    "logicalId": "{TAG}"\n}}',
        f"{name}.SemanticModel/definition.pbism": '{"version": "4.2"}',
        f"{name}.SemanticModel/diagramLayout.json": f'{{\n  "nodeLineageTag": "{TAG}"\n}}',
        f"{name}.SemanticModel/definition/expressions.tmdl": EXPRESSIONS,
        f"{name}.SemanticModel/definition/relationships.tmdl": (
            f"relationship {TAG}\n\tfromColumn: a.b\n\ttoColumn: c.d\n"
        ),
        f"{name}.SemanticModel/definition/tables/dim_demo.tmdl": TABLE,
        f"{name}.SemanticModel/.pbi/cache.abf": "binary cache",
        f"{name}.SemanticModel/.pbi/editorSettings.json": "{}",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


def test_select_files_keeps_allowlist_and_drops_local_files(tmp_path):
    keep, skip = select_files(make_project(tmp_path), "demo")
    assert "demo.SemanticModel/definition/tables/dim_demo.tmdl" in keep
    assert "demo.Report/StaticResources/SharedResources/BaseThemes/theme.json" in keep
    assert sorted(skip) == [
        ".gitignore",
        "demo.Report/.pbi/localSettings.json",
        "demo.SemanticModel/.pbi/cache.abf",
        "demo.SemanticModel/.pbi/editorSettings.json",
    ]


def test_unknown_new_file_is_skipped_by_default(tmp_path):
    src = make_project(tmp_path)
    (src / "demo.SemanticModel" / "newLocalState.json").write_text("{}")
    _, skip = select_files(src, "demo")
    assert "demo.SemanticModel/newLocalState.json" in skip


def test_parameters_are_read_and_replaced():
    values = parameter_values(EXPRESSIONS)
    assert values == {"DatabricksHost": HOST, "HttpPath": PATH}
    out = replace_parameters(EXPRESSIONS, values)
    assert 'expression DatabricksHost = "<databricks-host>" meta' in out
    assert 'expression HttpPath = "<warehouse-http-path>" meta' in out
    assert HOST not in out and PATH not in out


def test_missing_parameter_is_an_error():
    with pytest.raises(ExportError, match="HttpPath"):
        parameter_values(EXPRESSIONS.split("expression HttpPath")[0])


@pytest.mark.parametrize(
    ("text", "finding"),
    [
        (f"Source = DatabricksMultiCloud.Catalogs(\"{HOST}\")", "workspace host"),
        (f'"{PATH}"', "warehouse path"),
        (f"token {TOKEN}", "Databricks token"),
        ("owner: someone@example.org", "e-mail"),
        (f"\t\tannotation TenantId = {TAG}", "GUID"),
    ],
)
def test_scan_finds_sensitive_content(text, finding):
    assert finding in scan(text, [])


def test_scan_allows_power_bi_generated_ids():
    text = (
        f"\tlineageTag: {TAG}\n\t\tsourceLineageTag: {TAG}\nrelationship {TAG}\n"
        f'    "logicalId": "{TAG}",\n  "nodeLineageTag": "{TAG}"\n'
    )
    assert scan(text, []) == []


def test_scan_reports_parameter_values_without_echoing_them():
    findings = scan("value: my-private-host", ["my-private-host"])
    assert findings == ["parameter value #1"]


def test_build_writes_clean_export(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    name, _ = build(make_project(tmp_path / "src"), out)
    assert name == "demo"
    files = [p for p in out.rglob("*") if p.is_file()]
    assert len(files) == 11
    assert not any(".pbi" in p.parts for p in files)
    for path in files:
        assert scan(path.read_text(encoding="utf-8"), [HOST, PATH]) == []


def test_build_aborts_when_a_value_leaks_elsewhere(tmp_path):
    src = make_project(tmp_path / "src")
    table = src / "demo.SemanticModel/definition/tables/dim_demo.tmdl"
    table.write_text(TABLE.replace("DatabricksHost, HttpPath", f'"{HOST}", HttpPath'))
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ExportError, match="dim_demo.tmdl"):
        build(src, out)


def test_export_replaces_target_and_is_reproducible(tmp_path):
    src = make_project(tmp_path / "src")
    root = tmp_path / "reporting"
    stale = root / "demo" / "old_file.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")

    target, _ = export_pbip(src, root)
    first = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    export_pbip(src, root)
    second = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}

    assert not stale.exists()
    assert first == second
    assert sorted(p.name for p in root.iterdir()) == ["demo"]  # no staging folder left


def test_failed_export_keeps_previous_target(tmp_path):
    src = make_project(tmp_path / "src")
    root = tmp_path / "reporting"
    target, _ = export_pbip(src, root)
    (src / "demo.Report/definition/report.json").write_text(f'{{"note": "{TOKEN}"}}')
    with pytest.raises(ExportError):
        export_pbip(src, root)
    assert (target / "demo.Report/definition/report.json").read_text() == '{"pages": []}'
