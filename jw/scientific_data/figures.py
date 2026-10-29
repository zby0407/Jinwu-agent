"""Figures: turn a plotted series into numbers without pretending it is exact.

Digitising a plot means fitting the axes from tick marks, reading marker
positions in pixels, propagating the pixel uncertainty into data units, and then
projecting the recovered points *back* onto the image to prove the mapping is
sane. Every record produced here is marked ``DIGITIZED`` and carries the pixel
coordinates that produced it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import (
    IssueCode,
    IssueSeverity,
    IssueState,
    ObservationRecord,
    ProvenanceRef,
    QualityIssue,
    ValueKind,
)
from .domain import confidence_from_score
from .timescales import mjd_to_iso

__all__ = [
    "AxisCalibration",
    "DigitisedSeries",
    "FigureCalibration",
    "OverlayReport",
    "axis_issue",
    "digitise_points",
    "fit_axis",
    "overlay_report",
]

_MIN_RMS_FRACTION = 0.01
_DEFAULT_TOLERANCE_PX = 2.0


@dataclass(frozen=True, slots=True)
class AxisCalibration:
    """A linear fit between pixel positions and data values on one axis."""

    axis: str
    scale: str
    slope: float
    intercept: float
    rms: float = 0.0
    ticks: int = 0
    unit: str = ""
    pixel_low: float = 0.0
    pixel_high: float = 0.0

    @property
    def usable(self) -> bool:
        return self.ticks >= 2 and math.isfinite(self.slope) and self.slope != 0.0

    def to_value(self, pixel: float) -> float | None:
        """Data value at ``pixel``; ``None`` when the fit is unusable."""
        if not self.usable:
            return None
        mapped = self.slope * pixel + self.intercept
        if self.scale == "log":
            return 10**mapped if mapped > -300 else None
        return mapped

    def to_pixel(self, value: float) -> float | None:
        """Pixel position of ``value``, used when checking the overlay."""
        if not self.usable:
            return None
        if self.scale == "log":
            if value <= 0:
                return None
            mapped = math.log10(value)
        else:
            mapped = value
        return (mapped - self.intercept) / self.slope

    def value_per_pixel(self, *, pixel: float = 0.0) -> float:
        """Local data change per pixel, needed for the uncertainty budget."""
        if not self.usable:
            return 0.0
        probe = self.to_value(pixel)
        shifted = self.to_value(pixel + 1.0)
        if probe is None or shifted is None:
            return abs(self.slope)
        return abs(shifted - probe)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "scale": self.scale,
            "slope": self.slope,
            "intercept": self.intercept,
            "rms": self.rms,
            "ticks": self.ticks,
            "unit": self.unit,
            "pixel_low": self.pixel_low,
            "pixel_high": self.pixel_high,
        }


def fit_axis(
    ticks: Sequence[tuple[float, float]],
    *,
    axis: str = "x",
    scale: str = "linear",
    unit: str = "",
) -> AxisCalibration:
    """Fit a pixel-to-value mapping through labelled tick marks.

    ``ticks`` are ``(pixel, value)`` pairs. A log axis is fitted in log10 space,
    which is why a mislabelled log axis shows up as a huge ``rms`` instead of a
    plausible straight line.
    """
    clean: list[tuple[float, float]] = []
    for pixel, value in ticks:
        if not (math.isfinite(pixel) and math.isfinite(value)):
            continue
        if scale == "log":
            if value <= 0:
                continue
            clean.append((float(pixel), float(value)))
        else:
            clean.append((float(pixel), float(value)))
    if len(clean) < 2:
        return AxisCalibration(
            axis=axis, scale=scale, slope=0.0, intercept=0.0, unit=unit
        )

    ys = [math.log10(value) if scale == "log" else value for _pixel, value in clean]
    xs = [pixel for pixel, _value in clean]
    pixel_low = min(xs)
    pixel_high = max(xs)
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance = sum((value - mean_x) ** 2 for value in xs)
    if variance == 0:
        return AxisCalibration(
            axis=axis, scale=scale, slope=0.0, intercept=0.0, unit=unit
        )
    slope = (
        sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / variance
    )
    intercept = mean_y - slope * mean_x
    residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys, strict=True)]
    rms = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return AxisCalibration(
        axis=axis,
        scale=scale,
        slope=slope,
        intercept=intercept,
        rms=rms,
        ticks=len(clean),
        unit=unit,
        pixel_low=pixel_low,
        pixel_high=pixel_high,
    )


@dataclass(frozen=True, slots=True)
class FigureCalibration:
    """Both axes of one figure, plus the pixel size of a marker."""

    x: AxisCalibration
    y: AxisCalibration
    pixel_noise: float = 1.0
    figure_id: str = ""
    source_id: str = ""

    @property
    def usable(self) -> bool:
        return self.x.usable and self.y.usable

    def to_data(self, pixel_x: float, pixel_y: float) -> tuple[float, float] | None:
        x_value = self.x.to_value(pixel_x)
        y_value = self.y.to_value(pixel_y)
        if x_value is None or y_value is None:
            return None
        return x_value, y_value

    def to_pixels(self, x_value: float, y_value: float) -> tuple[float, float] | None:
        pixel_x = self.x.to_pixel(x_value)
        pixel_y = self.y.to_pixel(y_value)
        if pixel_x is None or pixel_y is None:
            return None
        return pixel_x, pixel_y

    def uncertainties(self, *, pixel_x: float, pixel_y: float) -> tuple[float, float]:
        noise = max(0.0, self.pixel_noise)
        return (
            noise * self.x.value_per_pixel(pixel=pixel_x),
            noise * self.y.value_per_pixel(pixel=pixel_y),
        )

    def axis_span(self, axis: str = "y") -> float:
        """Data span covered by the labelled ticks on one axis."""
        calibration = self.y if axis == "y" else self.x
        if not calibration.usable:
            return 0.0
        first = calibration.to_value(calibration.pixel_low)
        second = calibration.to_value(calibration.pixel_high)
        if first is None or second is None:
            return 0.0
        return abs(second - first)

    def to_dict(self) -> dict[str, Any]:
        return {
            "figure_id": self.figure_id,
            "source_id": self.source_id,
            "pixel_noise": self.pixel_noise,
            "x": self.x.to_dict(),
            "y": self.y.to_dict(),
            "usable": self.usable,
        }


@dataclass(frozen=True, slots=True)
class DigitisedSeries:
    """One plotted series: its label and the marker positions in pixels."""

    label: str
    points: tuple[tuple[float, float], ...]
    colour: str = ""
    marker: str = ""
    value_axis: str = "y"
    entity_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "colour": self.colour,
            "marker": self.marker,
            "points": [list(point) for point in self.points],
        }


def digitise_points(
    series: DigitisedSeries,
    calibration: FigureCalibration,
    *,
    entity_id: str,
    observable: str,
    time_observable: str = "time",
    time_system: str = "mjd",
    unit: str = "",
    time_unit: str = "",
    path: str = "",
) -> tuple[ObservationRecord, ...]:
    """Convert marker pixels into observation records marked ``DIGITIZED``."""
    records: list[ObservationRecord] = []
    for index, (pixel_x, pixel_y) in enumerate(series.points):
        converted = calibration.to_data(pixel_x, pixel_y)
        if converted is None:
            continue
        x_value, y_value = converted
        uncertainty_x, uncertainty_y = calibration.uncertainties(
            pixel_x=pixel_x, pixel_y=pixel_y
        )
        is_time_on_x = series.value_axis == "y"
        value = y_value if is_time_on_x else x_value
        value_uncertainty = uncertainty_y if is_time_on_x else uncertainty_x
        time_value = x_value if is_time_on_x else y_value
        time_uncertainty = uncertainty_x if is_time_on_x else uncertainty_y
        score = _quality_score(calibration)
        records.append(
            ObservationRecord(
                record_uid=f"rec:{calibration.figure_id}:{index:03d}",
                entity_id=entity_id,
                observable=observable,
                unit=unit or calibration.y.unit,
                value=value,
                value_raw=f"pixel({pixel_x:.1f},{pixel_y:.1f})",
                time=_time_string(time_value, time_system),
                time_raw=f"pixel_x={pixel_x:.1f}",
                time_system=time_system,
                uncertainty=value_uncertainty or None,
                uncertainty_kind="digitisation",
                value_kind=ValueKind.DIGITIZED,
                source_ids=(calibration.source_id,) if calibration.source_id else (),
                provenance=(
                    ProvenanceRef(
                        source_id=calibration.source_id,
                        locator={
                            "path": path,
                            "figure_id": calibration.figure_id,
                            "series": series.label,
                            "colour": series.colour,
                            "marker": series.marker,
                            "pixel_x": round(pixel_x, 3),
                            "pixel_y": round(pixel_y, 3),
                            "x_value": round(x_value, 9),
                            "y_value": round(y_value, 9),
                            "pixel_noise": calibration.pixel_noise,
                            "x_scale": calibration.x.scale,
                            "y_scale": calibration.y.scale,
                            "time_uncertainty": round(time_uncertainty, 9),
                            "time_unit": time_unit,
                        },
                        extractor="jw.scientific_data.figures.digitise_points",
                        extractor_version="1",
                        value_kind=ValueKind.DIGITIZED,
                        confidence=confidence_from_score(score),
                        confidence_score=score,
                        raw_excerpt=(
                            f"{series.label} marker at pixel "
                            f"({pixel_x:.1f}, {pixel_y:.1f})"
                        ),
                    ),
                ),
                flags=("digitised_from_figure", f"series:{series.label}"),
                attributes={"label": series.label} if series.label else {},
            )
        )
    return tuple(records)


def _time_string(value: float, time_system: str) -> str:
    """Canonical time text for a digitised point.

    MJD axes become ISO instants so digitised points merge with tabulated
    observations; any other axis keeps the numeric text and its raw pixels.
    """
    if time_system == "mjd":
        iso = mjd_to_iso(value)
        if iso:
            return iso
    return f"{value:.6f}"


def _quality_score(calibration: FigureCalibration) -> float:
    """Confidence in a digitised point, driven by axis fit quality."""
    if not calibration.usable:
        return 0.1
    penalty = min(
        0.5,
        0.5 * (calibration.x.rms + calibration.y.rms) / max(_MIN_RMS_FRACTION, 1e-9),
    )
    noise_penalty = min(0.2, calibration.pixel_noise / 10.0)
    return max(0.1, 0.92 - penalty - noise_penalty)


@dataclass(frozen=True, slots=True)
class OverlayReport:
    """Result of projecting digitised points back onto the figure."""

    checked: int
    passed: bool
    max_residual: float
    median_residual: float
    tolerance: float
    residuals: tuple[float, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "passed": self.passed,
            "max_residual": self.max_residual,
            "median_residual": self.median_residual,
            "tolerance": self.tolerance,
        }


def overlay_report(
    records: Sequence[ObservationRecord],
    calibration: FigureCalibration,
    *,
    tolerance_px: float = _DEFAULT_TOLERANCE_PX,
) -> OverlayReport:
    """Re-project every digitised record and measure the round-trip error."""
    residuals: list[float] = []
    for record in records:
        if not record.provenance:
            continue
        locator = record.provenance[0].locator
        pixel = _pixel_pair(locator)
        data = _data_pair(locator, record)
        if pixel is None or data is None:
            continue
        projected = calibration.to_pixels(data[0], data[1])
        if projected is None:
            continue
        residuals.append(math.hypot(projected[0] - pixel[0], projected[1] - pixel[1]))
    if not residuals:
        return OverlayReport(0, False, math.inf, math.inf, tolerance_px)
    ordered = sorted(residuals)
    median = ordered[(len(ordered) - 1) // 2]
    worst = ordered[-1]
    return OverlayReport(
        checked=len(ordered),
        passed=worst <= tolerance_px,
        max_residual=worst,
        median_residual=median,
        tolerance=tolerance_px,
        residuals=tuple(residuals),
    )


def _pixel_pair(locator: Mapping[str, Any]) -> tuple[float, float] | None:
    try:
        return float(locator["pixel_x"]), float(locator["pixel_y"])
    except (KeyError, TypeError, ValueError):
        return None


def _data_pair(
    locator: Mapping[str, Any], record: ObservationRecord
) -> tuple[float, float] | None:
    """(x, y) values that the record claims to represent on the figure."""
    try:
        return float(locator["x_value"]), float(locator["y_value"])
    except (KeyError, TypeError, ValueError):
        pass
    if record.value is None:
        return None
    try:
        return float(record.time), record.value
    except (TypeError, ValueError):
        return None


def axis_issue(
    calibration: FigureCalibration,
    *,
    figure_id: str = "",
    expected_log: bool | None = None,
    tolerance_px: float = _DEFAULT_TOLERANCE_PX,
) -> QualityIssue | None:
    """Flag an axis whose tick fit is too poor to trust."""
    for axis in (calibration.x, calibration.y):
        if axis.ticks < 2:
            return QualityIssue(
                issue_id=f"issue:figures:axis_tick_unreadable:{figure_id}:{axis.axis}",
                code=IssueCode.AXIS_TICK_UNREADABLE,
                severity=IssueSeverity.ERROR,
                stage="figures",
                subject={"figure_id": figure_id, "axis": axis.axis},
                detail=(
                    f"Only {axis.ticks} readable tick(s) were found on the "
                    f"{axis.axis} axis, so no calibration could be fitted."
                ),
                evidence=axis.to_dict(),
                proposed_action="Read the axis ticks from the figure or its caption.",
            )
        if axis.rms > _MIN_RMS_FRACTION and axis.ticks >= 3:
            return QualityIssue(
                issue_id=f"issue:figures:axis_scale_suspect:{figure_id}:{axis.axis}",
                code=IssueCode.AXIS_SCALE_SUSPECT,
                severity=IssueSeverity.ERROR,
                stage="figures",
                subject={"figure_id": figure_id, "axis": axis.axis},
                detail=(
                    f"The {axis.axis} axis ticks do not lie on a straight "
                    f"{axis.scale} line (rms {axis.rms:.4f} in fit space); the "
                    "axis may be logarithmic or the ticks were misread."
                ),
                evidence=axis.to_dict(),
                proposed_action=(
                    "Confirm whether this axis is linear or logarithmic and re-read "
                    "the tick labels."
                ),
            )
    if expected_log is not None:
        observed = calibration.y.scale == "log"
        if observed != expected_log:
            return QualityIssue(
                issue_id=f"issue:figures:axis_scale_mismatch:{figure_id}",
                code=IssueCode.AXIS_SCALE_SUSPECT,
                severity=IssueSeverity.ERROR,
                stage="figures",
                subject={"figure_id": figure_id, "axis": "y"},
                detail=(
                    f"The y axis was read as {calibration.y.scale} but the reader "
                    f"expected {'log' if expected_log else 'linear'}."
                ),
                evidence={"observed": calibration.y.scale, "expected": expected_log},
                proposed_action="Confirm the y axis scale before using these points.",
            )
    if not calibration.usable:
        return QualityIssue(
            issue_id=f"issue:figures:calibration_failed:{figure_id}",
            code=IssueCode.AXIS_TICK_UNREADABLE,
            severity=IssueSeverity.ERROR,
            stage="figures",
            subject={"figure_id": figure_id},
            detail="The figure could not be calibrated, so no point was digitised.",
            evidence=calibration.to_dict(),
            proposed_action="Provide the tick positions or the underlying table.",
        )
    return None


def overlay_issue(report: OverlayReport, *, figure_id: str = "") -> QualityIssue | None:
    """Flag points that do not land back on their markers."""
    if report.checked == 0:
        return QualityIssue(
            issue_id=f"issue:figures:overlay_unchecked:{figure_id}",
            code=IssueCode.FIGURE_OVERLAY_MISMATCH,
            severity=IssueSeverity.WARNING,
            stage="figures",
            subject={"figure_id": figure_id},
            detail="No digitised point could be re-projected for verification.",
            evidence=report.to_dict(),
            proposed_action="Digitise at least one point to verify the calibration.",
            state=IssueState.DETECTED,
        )
    if report.passed:
        return None
    return QualityIssue(
        issue_id=f"issue:figures:overlay_mismatch:{figure_id}",
        code=IssueCode.FIGURE_OVERLAY_MISMATCH,
        severity=IssueSeverity.ERROR,
        stage="figures",
        subject={"figure_id": figure_id},
        detail=(
            f"Re-projecting the digitised points misses the drawn markers by up to "
            f"{report.max_residual:.2f} px (tolerance {report.tolerance:.2f} px)."
        ),
        evidence=report.to_dict(),
        proposed_action=(
            "Re-check the axis calibration or the marker selection before using "
            "these values."
        ),
    )


def figure_size(path: str | Path) -> tuple[int, int] | None:
    """Pixel size of a figure, when the image can be opened."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is an optional reader
        return None
    try:
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:  # a broken image must not abort a run
        return None


def calibration_from_payload(payload: Mapping[str, Any]) -> FigureCalibration:
    """Build a calibration from the digitisation side-car written by a reader."""
    x_ticks = [
        (float(item["pixel"]), float(item["value"]))
        for item in payload.get("x_ticks") or ()
    ]
    y_ticks = [
        (float(item["pixel"]), float(item["value"]))
        for item in payload.get("y_ticks") or ()
    ]
    x_axis = fit_axis(
        x_ticks,
        axis="x",
        scale=str(payload.get("x_scale") or "linear"),
        unit=str(payload.get("x_unit") or ""),
    )
    y_axis = fit_axis(
        y_ticks,
        axis="y",
        scale=str(payload.get("y_scale") or "linear"),
        unit=str(payload.get("y_unit") or ""),
    )
    return FigureCalibration(
        x=x_axis,
        y=y_axis,
        pixel_noise=float(payload.get("pixel_noise", 1.0) or 1.0),
        figure_id=str(payload.get("figure_id") or ""),
        source_id=str(payload.get("source_id") or ""),
    )


def series_from_payload(payload: Mapping[str, Any]) -> tuple[DigitisedSeries, ...]:
    """Read the plotted series from the digitisation side-car."""
    series: list[DigitisedSeries] = []
    for index, item in enumerate(payload.get("series") or ()):
        points = tuple(
            (float(point[0]), float(point[1])) for point in item.get("points") or ()
        )
        series.append(
            DigitisedSeries(
                label=str(item.get("label") or f"series-{index + 1}"),
                points=points,
                colour=str(item.get("colour") or ""),
                marker=str(item.get("marker") or ""),
                value_axis=str(item.get("value_axis") or "y"),
                entity_hint=str(item.get("entity") or payload.get("entity") or ""),
            )
        )
    return tuple(series)
