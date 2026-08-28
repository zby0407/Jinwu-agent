#!/usr/bin/env python3
"""Validate a structured mathematical-modeling figure contract."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def half_point(value: float) -> bool:
    return 9.0 <= value <= 12.0 and math.isclose(
        value * 2, round(value * 2), abs_tol=1e-8
    )


def add(findings: list[dict[str, object]], code: str, where: str, detail: str) -> None:
    findings.append({"code": code, "where": where, "detail": detail})


def validate(data: dict[str, object]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for key in (
        "figure_id",
        "purpose",
        "data_source",
        "editable_source",
        "route",
        "panels",
        "gates",
    ):
        if not data.get(key):
            add(findings, "missing_required", "$", key)

    typography = data.get("typography", {})
    if isinstance(typography, dict):
        for name, raw in typography.get("sizes_pt", {}).items():
            value = float(raw)
            if not half_point(value):
                add(
                    findings,
                    "invalid_font_size",
                    f"typography.sizes_pt.{name}",
                    str(value),
                )
        if typography.get("latin_font") != "Times New Roman":
            add(
                findings,
                "latin_font",
                "typography.latin_font",
                "expected Times New Roman",
            )
        if typography.get("chinese_font") != "SimSun":
            add(findings, "chinese_font", "typography.chinese_font", "expected SimSun")

    route = str(data.get("route", "")).lower()
    origin_visio_route = "origin" in route and "visio" in route
    if origin_visio_route and not data.get("figure_class"):
        add(
            findings,
            "missing_figure_class",
            "figure_class",
            "declare data_chart for Origin-to-Visio statistical figures",
        )
    origin_visio_data_chart = origin_visio_route and data.get("figure_class") in (
        None,
        "data_chart",
    )
    if origin_visio_data_chart:
        if data.get("final_app") != "Visio":
            add(
                findings,
                "final_app",
                "final_app",
                "Origin-to-Visio chart must be finalized in Visio",
            )
        if (
            not isinstance(typography, dict)
            or typography.get("mixed_runs_verified") is not True
        ):
            add(
                findings,
                "mixed_runs_unverified",
                "typography",
                "verify Chinese and Latin/digit character runs",
            )

        line_style = data.get("line_style", {})
        if (
            not isinstance(line_style, dict)
            or line_style.get("normalized_in_final_app") is not True
        ):
            add(
                findings,
                "final_line_style_unverified",
                "line_style",
                "normalize imported line weights in Visio",
            )
        if (
            not isinstance(line_style, dict)
            or line_style.get("visual_hierarchy_checked") is not True
        ):
            add(
                findings,
                "line_hierarchy_unverified",
                "line_style",
                "inspect line hierarchy at paper size",
            )

        axis_spacing = data.get("axis_spacing", {})
        if (
            not isinstance(axis_spacing, dict)
            or axis_spacing.get("tick_labels_outside_plot") is not True
        ):
            add(
                findings,
                "tick_labels_inside_plot",
                "axis_spacing",
                "ordinary tick labels must remain outside the plotting region",
            )
        if (
            not isinstance(axis_spacing, dict)
            or axis_spacing.get("axis_titles_clear_of_tick_labels") is not True
        ):
            add(
                findings,
                "axis_title_spacing_unverified",
                "axis_spacing",
                "keep axis titles clear of tick labels",
            )
        if (
            not isinstance(axis_spacing, dict)
            or axis_spacing.get("checked_at_insertion_width") is not True
        ):
            add(
                findings,
                "axis_spacing_not_inspected",
                "axis_spacing",
                "inspect spacing at actual paper width",
            )

        geometry = data.get("geometry_evidence", {})
        required_measurements = {
            "text_box_fit_measured": "text_box_fit_unmeasured",
            "plot_bbox_centering_measured": "plot_bbox_centering_unmeasured",
            "axis_title_clearance_measured": "axis_clearance_unmeasured",
            "page_content_bbox_inspected": "page_bbox_uninspected",
        }
        for field, code in required_measurements.items():
            if not isinstance(geometry, dict) or geometry.get(field) is not True:
                add(
                    findings,
                    code,
                    f"geometry_evidence.{field}",
                    "record measured native-layout geometry",
                )
        required_receipts = {
            "vsdx_audit_receipt": "geometry_receipt_missing",
            "standalone_preview": "standalone_preview_missing",
            "paper_page_preview": "paper_preview_missing",
        }
        for field, code in required_receipts.items():
            if (
                not isinstance(geometry, dict)
                or not str(geometry.get(field, "")).strip()
            ):
                add(
                    findings,
                    code,
                    f"geometry_evidence.{field}",
                    "record the evidence artifact",
                )

    gates = data.get("gates", {})
    if isinstance(gates, dict):
        ordered = ("meaning", "coordinates", "layout", "insertion")
        seen_incomplete = False
        for gate in ordered:
            complete = gates.get(gate) is True
            if seen_incomplete and complete:
                add(
                    findings,
                    "gate_order",
                    f"gates.{gate}",
                    "later gate cannot pass before an earlier gate",
                )
            if not complete:
                seen_incomplete = True

    export = data.get("export")
    if isinstance(export, dict) and export.get("completed"):
        if float(export.get("dpi", 0)) < 600:
            add(
                findings,
                "export_resolution",
                "export.dpi",
                "formal export must be at least 600 dpi",
            )
    insertion = data.get("paper_insertion")
    if (
        isinstance(insertion, dict)
        and insertion.get("completed")
        and not insertion.get("actual_page_inspected")
    ):
        add(
            findings,
            "insertion_not_inspected",
            "paper_insertion",
            "inspect the rendered paper page at actual width",
        )

    panels = data.get("panels", [])
    if not isinstance(panels, list):
        add(findings, "invalid_panels", "panels", "must be a list")
        return findings

    layout = data.get("layout", {})
    if isinstance(layout, dict) and layout.get("arrangement") == "1x3":
        ratios = [
            float(p.get("height", 0)) / float(p.get("width", 1))
            for p in panels
            if float(p.get("width", 0)) > 0
        ]
        if ratios and min(ratios) < float(layout.get("minimum_panel_aspect", 0.55)):
            add(
                findings,
                "flattened_panels",
                "layout",
                f"minimum aspect={min(ratios):.3f}",
            )

    tolerance = float(data.get("alignment_tolerance", 0.02))
    for index, panel in enumerate(panels):
        where = f"panels[{index}]"
        if not isinstance(panel, dict):
            add(findings, "invalid_panel", where, "must be an object")
            continue
        for tick in panel.get("tick_labels", []):
            actual = float(tick.get("actual_center", 0))
            expected = float(tick.get("tick_center", 0))
            if abs(actual - expected) > tolerance:
                add(
                    findings,
                    "tick_misaligned",
                    where,
                    f"{tick.get('axis')} {tick.get('label')}: {actual} vs {expected}",
                )
        for axis, item in panel.get("axis_titles", {}).items():
            if (
                abs(
                    float(item.get("actual_center", 0))
                    - float(item.get("panel_center", 0))
                )
                > tolerance
            ):
                add(findings, "axis_title_misaligned", where, axis)
        for line in panel.get("reference_lines", []):
            if abs(
                float(line.get("actual_value", 0))
                - float(line.get("expected_value", 0))
            ) > float(line.get("tolerance", 1e-9)):
                add(
                    findings,
                    "reference_line_mismatch",
                    where,
                    str(line.get("label", "reference")),
                )
            if not str(line.get("meaning", "")).strip():
                add(
                    findings,
                    "reference_line_unexplained",
                    where,
                    str(line.get("label", "reference")),
                )
        for bar in panel.get("error_bars", []):
            low, point, high = (
                float(bar["low"]),
                float(bar["point"]),
                float(bar["high"]),
            )
            if not low <= point <= high:
                add(
                    findings,
                    "error_bar_excludes_point",
                    where,
                    str(bar.get("label", "interval")),
                )
            if bar.get("plot_origin") != bar.get("overlay_origin"):
                add(
                    findings,
                    "coordinate_origin_mismatch",
                    where,
                    str(bar.get("label", "interval")),
                )
        if panel.get("markers_present") and not panel.get("marker_meanings"):
            add(
                findings,
                "marker_meaning_missing",
                where,
                "markers require explicit meanings",
            )
        if panel.get("y_axis_visible") and panel.get("y_axis_informative") is False:
            add(findings, "empty_y_axis", where, "remove or provide information")
        keys = [str(s.get("visual_key", "")) for s in panel.get("series", [])]
        if keys and len(keys) != len(set(keys)):
            add(
                findings,
                "indistinguishable_series",
                where,
                "visual keys must be unique",
            )

        if origin_visio_data_chart:
            geometry = panel.get("geometry")
            if not isinstance(geometry, dict):
                add(
                    findings,
                    "panel_geometry_missing",
                    where,
                    "record plot-relative title, text-box and axis-gap measurements",
                )
                continue

            text_boxes = geometry.get("text_boxes", [])
            axis_title_gaps = geometry.get("axis_title_gaps", [])
            axis_title_center_errors = geometry.get("axis_title_center_errors", {})
            if not isinstance(text_boxes, list) or not text_boxes:
                add(
                    findings,
                    "text_box_measurements_missing",
                    f"{where}.geometry.text_boxes",
                    "record composed-text and box widths",
                )
                text_boxes = []
            if not isinstance(axis_title_gaps, list) or not axis_title_gaps:
                add(
                    findings,
                    "axis_title_gap_measurements_missing",
                    f"{where}.geometry.axis_title_gaps",
                    "record title-to-tick-label clearance",
                )
                axis_title_gaps = []
            if (
                not isinstance(axis_title_center_errors, dict)
                or not axis_title_center_errors
            ):
                add(
                    findings,
                    "axis_title_center_measurements_missing",
                    f"{where}.geometry.axis_title_center_errors",
                    "record plot-relative center error by axis",
                )
                axis_title_center_errors = {}

            panel_title_measured = any(
                str(box.get("role", "")) == "panel_title"
                for box in text_boxes
                if isinstance(box, dict)
            )
            if panel_title_measured and "panel_title_center_error" not in geometry:
                add(
                    findings,
                    "panel_title_center_measurement_missing",
                    f"{where}.geometry.panel_title_center_error",
                    "record plot-relative panel-title center error",
                )
            elif (
                "panel_title_center_error" in geometry
                and abs(float(geometry["panel_title_center_error"])) > tolerance
            ):
                add(
                    findings,
                    "panel_title_misaligned",
                    f"{where}.geometry",
                    "center the fitted title box on the plot bounding box",
                )
            for axis, error in axis_title_center_errors.items():
                if abs(float(error)) > tolerance:
                    add(
                        findings,
                        "axis_title_misaligned",
                        f"{where}.geometry.axis_title_center_errors.{axis}",
                        str(error),
                    )

            for box_index, box in enumerate(text_boxes):
                box_where = f"{where}.geometry.text_boxes[{box_index}]"
                if not isinstance(box, dict):
                    add(
                        findings,
                        "invalid_text_box_measurement",
                        box_where,
                        "must be an object",
                    )
                    continue
                text_width = float(box.get("text_width", 0))
                box_width = float(box.get("box_width", 0))
                font_em = float(box.get("font_em", 0))
                maximum = float(box.get("max_extra_per_side_em", 0))
                if text_width <= 0 or box_width <= 0 or font_em <= 0 or maximum < 0:
                    add(
                        findings,
                        "invalid_text_box_measurement",
                        box_where,
                        "widths and font_em must be positive",
                    )
                    continue
                if box_width + tolerance < text_width:
                    add(
                        findings,
                        "text_box_clips_text",
                        box_where,
                        str(box.get("role", "text")),
                    )
                    continue
                extra_per_side_em = (box_width - text_width) / (2 * font_em)
                if extra_per_side_em > maximum + tolerance:
                    add(
                        findings,
                        "text_box_oversized",
                        box_where,
                        f"extra_per_side_em={extra_per_side_em:.3f}",
                    )

            for gap_index, gap in enumerate(axis_title_gaps):
                gap_where = f"{where}.geometry.axis_title_gaps[{gap_index}]"
                if not isinstance(gap, dict):
                    add(
                        findings,
                        "invalid_axis_title_gap",
                        gap_where,
                        "must be an object",
                    )
                    continue
                distance = float(gap.get("gap", 0))
                font_em = float(gap.get("font_em", 0))
                minimum = float(gap.get("minimum_gap_em", 0))
                maximum = float(gap.get("maximum_gap_em", 0))
                if distance < 0 or font_em <= 0 or minimum < 0 or maximum < minimum:
                    add(
                        findings,
                        "invalid_axis_title_gap",
                        gap_where,
                        "use non-negative distance and an ordered em range",
                    )
                    continue
                normalized = distance / font_em
                if normalized < minimum - tolerance or normalized > maximum + tolerance:
                    add(
                        findings,
                        "axis_title_gap_out_of_range",
                        gap_where,
                        f"gap_em={normalized:.3f}",
                    )

    if origin_visio_data_chart:
        content_bbox = data.get("content_bbox")
        if not isinstance(content_bbox, dict):
            add(
                findings,
                "content_bbox_missing",
                "content_bbox",
                "record visible-content use of the final page",
            )
        else:
            ratio = float(content_bbox.get("page_area_ratio", 0))
            minimum = float(content_bbox.get("minimum_page_area_ratio", 0))
            if not 0 < ratio <= 1 or not 0 < minimum <= 1:
                add(
                    findings,
                    "invalid_content_bbox_ratio",
                    "content_bbox",
                    "ratios must be within (0, 1]",
                )
            elif ratio + tolerance < minimum:
                add(
                    findings,
                    "page_content_too_sparse",
                    "content_bbox",
                    f"page_area_ratio={ratio:.3f}",
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.contract.read_text(encoding="utf-8"))
    findings = validate(data)
    result = {
        "contract": str(args.contract),
        "findings": findings,
        "passed": not findings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if args.fail_on_findings and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
