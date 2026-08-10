"""Excel (.xlsx) inspection and sanitizing.

SpreadsheetML shares the OPC package, so ``docProps`` comes free from
:mod:`.opc`. What is specific to workbooks is that a spreadsheet keeps *copies*
of data you thought you removed:

- **Pivot caches** (``xl/pivotCache/*``) store a full snapshot of the source
  range. Deleting the source sheet does not delete the cache, so a workbook
  can hand over every row behind a summary table.
- **External links** (``xl/externalLinks/*``) name other workbooks by path --
  routinely revealing internal server names and directory structure -- and
  cache their last-read values.
- **Hidden sheets, rows and columns** hide data by display only.
- **Threaded comments** carry commenter identities in ``xl/persons.xml``.

Hidden sheets are **reported, not removed**, which differs deliberately from
the way :mod:`.pptx` removes hidden slides. Slides are independent; worksheets
are not -- formulas, charts, defined names and pivot sources reference sheets
by name, so deleting one can silently turn a workbook into a field of
``#REF!`` errors. Naming it and letting the author decide is the only safe
behaviour.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from latextify.ingest._xml import hardened_xml_parser

from . import opc
from .report import Finding

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

_WORKBOOK_PART = "xl/workbook.xml"
_PIVOT_RE = re.compile(r"^xl/pivotCache/[^/]+$")
_EXTERNAL_RE = re.compile(r"^xl/externalLinks/(?!_rels/).+$")
_COMMENTS_RE = re.compile(r"^xl/(threadedComments/)?[^/]*comments[^/]*\.xml$")
_PERSONS_PART = "xl/persons.xml"
_SHEET_RE = re.compile(r"^xl/worksheets/sheet\d+\.xml$")


def _parse(data: bytes) -> etree._Element | None:
    try:
        return etree.fromstring(data, parser=hardened_xml_parser())
    except etree.XMLSyntaxError:
        return None


def _hidden_sheets(zin: zipfile.ZipFile, names: set[str]) -> list[str]:
    if _WORKBOOK_PART not in names:
        return []
    root = _parse(zin.read(_WORKBOOK_PART))
    if root is None:
        return []
    hidden = []
    for sheet in root.iter(f"{{{S}}}sheet"):
        if sheet.get("state") in ("hidden", "veryHidden"):
            label = sheet.get("name", "?")
            if sheet.get("state") == "veryHidden":
                label += " (very hidden -- not listed in Excel's Unhide dialog)"
            hidden.append(label)
    return hidden


def _hidden_rows_cols(zin: zipfile.ZipFile, names: set[str]) -> tuple[int, int]:
    rows = cols = 0
    for name in sorted(n for n in names if _SHEET_RE.match(n)):
        root = _parse(zin.read(name))
        if root is None:
            continue
        rows += sum(1 for r in root.iter(f"{{{S}}}row") if r.get("hidden") == "1")
        cols += sum(1 for c in root.iter(f"{{{S}}}col") if c.get("hidden") == "1")
    return rows, cols


def _defined_names(zin: zipfile.ZipFile, names: set[str]) -> int:
    if _WORKBOOK_PART not in names:
        return 0
    root = _parse(zin.read(_WORKBOOK_PART))
    if root is None:
        return 0
    return sum(1 for _ in root.iter(f"{{{S}}}definedName"))


def _leak_parts(names: set[str]) -> dict[str, list[str]]:
    return {
        "pivot": sorted(n for n in names if _PIVOT_RE.match(n)),
        "external": sorted(n for n in names if _EXTERNAL_RE.match(n)),
        "comments": sorted(
            [n for n in names if _COMMENTS_RE.match(n)]
            + ([_PERSONS_PART] if _PERSONS_PART in names else [])
        ),
    }


def _open(path: Path) -> zipfile.ZipFile:
    opc.validate_opc_archive(path, ".xlsx")
    zin = zipfile.ZipFile(path)
    if _WORKBOOK_PART not in set(zin.namelist()):
        zin.close()
        raise ValueError(f"{path}: not a valid .xlsx (missing {_WORKBOOK_PART})")
    return zin


def inspect(path: Path) -> tuple[list[Finding], list[str]]:
    """Report what a ``.xlsx`` carries, without modifying it."""
    findings: list[Finding] = []
    warnings: list[str] = []

    with _open(path) as zin:
        names = set(zin.namelist())
        findings.extend(opc.docprops_findings(zin))
        parts = _leak_parts(names)

        if parts["pivot"]:
            findings.append(
                Finding(
                    category="pivot-cache",
                    severity="high",
                    summary=f"{len(parts['pivot'])} pivot cache part(s)",
                    detail=(
                        "A pivot cache stores a full copy of its source range. It "
                        "survives deletion of the source sheet, so the underlying "
                        "rows can still be recovered from the workbook."
                    ),
                    location=parts["pivot"][0],
                    count=len(parts["pivot"]),
                )
            )

        if parts["external"]:
            findings.append(
                Finding(
                    category="external-link",
                    severity="high",
                    summary=f"{len(parts['external'])} external workbook link(s)",
                    detail=(
                        "External links record the full path of the workbooks they "
                        "point at, revealing internal server names and folder "
                        "structure, and cache their last-read values."
                    ),
                    location=parts["external"][0],
                    count=len(parts["external"]),
                )
            )

        if parts["comments"]:
            findings.append(
                Finding(
                    category="comments",
                    severity="medium",
                    summary=f"{len(parts['comments'])} comment/author part(s)",
                    detail="Cell comments and threaded-comment author identities are "
                    "stored in the workbook.",
                    location=parts["comments"][0],
                    count=len(parts["comments"]),
                )
            )

        hidden = _hidden_sheets(zin, names)
        if hidden:
            findings.append(
                Finding(
                    category="hidden-sheet",
                    severity="high",
                    summary=f"{len(hidden)} hidden sheet(s): {', '.join(hidden[:3])}",
                    detail=(
                        "Hidden worksheets are fully present and readable. They are "
                        "reported rather than removed: formulas, charts and defined "
                        "names reference sheets by name, so deleting one can break "
                        "the workbook."
                    ),
                    location=_WORKBOOK_PART,
                    count=len(hidden),
                    removable=False,
                )
            )
            warnings.append(
                f"{len(hidden)} hidden sheet(s) found but NOT removed: "
                f"{', '.join(hidden[:3])}. Delete them in Excel if they are not needed."
            )

        rows, cols = _hidden_rows_cols(zin, names)
        if rows or cols:
            findings.append(
                Finding(
                    category="hidden-cells",
                    severity="medium",
                    summary=f"{rows} hidden row(s) and {cols} hidden column group(s)",
                    detail="Hidden rows and columns still contain their values and "
                    "are revealed by a single Unhide.",
                    location="xl/worksheets/",
                    count=rows + cols,
                    removable=False,
                )
            )

        defined = _defined_names(zin, names)
        if defined:
            findings.append(
                Finding(
                    category="defined-names",
                    severity="low",
                    summary=f"{defined} defined name(s)",
                    detail="Defined names can retain references to removed ranges and "
                    "external files.",
                    location=_WORKBOOK_PART,
                    count=defined,
                )
            )

    return findings, warnings


def sanitize(src: Path, dest: Path, **_options: object) -> tuple[list[Finding], list[str]]:
    """Write a scrubbed copy of ``src`` to ``dest``.

    Removes docProps, pivot caches, external links and comments. Hidden
    sheets/rows/columns are reported as warnings, never deleted -- see the
    module docstring for why.
    """
    removed: list[Finding] = []
    warnings: list[str] = []

    with _open(src) as zin:
        names = set(zin.namelist())
        parts_to_remove: set[str] = set()

        docprops = opc.docprops_parts_present(names)
        if docprops:
            parts_to_remove |= docprops
            removed.append(
                Finding(
                    category="docprops",
                    severity="high",
                    summary=f"Stripped {len(docprops)} document-properties part(s)",
                    detail="Author, company, editing time and custom properties were "
                    "removed.",
                    location="docProps/",
                    count=len(docprops),
                )
            )

        parts = _leak_parts(names)
        for key, category, severity, summary, detail in (
            (
                "pivot",
                "pivot-cache",
                "high",
                "pivot cache part(s)",
                "Cached copies of pivot source data were removed; pivot tables will "
                "need a refresh against live data to work again.",
            ),
            (
                "external",
                "external-link",
                "high",
                "external workbook link(s)",
                "Links naming other workbooks and their cached values were removed.",
            ),
            (
                "comments",
                "comments",
                "medium",
                "comment/author part(s)",
                "Cell comments and commenter identities were deleted.",
            ),
        ):
            found = parts[key]
            if not found:
                continue
            parts_to_remove |= set(found)
            removed.append(
                Finding(
                    category=category,
                    severity=severity,
                    summary=f"Removed {len(found)} {summary}",
                    detail=detail,
                    location=found[0],
                    count=len(found),
                )
            )

        hidden = _hidden_sheets(zin, names)
        if hidden:
            warnings.append(
                f"{len(hidden)} hidden sheet(s) were NOT removed and are still in the "
                f"cleaned file: {', '.join(hidden[:3])}."
            )
        rows, cols = _hidden_rows_cols(zin, names)
        if rows or cols:
            warnings.append(
                f"{rows} hidden row(s) and {cols} hidden column group(s) were NOT "
                "removed; their values remain in the cleaned file."
            )

        opc.rewrite_package(zin, dest, parts_to_remove=parts_to_remove)

    return removed, warnings
