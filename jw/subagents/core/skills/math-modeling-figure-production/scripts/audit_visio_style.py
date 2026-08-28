#!/usr/bin/env python3
"""Inventory VSDX fonts, line weights and named text-box geometry without editing."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Sequence
from xml.etree import ElementTree as ET


VISIO_NS = "http://schemas.microsoft.com/office/visio/2012/main"
NS = {"v": VISIO_NS}
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
LATIN_DIGIT_RE = re.compile(r"[A-Za-z0-9]")
PAGE_XML_RE = re.compile(r"^visio/pages/page\d+\.xml$")


def _name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _character_fonts(shape: ET.Element) -> dict[int, str]:
    fonts: dict[int, str] = {}
    for row in shape.findall("./v:Section[@N='Character']/v:Row", NS):
        index = int(row.get("IX", "0"))
        cell = row.find("./v:Cell[@N='Font']", NS)
        if cell is not None and cell.get("V"):
            fonts[index] = str(cell.get("V"))
    return fonts


def _text_runs(text: ET.Element) -> list[tuple[int, str]]:
    current = 0
    pieces: list[tuple[int, str]] = []
    if text.text:
        pieces.append((current, text.text))
    for element in text.iter():
        if element is text:
            continue
        if _name(element) == "cp":
            current = int(element.get("IX", "0"))
        if element.tail:
            pieces.append((current, element.tail))
    merged: list[tuple[int, str]] = []
    for index, value in pieces:
        if not value:
            continue
        if merged and merged[-1][0] == index:
            merged[-1] = (index, merged[-1][1] + value)
        else:
            merged.append((index, value))
    return merged


def _shape_label(shape: ET.Element) -> str:
    return str(shape.get("NameU") or shape.get("Name") or shape.get("ID") or "unknown")


def _direct_cells(shape: ET.Element) -> dict[str, ET.Element]:
    return {
        str(cell.get("N")): cell
        for cell in shape.findall("./v:Cell", NS)
        if cell.get("N")
    }


def _float_cell(cells: dict[str, ET.Element], name: str) -> float | None:
    cell = cells.get(name)
    if cell is None or not cell.get("V"):
        return None
    try:
        return float(str(cell.get("V")))
    except ValueError:
        return None


def _formula_cell(cells: dict[str, ET.Element], name: str) -> str | None:
    cell = cells.get(name)
    if cell is None:
        return None
    value = cell.get("F")
    return str(value) if value else None


def _horizontal_alignment(shape: ET.Element) -> int | None:
    cell = shape.find("./v:Section[@N='Paragraph']/v:Row/v:Cell[@N='HorzAlign']", NS)
    if cell is None or not cell.get("V"):
        return None
    try:
        return int(round(float(str(cell.get("V")))))
    except ValueError:
        return None


def audit(
    path: Path,
    *,
    max_line_pt: float | None = None,
    chinese_font: str = "SimSun",
    latin_font: str = "Times New Roman",
    content_fit_name_patterns: Sequence[str] = (),
) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    line_weights: list[float] = []
    pages = 0
    shapes = 0
    text_runs = 0
    text_geometry: list[dict[str, object]] = []
    fit_patterns = [re.compile(pattern) for pattern in content_fit_name_patterns]

    with zipfile.ZipFile(path) as archive:
        page_names = sorted(
            name for name in archive.namelist() if PAGE_XML_RE.fullmatch(name)
        )
        for page_name in page_names:
            pages += 1
            root = ET.fromstring(archive.read(page_name))
            for shape in root.findall(".//v:Shape", NS):
                shapes += 1
                label = _shape_label(shape)

                line_cell = shape.find("./v:Cell[@N='LineWeight']", NS)
                if line_cell is not None and line_cell.get("V"):
                    try:
                        points = round(float(line_cell.get("V")) * 72.0, 6)
                    except ValueError:
                        findings.append(
                            {
                                "code": "line_weight_unresolved",
                                "page": page_name,
                                "shape": label,
                                "value": line_cell.get("V"),
                            }
                        )
                    else:
                        line_weights.append(points)
                        if max_line_pt is not None and points > max_line_pt + 1e-9:
                            findings.append(
                                {
                                    "code": "line_too_heavy",
                                    "page": page_name,
                                    "shape": label,
                                    "line_weight_pt": points,
                                    "maximum_pt": max_line_pt,
                                }
                            )

                fonts = _character_fonts(shape)
                text = shape.find("./v:Text", NS)
                if text is None:
                    continue
                content = "".join(text.itertext()).strip()
                cells = _direct_cells(shape)
                width_formula = _formula_cell(cells, "Width")
                height_formula = _formula_cell(cells, "Height")
                content_fitted = bool(
                    width_formula and "TEXTWIDTH(" in width_formula.upper()
                )
                geometry = {
                    "page": page_name,
                    "shape": label,
                    "text": content,
                    "width_in": _float_cell(cells, "Width"),
                    "height_in": _float_cell(cells, "Height"),
                    "pin_x_in": _float_cell(cells, "PinX"),
                    "pin_y_in": _float_cell(cells, "PinY"),
                    "angle_rad": _float_cell(cells, "Angle"),
                    "width_formula": width_formula,
                    "height_formula": height_formula,
                    "horizontal_alignment": _horizontal_alignment(shape),
                    "content_fitted": content_fitted,
                }
                text_geometry.append(geometry)
                if (
                    content
                    and any(pattern.search(label) for pattern in fit_patterns)
                    and not content_fitted
                ):
                    findings.append(
                        {
                            "code": "text_box_not_content_fitted",
                            "page": page_name,
                            "shape": label,
                            "text": content,
                            "width_formula": width_formula,
                            "detail": "name-matched title must size Width from TEXTWIDTH(TheText)",
                        }
                    )
                for index, value in _text_runs(text):
                    if not value.strip():
                        continue
                    text_runs += 1
                    font = fonts.get(index)
                    where = {
                        "page": page_name,
                        "shape": label,
                        "character_row": index,
                        "text": value.strip(),
                        "font": font,
                    }
                    if CJK_RE.search(value) and font != chinese_font:
                        findings.append(
                            {"code": "cjk_font", "expected": chinese_font, **where}
                        )
                    if LATIN_DIGIT_RE.search(value) and font != latin_font:
                        findings.append(
                            {
                                "code": "latin_digit_font",
                                "expected": latin_font,
                                **where,
                            }
                        )

    summary = {
        "count": len(line_weights),
        "minimum": min(line_weights) if line_weights else None,
        "maximum": max(line_weights) if line_weights else None,
        "unique": sorted(set(line_weights)),
    }
    return {
        "file": str(path),
        "pages": pages,
        "shapes": shapes,
        "text_runs": text_runs,
        "text_geometry": text_geometry,
        "line_weights_pt": summary,
        "findings": findings,
        "passed": not findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vsdx", type=Path)
    parser.add_argument("--max-line-pt", type=float)
    parser.add_argument("--chinese-font", default="SimSun")
    parser.add_argument("--latin-font", default="Times New Roman")
    parser.add_argument(
        "--require-content-fit-pattern",
        action="append",
        default=[],
        help="Regex for named text shapes whose Width formula must use TEXTWIDTH(TheText); repeatable",
    )
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args()

    result = audit(
        args.vsdx,
        max_line_pt=args.max_line_pt,
        chinese_font=args.chinese_font,
        latin_font=args.latin_font,
        content_fit_name_patterns=args.require_content_fit_pattern,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_findings and result["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
