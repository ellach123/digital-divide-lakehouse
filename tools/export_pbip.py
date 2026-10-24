"""Export the Power BI project (PBIP) from its working folder into the public repo.

The report is edited in Power BI Desktop in a folder outside the repo (the working copy holds
the real connection values, the local data cache and per-user settings). This tool copies a
publishable version into reporting/powerbi/<name>/:

1. Copy only an allowlist of PBIP files: the .pbip, both definition folders, the .platform
   and definition.pbir/.pbism files, diagramLayout.json and the report's StaticResources.
   Every .pbi/ folder (cache.abf, localSettings.json, editorSettings.json) and anything
   Desktop adds in future stays behind.
2. Read the connection parameters from expressions.tmdl and replace their values with
   placeholders. The values are read from the working copy, never written in this file.
3. Scan every exported file for e-mails, workspace hosts, warehouse paths, Databricks
   tokens, SAS signatures, storage endpoints, the parameter values themselves, and GUIDs
   outside the places where Power BI writes its own random ids (lineageTag, relationship
   names, logicalId). Any hit aborts and nothing is written.
4. Replace the target folder only after the scan passed.

    python -m tools.export_pbip D:\\pbi-work\\ddlake-report

After cloning, open the .pbip in Power BI Desktop and set DatabricksHost and HttpPath under
Transform data > Edit parameters, then refresh.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

from tools.export_notebook_snapshot import GENERIC_PATTERNS

REPO = Path(__file__).resolve().parent.parent
TARGET_ROOT = REPO / "reporting" / "powerbi"

PLACEHOLDERS = {
    "DatabricksHost": "<databricks-host>",
    "HttpPath": "<warehouse-http-path>",
}
EXPRESSIONS = "{name}.SemanticModel/definition/expressions.tmdl"

# Relative paths (POSIX) allowed in the export; {name} is the project name.
ALLOWLIST = [
    r"{name}\.pbip",
    r"{name}\.Report/\.platform",
    r"{name}\.Report/definition\.pbir",
    r"{name}\.Report/definition/.+",
    r"{name}\.Report/StaticResources/.+",
    r"{name}\.SemanticModel/\.platform",
    r"{name}\.SemanticModel/definition\.pbism",
    r"{name}\.SemanticModel/definition/.+",
    r"{name}\.SemanticModel/diagramLayout\.json",
]

PATTERNS = {
    **{k: v for k, v in GENERIC_PATTERNS.items() if k != "GUID"},
    "warehouse path": r"/sql/1\.0/warehouses/[0-9a-fA-F]+",
    "Databricks token": r"\bdapi[0-9a-fA-F]{32}",
}
GUID = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
# Lines where Power BI writes random ids it generates itself; a GUID anywhere else
# (e.g. a tenant or subscription id pasted into a query) is a finding.
GUID_OK_LINE = re.compile(
    r'^\s*(?:lineageTag:|sourceLineageTag:|relationship \S+$|"logicalId":|"nodeLineageTag":)'
)


class ExportError(RuntimeError):
    pass


def project_name(source: Path) -> str:
    pbips = sorted(source.glob("*.pbip"))
    if len(pbips) != 1:
        raise ExportError(f"expected one .pbip in {source}, found {len(pbips)}")
    return pbips[0].stem


def select_files(source: Path, name: str) -> tuple[list[str], list[str]]:
    """Split the working folder into allowlisted files and skipped files (relative paths)."""
    allowed = [re.compile(p.format(name=re.escape(name))) for p in ALLOWLIST]
    keep, skip = [], []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = path.relative_to(source).as_posix()
        if "/.pbi/" not in f"/{rel}" and any(a.fullmatch(rel) for a in allowed):
            keep.append(rel)
        else:
            skip.append(rel)
    return keep, skip


def parameter_values(expressions: str) -> dict[str, str]:
    values = {}
    for param in PLACEHOLDERS:
        found = re.findall(
            rf'^expression {param} = "([^"]*)" meta \[[^\]]*IsParameterQuery=true',
            expressions, flags=re.MULTILINE,
        )
        if len(found) != 1:
            raise ExportError(f"parameter {param} not found exactly once in expressions.tmdl")
        values[param] = found[0]
    return values


def replace_parameters(expressions: str, values: dict[str, str]) -> str:
    for param, value in values.items():
        expressions = expressions.replace(
            f'expression {param} = "{value}"', f'expression {param} = "{PLACEHOLDERS[param]}"'
        )
    return expressions


def scan(text: str, sensitive_values: list[str]) -> list[str]:
    findings = [name for name, pattern in PATTERNS.items() if re.search(pattern, text)]
    if any(GUID.search(line) and not GUID_OK_LINE.match(line) for line in text.splitlines()):
        findings.append("GUID")
    findings += [
        "parameter value #" + str(i)  # never echo the value itself
        for i, value in enumerate(sensitive_values, 1)
        if value and value.lower() in text.lower()
    ]
    return findings


def build(source: Path, out: Path) -> tuple[str, list[str]]:
    """Write the cleaned export to out (an empty folder); return project name and skips."""
    name = project_name(source)
    keep, skip = select_files(source, name)
    expressions_rel = EXPRESSIONS.format(name=name)
    if expressions_rel not in keep:
        raise ExportError(f"{expressions_rel} missing; are the connection parameters set up?")

    values = parameter_values((source / expressions_rel).read_text(encoding="utf-8"))
    problems = []
    for rel in keep:
        data = (source / rel).read_bytes()
        if rel == expressions_rel:
            data = replace_parameters(data.decode("utf-8"), values).encode("utf-8")
        # latin-1 maps every byte, so binary resources are scanned too
        findings = scan(data.decode("latin-1"), list(values.values()))
        if findings:
            problems.append(f"{rel}: {findings}")
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if problems:
        raise ExportError("sensitive content found, nothing written:\n  " + "\n  ".join(problems))
    return name, skip


def export_pbip(source: Path, target_root: Path) -> tuple[Path, list[str]]:
    target_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_root) as tmp:
        staged = Path(tmp) / "export"
        staged.mkdir()
        name, skip = build(source, staged)
        target = target_root / name
        if target.exists():
            shutil.rmtree(target)
        staged.rename(target)
    return target, skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.export_pbip", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="working folder that holds the .pbip")
    args = parser.parse_args(argv)

    if REPO in args.source.resolve().parents:
        parser.error("source must be the working copy outside the repo")
    try:
        target, skip = export_pbip(args.source, TARGET_ROOT)
    except ExportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    files = sum(1 for p in target.rglob("*") if p.is_file())
    print(f"wrote {target.relative_to(REPO).as_posix()} ({files} files)")
    for rel in skip:
        print(f"  skipped {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
