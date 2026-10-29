"""Figure digitisation: axis calibration, pixel uncertainty and overlay checks."""

from __future__ import annotations

import math

import pytest

from jw.scientific_data.contracts import IssueCode, ValueKind
from jw.scientific_data.figures import (
    DigitisedSeries,
    FigureCalibration,
    axis_issue,
    calibration_from_payload,
    digitise_points,
    figure_size,
    fit_axis,
    overlay_issue,
    overlay_report,
    series_from_payload,
)


def _calibration(
    *, pixel_noise: float = 1.0, y_scale: str = "linear"
) -> FigureCalibration:
    return FigureCalibration(
        x=fit_axis([(100.0, 54000.0), (300.0, 54040.0)], axis="x", unit="MJD"),
        y=fit_axis([(400.0, 20.0), (200.0, 10.0)], axis="y", scale=y_scale, unit="mag"),
        pixel_noise=pixel_noise,
        figure_id="fig1",
        source_id="src:local:001",
    )


def test_linear_axis_fit_maps_pixels_to_values():
    axis = fit_axis([(100.0, 10.0), (200.0, 20.0), (300.0, 30.0)])
    assert axis.usable
    assert axis.slope == pytest.approx(0.1)
    assert axis.intercept == pytest.approx(0.0)
    assert axis.to_value(250.0) == pytest.approx(25.0)
    assert axis.to_pixel(25.0) == pytest.approx(250.0)


def test_log_axis_is_fitted_in_log_space():
    axis = fit_axis([(100.0, 1.0), (200.0, 10.0), (300.0, 100.0)], scale="log")
    assert axis.usable
    assert axis.to_value(150.0) == pytest.approx(10**0.5)
    assert axis.to_pixel(10**0.5) == pytest.approx(150.0)


def test_pixel_order_does_not_matter_for_the_fit():
    ascending = fit_axis([(200.0, 10.0), (400.0, 20.0)], axis="y")
    descending = fit_axis([(400.0, 20.0), (200.0, 10.0)], axis="y")
    assert ascending.slope == pytest.approx(descending.slope)
    assert ascending.to_value(300.0) == pytest.approx(descending.to_value(300.0))


def test_single_tick_cannot_be_calibrated():
    axis = fit_axis([(100.0, 10.0)])
    assert not axis.usable
    assert axis.to_value(120.0) is None


def test_curved_ticks_reveal_a_suspect_scale():
    axis = fit_axis([(0.0, 1.0), (100.0, 10.0), (200.0, 100.0)], axis="y")
    issue = axis_issue(
        FigureCalibration(
            x=fit_axis([(0.0, 0.0), (100.0, 1.0)], axis="x"),
            y=axis,
            figure_id="fig9",
        )
    )
    assert issue is not None
    assert issue.code is IssueCode.AXIS_SCALE_SUSPECT
    assert issue.evidence["axis"] == "y"


def test_unreadable_axis_is_reported():
    calibration = FigureCalibration(
        x=fit_axis([]), y=fit_axis([(10.0, 1.0)]), figure_id="fig2"
    )
    issue = axis_issue(calibration, figure_id="fig2")
    assert issue is not None
    assert issue.code is IssueCode.AXIS_TICK_UNREADABLE
    assert calibration.usable is False


def test_expected_log_scale_mismatch_is_reported():
    calibration = _calibration(y_scale="log")
    issue = axis_issue(calibration, figure_id="fig1", expected_log=False)
    assert issue is not None
    assert issue.code is IssueCode.AXIS_SCALE_SUSPECT
    assert issue.evidence == {"observed": "log", "expected": False}


def test_digitised_points_keep_pixels_and_uncertainty():
    calibration = _calibration(pixel_noise=2.0)
    series = DigitisedSeries(label="B band", points=((200.0, 300.0),))
    records = digitise_points(
        series,
        calibration,
        entity_id="obj:snia-1",
        observable="apparent_magnitude",
        unit="mag",
    )
    assert len(records) == 1
    record = records[0]
    assert record.value_kind is ValueKind.DIGITIZED
    assert record.value_raw == "pixel(200.0,300.0)"
    assert record.value == pytest.approx(15.0)
    assert record.unit == "mag"
    assert record.uncertainty == pytest.approx(0.1)
    assert record.uncertainty_kind == "digitisation"
    assert record.flags == ("digitised_from_figure", "series:B band")
    locator = record.provenance[0].locator
    assert locator["pixel_x"] == 200.0
    assert locator["pixel_y"] == 300.0
    assert locator["series"] == "B band"
    assert record.provenance[0].value_kind is ValueKind.DIGITIZED


def test_mjd_axis_becomes_an_iso_instant():
    calibration = _calibration()
    series = DigitisedSeries(label="V", points=((200.0, 300.0),))
    records = digitise_points(
        series,
        calibration,
        entity_id="obj:snia-1",
        observable="apparent_magnitude",
        time_system="mjd",
    )
    assert records[0].time.startswith("2006-10-12")


def test_overlay_round_trip_passes():
    calibration = _calibration()
    series = DigitisedSeries(label="V", points=((200.0, 300.0), (250.0, 250.0)))
    records = digitise_points(
        series, calibration, entity_id="obj:1", observable="apparent_magnitude"
    )
    report = overlay_report(records, calibration)
    assert report.checked == 2
    assert report.passed
    assert report.max_residual == pytest.approx(0.0, abs=1e-6)
    assert overlay_issue(report, figure_id="fig1") is None


def test_overlay_detects_a_wrong_calibration():
    calibration = _calibration()
    series = DigitisedSeries(label="V", points=((200.0, 300.0),))
    records = digitise_points(
        series, calibration, entity_id="obj:1", observable="apparent_magnitude"
    )
    shifted = FigureCalibration(
        x=calibration.x,
        y=fit_axis([(400.0, 24.0), (200.0, 10.0)], axis="y", unit="mag"),
        figure_id="fig1",
    )
    report = overlay_report(records, shifted, tolerance_px=1.0)
    assert not report.passed
    assert report.max_residual > 1.0
    issue = overlay_issue(report, figure_id="fig1")
    assert issue is not None
    assert issue.code is IssueCode.FIGURE_OVERLAY_MISMATCH


def test_overlay_without_residuals_is_flagged():
    report = overlay_report((), _calibration())
    assert report.checked == 0
    issue = overlay_issue(report, figure_id="fig1")
    assert issue is not None
    assert "could be re-projected" in issue.detail


def test_axis_span_and_value_per_pixel():
    calibration = _calibration()
    assert calibration.axis_span("y") == pytest.approx(10.0)
    assert calibration.axis_span("x") == pytest.approx(40.0)
    assert calibration.y.value_per_pixel(pixel=250.0) == pytest.approx(0.05)


def test_calibration_payload_round_trip():
    payload = {
        "figure_id": "fig7",
        "source_id": "src:x",
        "pixel_noise": 1.5,
        "x_ticks": [{"pixel": 100, "value": 54000}, {"pixel": 300, "value": 54040}],
        "y_ticks": [{"pixel": 400, "value": 20}, {"pixel": 200, "value": 10}],
        "y_scale": "linear",
        "y_unit": "mag",
        "series": [
            {
                "label": "r",
                "colour": "#ff0000",
                "points": [[200, 300], [250, 250]],
                "entity": "SN 2005cf",
            }
        ],
    }
    calibration = calibration_from_payload(payload)
    assert calibration.figure_id == "fig7"
    assert calibration.pixel_noise == 1.5
    assert calibration.to_data(200.0, 300.0) == pytest.approx((54020.0, 15.0))
    series = series_from_payload(payload)
    assert len(series) == 1
    assert series[0].label == "r"
    assert series[0].points == ((200.0, 300.0), (250.0, 250.0))
    assert series[0].entity_hint == "SN 2005cf"


def test_figure_size_reads_png_and_tolerates_garbage(tmp_path):
    from PIL import Image

    good = tmp_path / "fig.png"
    Image.new("RGB", (64, 32)).save(good)
    assert figure_size(good) == (64, 32)
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    assert figure_size(bad) is None
    assert figure_size(tmp_path / "missing.png") is None


def test_unusable_calibration_produces_no_points():
    calibration = FigureCalibration(x=fit_axis([]), y=fit_axis([]), figure_id="fig3")
    series = DigitisedSeries(label="x", points=((1.0, 2.0),))
    assert digitise_points(series, calibration, entity_id="e", observable="o") == ()
    assert math.isinf(overlay_report((), calibration).max_residual)
