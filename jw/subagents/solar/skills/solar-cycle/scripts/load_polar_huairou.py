#!/usr/bin/env python3
"""Extract polar-cap magnetic field proxy from Huairou SMFT magnetograms.

This script handles two instrument epochs:

* 1987-2001: legacy little-endian 16-bit signed integer ``.dat`` images. The
  first 128 bytes are treated as an opaque binary prefix and skipped.
* 2002 onward: FITS files produced by the Huairou Solar Observing Station (HSOS)
  35 cm SMFT longitudinal magnetograph. File formats vary across this period:

  - 2002-2008: 640x480 single-plane (``CONTENT='L'``, ``BITPIX=16``) images.
  - 2009-2010: 992x1000 two-plane cubes (``BITPIX=32``, ``NAXIS3=2``).
  - 2015-2017 and verified 2020-2023 samples: 992x992 two-plane cubes
    (``BITPIX=32``, ``NAXIS3=2``).

For the two-plane epochs, no production signal is selected by default.  The
diagnostic workflow compares both stored planes, their difference, Stokes V/I,
and header-calibrated V/I before a project-wide choice is made.

Polar-cap apertures (confirmed/parameterised with the user):
- North polar files (NPL): top rows.
- South polar files (SPL): bottom rows.
- For 640x480 images the default is the top/bottom ``--fit-cap-rows`` rows with
  a central quiet-reference box of ``--fit-center-box`` size.
- For the 992x... two-plane epochs, a central circular aperture around the pole
  is used because the telescope points at latitude ~90 degrees.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from astropy.io import fits

    HAS_ASTROPY = True
except ImportError:  # pragma: no cover - optional dependency
    fits = None  # type: ignore[assignment]
    HAS_ASTROPY = False

# ---------------------------------------------------------------------------
# Format constants inferred from the D:\极区前兆 sample.
# ---------------------------------------------------------------------------
# The legacy .dat archive stores raw 16-bit signed integer images followed by
# a small ASCII trailer of variable length after the payload.
LARGE_SHAPE = (512, 512)  # "l*" files, payload = 524288 B
SMALL_SHAPE = (256, 512)  # "s*" files, payload = 262144 B
HUGE_SHAPE = (1024, 512)  # occasional large frames, payload = 1048576 B

LARGE_CAP_ROWS = 100
SMALL_CAP_ROWS = 50

LARGE_CENTER_BOX = (256, 256)
SMALL_CENTER_BOX = (128, 256)

# FITS-era defaults (640x480 single-plane and 992x... two-plane).
DEFAULT_FITS_CAP_ROWS = 100
DEFAULT_FITS_CENTER_BOX = (240, 320)  # height x width in pixels; corresponds to
                                      # the intuitive "320,240" width x height.
DEFAULT_FITS_CENTER_RADIUS = 150
DEFAULT_FITS_APERTURE_BOX = (200, 200)

FITS_SIGNAL_CHOICES = (
    "plane0",
    "plane1",
    "difference",
    "vi",
    "calibrated_vi",
)
FITS_APERTURE_CHOICES = ("auto", "polar-strip", "center-circle", "center-box")
UNVALIDATED_GEOMETRY_EPOCHS = {
    "pulnix_fit16_2011_2014",
    "imperx_fit32_2014",
    "imperx_fit32_2018_2026",
}

DAILY_COLUMNS = [
    "date",
    "hemisphere",
    "instrument_epoch",
    "camera",
    "source_format",
    "signal_definition",
    "signal_unit",
    "calibration_status",
    "byte_order_normalization",
    "field_mean_raw",
    "field_mean_center",
    "field_mean_corrected",
    "field_mean_abs",
    "valid_pixel_ratio",
    "n_obs",
]

MONTHLY_COLUMNS = [
    "year",
    "month",
    "hemisphere",
    "instrument_epoch",
    "camera",
    "source_format",
    "signal_definition",
    "signal_unit",
    "calibration_status",
    "byte_order_normalization",
    "field_mean_raw",
    "field_mean_center",
    "field_mean_corrected",
    "field_mean_abs",
    "n_days",
    "polarity_strength",
]

MONTH_MAP = {
    name: idx
    for idx, name in enumerate(
        [
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ],
        start=1,
    )
}


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _month_to_int(month_str: str) -> int:
    """Convert three-letter English month name to month number."""
    value = MONTH_MAP.get(month_str.lower())
    if value is None:
        raise ValueError(f"Unrecognized month: {month_str!r}")
    return value


def _looks_like_year(name: str) -> bool:
    return len(name) == 4 and name.isdigit()


def _looks_like_month(name: str) -> bool:
    return name.lower() in MONTH_MAP


def _looks_like_day(name: str) -> bool:
    return name.isdigit() and 1 <= int(name) <= 31


def _parse_center_box(s: str) -> tuple[int, int]:
    """Parse 'WIDTH,HEIGHT' into (height, width) internal representation."""
    parts = [p.strip() for p in s.split(",")]
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"Center box must be 'WIDTH,HEIGHT', got {s!r}"
        )
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid center box {s!r}") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError(
            f"Center box dimensions must be positive, got {s!r}"
        )
    return (height, width)


def parse_path(path: Path, polar_dir: Path) -> dict | None:
    """Extract date/hemisphere/view metadata from the directory tree.

    The archive layout is not uniform: most files are under
    ``YYYY/MON/DD/<file>.dat``, but some are nested one or two extra levels
    (e.g. ``.../DD/1/<file>.dat`` or ``.../DD/DYY/16/<file>.dat``). We therefore
    scan the parent chain for year/month/day tokens in the correct order.
    """
    try:
        rel = path.relative_to(polar_dir)
        if len(rel.parts) < 4:
            return None

        parents = list(path.parents)
        # path.parents[0] is the immediate parent, [1] is its parent, etc.
        # We need to find day -> month -> year going upward.
        day_idx: int | None = None
        for idx, par in enumerate(parents):
            if _looks_like_day(par.name):
                day_idx = idx
                break
        if day_idx is None:
            return None

        month_idx: int | None = None
        for idx in range(day_idx + 1, len(parents)):
            if _looks_like_month(parents[idx].name):
                month_idx = idx
                break
        if month_idx is None:
            return None

        year_idx: int | None = None
        for idx in range(month_idx + 1, len(parents)):
            if _looks_like_year(parents[idx].name):
                year_idx = idx
                break
        if year_idx is None:
            return None

        year = int(parents[year_idx].name)
        month = _month_to_int(parents[month_idx].name)
        day = int(parents[day_idx].name)
    except Exception:
        return None

    name = path.stem.lower()
    if "npl" in name:
        hemisphere = "N"
    elif "spl" in name:
        hemisphere = "S"
    else:
        return None

    if name.startswith("l"):
        view_type = "L"
    elif name.startswith("s"):
        view_type = "S"
    elif name[0].isalpha():
        # Other single-letter instrument prefixes (e.g. V, P, Q, D, H) are
        # treated as large-view frames when their payload is 512x512.
        view_type = name[0].upper()
    else:
        return None

    # Last alphabetic character, if any, is treated as the observation sequence
    # code for that day (a, b, c, ...).
    sequence = name[-1] if name and name[-1].isalpha() else ""

    return {
        "year": year,
        "month": month,
        "day": day,
        "hemisphere": hemisphere,
        "view_type": view_type,
        "sequence": sequence,
    }


# ---------------------------------------------------------------------------
# Legacy .dat helpers
# ---------------------------------------------------------------------------
def _infer_image_shape(path: Path) -> tuple[int, int] | None:
    """Infer image shape from total file size.

    The archive contains a small ASCII trailer of variable length after the
    payload, so we match the total size against known payload sizes plus a
    reasonable trailer allowance.
    """
    size = path.stat().st_size
    candidates = [
        (HUGE_SHAPE, HUGE_SHAPE[0] * HUGE_SHAPE[1] * 2),
        (LARGE_SHAPE, LARGE_SHAPE[0] * LARGE_SHAPE[1] * 2),
        (SMALL_SHAPE, SMALL_SHAPE[0] * SMALL_SHAPE[1] * 2),
    ]
    max_trailer = 2048
    best: tuple[int, tuple[int, int]] | None = None
    for shape, payload_bytes in candidates:
        if size < payload_bytes:
            continue
        trailer = size - payload_bytes
        if trailer <= max_trailer:
            # Prefer the shape whose payload is closest to the file size.
            if best is None or trailer < best[0]:
                best = (trailer, shape)
    if best is None:
        return None
    return best[1]


def _read_int16_image(path: Path, shape: tuple[int, int]) -> np.ndarray:
    """Read the image payload as little-endian int16 and reshape."""
    data = path.read_bytes()
    payload_bytes = shape[0] * shape[1] * 2
    if len(data) < payload_bytes:
        raise ValueError(
            f"{path}: need at least {payload_bytes} bytes for shape {shape}, got {len(data)}"
        )
    payload = data[:payload_bytes]
    ints = struct.unpack("<" + "h" * (len(payload) // 2), payload)
    return np.array(ints, dtype=np.int16).reshape(shape)


# ---------------------------------------------------------------------------
# FITS helpers
# ---------------------------------------------------------------------------
def _read_fits_image(path: Path) -> tuple[np.ndarray, dict]:
    """Read a Huairou SMFT FITS file with astropy.

    Some files in the archive are not padded to a standard FITS block size, so
    we suppress the truncation warning and read the valid header/data.
    """
    if not HAS_ASTROPY:
        raise RuntimeError(
            "astropy is required to process FITS files. "
            "Install it with: pip install astropy"
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", fits.verify.VerifyWarning)
        warnings.simplefilter("ignore", UserWarning)
        with fits.open(path, memmap=False, ignore_missing_end=True) as hdul:
            hdu = hdul[0]
            data = hdu.data
            header = dict(hdu.header)

    if data is None:
        raise ValueError("FITS HDU contains no data")

    return np.asarray(data), header


def _header_int(header: dict, key: str) -> int:
    """Return an integer FITS keyword with a useful validation error."""
    try:
        return int(header[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"FITS header has invalid or missing {key}") from exc


def _parse_calibration(header: dict) -> float | None:
    """Return a finite CALIBRAT value, if one is present."""
    raw = header.get("CALIBRAT")
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def normalize_fits_data(data: np.ndarray, header: dict) -> tuple[np.ndarray, str]:
    """Validate and decode a known Huairou FITS layout.

    The PULNIX-era files store little-endian payloads even though FITS requires
    big-endian array data.  The IMPERX files follow the FITS byte-order rule.
    Selection is based only on the verified shape/BITPIX/camera combination;
    numeric-range heuristics are intentionally not used.
    """
    bitpix = _header_int(header, "BITPIX")
    naxis = _header_int(header, "NAXIS")
    camera = str(header.get("CAMERA", "")).strip().upper()

    if naxis != data.ndim:
        raise ValueError(f"Header NAXIS={naxis} disagrees with data ndim={data.ndim}")
    expected = tuple(
        _header_int(header, f"NAXIS{axis}") for axis in range(naxis, 0, -1)
    )
    if expected != data.shape:
        raise ValueError(
            f"Header dimensions {expected} disagree with data shape {data.shape}"
        )

    plane_shape = data.shape[-2:]
    if bitpix == 8 and plane_shape in {(480, 640), (1000, 992), (992, 992)}:
        decoded = data
        normalization = "fits-standard-byte-order"
    elif bitpix == 16 and plane_shape == (480, 640) and "PULNIX" in camera:
        decoded = data.byteswap()
        normalization = "pulnix-little-endian-byteswap"
    elif bitpix == 32 and plane_shape == (1000, 992) and "PULNIX" in camera:
        decoded = data.byteswap()
        normalization = "pulnix-little-endian-byteswap"
    elif bitpix == 32 and plane_shape == (992, 992) and "IMPERX" in camera:
        decoded = data
        normalization = "fits-standard-byte-order"
    else:
        raise ValueError(
            "Unsupported FITS layout: "
            f"BITPIX={bitpix}, shape={data.shape}, CAMERA={camera or '<missing>'}"
        )

    values = np.asarray(decoded, dtype=np.float64)
    values[~np.isfinite(values)] = np.nan

    # Exact integer endpoints commonly indicate saturation/corrupt sentinels.
    if bitpix in (8, 16, 32):
        info = np.iinfo({8: np.uint8, 16: np.int16, 32: np.int32}[bitpix])
        values[(values == info.min) | (values == info.max)] = np.nan
    return values, normalization


def compute_cube_signals(data: np.ndarray, header: dict) -> dict[str, np.ndarray]:
    """Return all diagnostic signal definitions for a two-plane FITS cube."""
    if data.ndim != 3 or data.shape[0] != 2:
        raise ValueError(f"Expected a two-plane cube, got shape {data.shape}")

    plane0 = data[0]
    plane1 = data[1]
    difference = plane0 - plane1
    denominator = plane0 + plane1
    vi = np.full(plane0.shape, np.nan, dtype=np.float64)
    valid = np.isfinite(plane0) & np.isfinite(plane1) & (denominator != 0)
    np.divide(difference, denominator, out=vi, where=valid)

    calibration = _parse_calibration(header)
    calibrated = np.full(plane0.shape, np.nan, dtype=np.float64)
    if calibration is not None:
        calibrated = vi * calibration

    return {
        "plane0": plane0,
        "plane1": plane1,
        "difference": difference,
        "vi": vi,
        "calibrated_vi": calibrated,
    }


def select_fits_signal(
    data: np.ndarray,
    header: dict,
    signal: str | None,
    plane: int | None = None,
) -> tuple[np.ndarray, str, str, str]:
    """Select one image and return image, definition, unit, calibration state."""
    if data.ndim == 2:
        if signal not in (None, "plane0"):
            raise ValueError(f"Signal {signal!r} is invalid for a single-plane FITS image")
        return data, "stored-longitudinal-image", "detector_count_proxy", "uncalibrated"

    if data.ndim != 3 or data.shape[0] != 2:
        raise ValueError(f"Unsupported FITS cube shape {data.shape}")

    if signal is None and plane is not None:
        if plane not in (0, 1):
            raise ValueError(f"Requested plane {plane} out of range for two-plane cube")
        signal = f"plane{plane}"
    if signal is None:
        raise ValueError(
            "Two-plane FITS processing requires explicit --fit-signal; "
            "diagnostic selection is pending"
        )
    if signal not in FITS_SIGNAL_CHOICES:
        raise ValueError(f"Unsupported FITS signal {signal!r}")

    signals = compute_cube_signals(data, header)
    if signal in ("plane0", "plane1", "difference"):
        return signals[signal], signal, "detector_count_proxy", "uncalibrated"
    if signal == "vi":
        return signals[signal], "(plane0-plane1)/(plane0+plane1)", "dimensionless", "derived"
    if _parse_calibration(header) is None:
        raise ValueError("calibrated_vi requested but CALIBRAT is absent or invalid")
    return (
        signals[signal],
        "CALIBRAT*(plane0-plane1)/(plane0+plane1)",
        "header_calibrated_proxy",
        "header-calibrated-unvalidated",
    )


def _parse_fits_date(header: dict, path: Path | None = None) -> pd.Timestamp:
    """Extract observation date from FITS header.

    Prefer ``T_START``; fall back to ``TIME_POS``. Both are commonly written as
    ``YYYY-M-D H:M:S``. For audited files whose time cards are absent, accept
    the filename timestamp only when its date agrees with an eight-digit date
    directory in the path.
    """
    raw = header.get("T_START") or header.get("TIME_POS")
    if raw is not None:
        return pd.to_datetime(str(raw))
    if path is None:
        raise ValueError("FITS header lacks T_START and TIME_POS")

    folder_tokens = [part for part in path.parts if re.fullmatch(r"20\d{6}", part)]
    filename_match = re.search(
        r"(?:npl|spl)(\d{6})(\d{6})(?:\(\d+\))?$", path.stem.lower()
    )
    if not folder_tokens or filename_match is None:
        raise ValueError(
            "FITS header lacks T_START/TIME_POS and path has no auditable timestamp"
        )
    folder_date = pd.to_datetime(folder_tokens[-1], format="%Y%m%d")
    filename_time = pd.to_datetime(
        "".join(filename_match.groups()), format="%y%m%d%H%M%S"
    )
    if folder_date.date() != filename_time.date():
        raise ValueError(
            "FITS header lacks T_START/TIME_POS and filename/path dates disagree"
        )
    return filename_time


def _hemisphere_from_header(header: dict, filename: str) -> str | None:
    """Return 'N' or 'S' from HSOS_NO or filename, or None if ambiguous."""
    hsos_no = str(header.get("HSOS_NO", "")).lower()
    name = Path(filename).stem.lower()
    token = f"{hsos_no} {name}"
    if "npl" in token:
        return "N"
    if "spl" in token:
        return "S"
    return None


def _view_type_from_header(header: dict, filename: str) -> str:
    """Infer view type from CONTENT keyword or filename prefix."""
    content = str(header.get("CONTENT", "")).strip().upper()
    if content in ("L", "S"):
        return content
    name = Path(filename).stem.lower()
    if name.startswith("l"):
        return "L"
    if name.startswith("s"):
        return "S"
    return "L"


def _should_skip_fits(
    header: dict,
    filename: str,
    include_wpl: bool,
    include_small_view: bool = False,
) -> str | None:
    """Return a skip reason, or None if the file should be processed."""
    hsos_no = str(header.get("HSOS_NO", "")).lower()
    name = Path(filename).stem.lower()
    token = f"{hsos_no} {name}"

    if not include_wpl and "wpl" in token:
        return "wpl file skipped by default"

    if "npl" not in token and "spl" not in token:
        return "no polar hemisphere marker (npl/spl)"

    content = str(header.get("CONTENT", "")).strip().upper()
    if content and content not in {"L", "S"}:
        return f"non-longitudinal CONTENT={content!r}"

    view = _view_type_from_header(header, filename)
    if view == "S" and not include_small_view:
        return "small-view/filtergram file skipped by default"

    return None


def parse_fits_meta(path: Path, header: dict, data: np.ndarray) -> dict:
    """Build metadata dict from FITS header and data shape.

    Raises ValueError for files that should not be processed (e.g. non-polar
    or small-view/filtergram).
    """
    hemisphere = _hemisphere_from_header(header, path.name)
    if hemisphere is None:
        raise ValueError("Could not determine hemisphere from FITS header")

    date = _parse_fits_date(header, path)
    view_type = _view_type_from_header(header, path.name)

    if data.ndim not in (2, 3):
        raise ValueError(f"Unsupported FITS data ndim={data.ndim}")

    if data.ndim == 3:
        # (planes, height, width) for the SMFT two-plane cubes.
        n_planes = data.shape[0]
        plane_shape = (data.shape[1], data.shape[2])
    else:
        n_planes = 1
        plane_shape = data.shape

    return {
        "date": date.strftime("%Y-%m-%d"),
        "year": date.year,
        "month": date.month,
        "day": date.day,
        "hemisphere": hemisphere,
        "view_type": view_type,
        "sequence": "",
        "shape": plane_shape,
        "n_planes": n_planes,
        "bitpix": int(header.get("BITPIX", 0)),
        "camera": str(header.get("CAMERA", "")).strip() or "unknown",
    }


def _instrument_epoch(shape: tuple[int, int], camera: str, year: int) -> str:
    """Identify a verified acquisition cohort without merging archive gaps."""
    camera_upper = camera.upper()
    if shape == (480, 640) and "PULNIX" in camera_upper:
        if 2002 <= year <= 2008:
            return "pulnix_fit16_2002_2008"
        if 2011 <= year <= 2014:
            return "pulnix_fit16_2011_2014"
        raise ValueError(
            "Unsupported PULNIX FIT16 acquisition year "
            f"{year} for shape={shape}, camera={camera}"
        )
    if shape == (1000, 992) and "PULNIX" in camera_upper:
        if 2009 <= year <= 2010:
            return "pulnix_fit32_2009_2010"
        raise ValueError(
            "Unsupported PULNIX FIT32 acquisition year "
            f"{year} for shape={shape}, camera={camera}"
        )
    if shape == (992, 992) and "IMPERX" in camera_upper:
        if year == 2014:
            return "imperx_fit32_2014"
        if 2015 <= year <= 2017:
            return "imperx_fit32_2015_2017"
        if 2018 <= year <= 2026:
            return "imperx_fit32_2018_2026"
        raise ValueError(
            "Unsupported IMPERX acquisition year "
            f"{year} for shape={shape}, camera={camera}"
        )
    raise ValueError(f"Unsupported instrument epoch for shape={shape}, camera={camera}")


# ---------------------------------------------------------------------------
# Aperture & feature extraction (format-agnostic)
# ---------------------------------------------------------------------------
def _select_aperture(
    arr: np.ndarray,
    hemisphere: str,
    cap_rows: int,
    center_box: tuple[int, int],
) -> dict:
    """Select polar-cap and quiet-reference boxes.

    For the single-plane 640x480 and legacy 512x512 polar views, the polar
    cap is the top/bottom ``cap_rows`` rows. The quiet reference is a box at
    the image centre.
    """
    rows, cols = arr.shape
    center_h, center_w = center_box

    if center_h > rows or center_w > cols:
        raise ValueError(
            f"Center box {center_h}x{center_w} does not fit in image {rows}x{cols}"
        )
    if cap_rows > rows:
        raise ValueError(f"Polar cap rows {cap_rows} exceed image height {rows}")

    if hemisphere == "N":
        cap = arr[:cap_rows, :]
    else:
        cap = arr[rows - cap_rows :, :]

    r0 = (rows - center_h) // 2
    c0 = (cols - center_w) // 2
    center = arr[r0 : r0 + center_h, c0 : c0 + center_w]

    return {
        "cap": cap,
        "center": center,
        "cap_mask": np.ones(cap.shape, dtype=bool),
        "center_mask": np.ones(center.shape, dtype=bool),
    }


def _select_center_aperture(
    arr: np.ndarray,
    hemisphere: str,
    radius: int,
) -> dict:
    """Select a circular polar-cap aperture around the image centre.

    Used for the 992x... two-plane epochs where the telescope points at the
    pole (latitude ~90 degrees) and the pole is near the image centre.
    ``hemisphere`` still determines whether we label the measurement as N/S.
    """
    rows, cols = arr.shape
    if radius <= 0 or radius > min(rows, cols) // 2:
        raise ValueError(f"Invalid polar radius {radius} for image {rows}x{cols}")

    y, x = np.ogrid[:rows, :cols]
    cy, cx = rows / 2.0, cols / 2.0
    mask = (y - cy) ** 2 + (x - cx) ** 2 <= radius**2
    # Quiet reference is an annulus just outside the polar cap.
    inner = radius
    outer = min(rows, cols) / 2.0
    annulus_mask = ((y - cy) ** 2 + (x - cx) ** 2 >= inner**2) & (
        (y - cy) ** 2 + (x - cx) ** 2 <= outer**2
    )
    return {
        "cap": arr,
        "center": arr,
        "cap_mask": mask,
        "center_mask": annulus_mask,
    }


def _select_center_box_aperture(
    arr: np.ndarray,
    box: tuple[int, int],
) -> dict:
    """Select a centered rectangular aperture and an outer reference region."""
    rows, cols = arr.shape
    box_h, box_w = box
    if box_h <= 0 or box_w <= 0 or box_h > rows or box_w > cols:
        raise ValueError(f"Invalid center box {box_h}x{box_w} for image {rows}x{cols}")

    r0 = (rows - box_h) // 2
    c0 = (cols - box_w) // 2
    cap_mask = np.zeros(arr.shape, dtype=bool)
    cap_mask[r0 : r0 + box_h, c0 : c0 + box_w] = True

    y, x = np.ogrid[:rows, :cols]
    cy, cx = rows / 2.0, cols / 2.0
    inner = max(box_h, box_w) / 2.0
    outer = min(rows, cols) / 2.0
    reference_mask = ((y - cy) ** 2 + (x - cx) ** 2 >= inner**2) & (
        (y - cy) ** 2 + (x - cx) ** 2 <= outer**2
    )
    return {
        "cap": arr,
        "center": arr,
        "cap_mask": cap_mask,
        "center_mask": reference_mask,
    }


def _signed_mean_valid(arr: np.ndarray) -> float:
    """Mean of non-zero pixels; zero is treated as missing/padding."""
    valid = arr != 0
    if valid.sum() == 0:
        raise ValueError("No valid (non-zero) pixels")
    return float(arr[valid].mean())


def _median_valid(arr: np.ndarray) -> float:
    """Median of non-zero pixels; robust to outliers in the center reference."""
    valid = arr[arr != 0]
    if valid.size == 0:
        raise ValueError("No valid (non-zero) pixels")
    return float(np.median(valid))


def extract_features(
    arr: np.ndarray,
    hemisphere: str,
    cap_rows: int | None = None,
    center_box: tuple[int, int] | None = None,
    center_radius: int | None = None,
    aperture_mode: str | None = None,
    aperture_box: tuple[int, int] | None = None,
) -> dict:
    """Compute signed and unsigned polar-cap proxies.

    Because NPL/SPL files in this archive do not reliably show opposite
    magnetic polarity in the top/bottom caps, the unsigned pixel-level absolute
    value (after subtracting a robust zero-offset estimate) is used as the
    primary polar-field strength proxy.
    """
    if aperture_mode == "center-box" or aperture_box is not None:
        if aperture_box is None:
            raise ValueError("center-box aperture requires aperture_box")
        aperture = _select_center_box_aperture(arr, aperture_box)
    elif aperture_mode == "center-circle" or center_radius is not None:
        if center_radius is None:
            raise ValueError("center-circle aperture requires center_radius")
        aperture = _select_center_aperture(arr, hemisphere, center_radius)
    else:
        if cap_rows is None or center_box is None:
            raise ValueError("Either center_radius or (cap_rows, center_box) required")
        aperture = _select_aperture(arr, hemisphere, cap_rows, center_box)

    cap = aperture["cap"]
    center = aperture["center"]
    cap_mask = aperture["cap_mask"] & np.isfinite(cap) & (cap != 0)
    center_mask = aperture["center_mask"] & np.isfinite(center) & (center != 0)
    if not cap_mask.any():
        raise ValueError("No valid pixels in polar aperture")
    if not center_mask.any():
        raise ValueError("No valid pixels in reference aperture")

    cap_valid = cap[cap_mask]
    center_valid = center[center_mask]
    raw_mean = float(cap_valid.mean())
    center_mean = float(center_valid.mean())
    center_median = float(np.median(center_valid))

    # Unsigned pixel-level absolute value after zero-offset correction.
    # We use the median rather than the mean to avoid overweighting strong
    # active-region fields that leak into the nominally polar cap rows.
    field_mean_abs = float(np.median(np.abs(cap_valid - center_median)))
    aperture_pixels = int(aperture["cap_mask"].sum())
    valid_ratio = float(cap_mask.sum() / aperture_pixels)

    return {
        "field_mean_raw": raw_mean,
        "field_mean_center": center_mean,
        "field_mean_corrected": raw_mean - center_mean,
        "field_mean_abs": field_mean_abs,
        "valid_pixel_ratio": valid_ratio,
    }


def _validate_fits_features(features: dict) -> None:
    """Reject FITS frames whose selected signal has no measurable spread."""
    strength = float(features["field_mean_abs"])
    if not np.isfinite(strength) or strength <= 0:
        raise ValueError(
            "degenerate polar signal: field_mean_abs must be finite and positive"
        )


def _require_validated_geometry(instrument_epoch: str, allow_unvalidated: bool) -> None:
    """Stop production use of epochs that do not yet have a solar WCS mask."""
    if instrument_epoch in UNVALIDATED_GEOMETRY_EPOCHS and not allow_unvalidated:
        raise ValueError(
            f"{instrument_epoch} has no validated solar WCS/fixed-latitude mask; "
            "use --allow-unvalidated-geometry only for diagnostic output"
        )


# ---------------------------------------------------------------------------
# Per-file processors
# ---------------------------------------------------------------------------
def process_file(
    path: Path,
    polar_dir: Path,
    fit_cap_rows: int = DEFAULT_FITS_CAP_ROWS,
    fit_center_box: tuple[int, int] = DEFAULT_FITS_CENTER_BOX,
    fit_plane: int | None = None,
    fit_signal: str | None = None,
    fit_aperture_mode: str = "auto",
    fit_center_radius: int = DEFAULT_FITS_CENTER_RADIUS,
    fit_aperture_box: tuple[int, int] = DEFAULT_FITS_APERTURE_BOX,
    skip_wpl: bool = True,
    include_small_view: bool = False,
    allow_unvalidated_geometry: bool = False,
) -> dict:
    """Process a single file and return a record dict.

    Dispatches between the legacy .dat reader and the FITS reader based on the
    file extension.
    """
    ext = path.suffix.lower()
    if ext in (".fit", ".fits"):
        return process_file_fits(
            path,
            polar_dir,
            fit_cap_rows=fit_cap_rows,
            fit_center_box=fit_center_box,
            fit_plane=fit_plane,
            fit_signal=fit_signal,
            fit_aperture_mode=fit_aperture_mode,
            fit_center_radius=fit_center_radius,
            fit_aperture_box=fit_aperture_box,
            skip_wpl=skip_wpl,
            include_small_view=include_small_view,
            allow_unvalidated_geometry=allow_unvalidated_geometry,
        )

    if ext == ".dat":
        return process_file_dat(path, polar_dir)

    raise ValueError(f"Unsupported file extension: {ext}")


def process_file_dat(path: Path, polar_dir: Path) -> dict:
    """Process a single legacy .dat file."""
    meta = parse_path(path, polar_dir)
    if meta is None:
        raise ValueError("Could not parse date/hemisphere/view from path")

    # Guard against files that are actually FITS despite the .dat extension.
    header_peek = path.read_bytes()[:6]
    if header_peek == b"SIMPLE":
        raise ValueError("File starts with FITS header despite .dat extension")

    shape = _infer_image_shape(path)
    if shape is None:
        raise ValueError("Could not infer image shape from file size")

    arr = _read_int16_image(path, shape)
    feats = extract_features(
        arr,
        meta["hemisphere"],
        cap_rows=LARGE_CAP_ROWS,
        center_box=LARGE_CENTER_BOX,
    )

    return {
        "date": f"{meta['year']:04d}-{meta['month']:02d}-{meta['day']:02d}",
        "year": meta["year"],
        "month": meta["month"],
        "day": meta["day"],
        "hemisphere": meta["hemisphere"],
        "view_type": meta["view_type"],
        "sequence": meta["sequence"],
        "instrument_epoch": "legacy_dat_1987_2001",
        "camera": "SMFT legacy detector",
        "source_format": "dat-int16-le",
        "signal_definition": "stored-longitudinal-image",
        "signal_unit": "detector_count_proxy",
        "calibration_status": "uncalibrated",
        "byte_order_normalization": "legacy-little-endian",
        **feats,
        "file_path": str(path),
    }


def process_file_fits(
    path: Path,
    polar_dir: Path,
    fit_cap_rows: int = DEFAULT_FITS_CAP_ROWS,
    fit_center_box: tuple[int, int] = DEFAULT_FITS_CENTER_BOX,
    fit_plane: int | None = None,
    fit_signal: str | None = None,
    fit_aperture_mode: str = "auto",
    fit_center_radius: int = DEFAULT_FITS_CENTER_RADIUS,
    fit_aperture_box: tuple[int, int] = DEFAULT_FITS_APERTURE_BOX,
    skip_wpl: bool = True,
    include_small_view: bool = False,
    allow_unvalidated_geometry: bool = False,
) -> dict:
    """Process a single FITS file."""
    raw_data, header = _read_fits_image(path)

    skip_reason = _should_skip_fits(
        header, path.name, not skip_wpl, include_small_view
    )
    if skip_reason:
        raise ValueError(skip_reason)

    meta = parse_fits_meta(path, header, raw_data)
    instrument_epoch = _instrument_epoch(
        meta["shape"], meta["camera"], meta["year"]
    )
    _require_validated_geometry(instrument_epoch, allow_unvalidated_geometry)
    data, normalization = normalize_fits_data(raw_data, header)
    arr, signal_definition, signal_unit, calibration_status = select_fits_signal(
        data, header, fit_signal, fit_plane
    )

    aperture_mode = fit_aperture_mode
    if aperture_mode == "auto":
        aperture_mode = "polar-strip" if arr.shape == (480, 640) else "center-circle"

    if aperture_mode == "polar-strip":
        feats = extract_features(
            arr,
            meta["hemisphere"],
            cap_rows=fit_cap_rows,
            center_box=fit_center_box,
            aperture_mode="polar-strip",
        )
    elif aperture_mode == "center-circle":
        feats = extract_features(
            arr,
            meta["hemisphere"],
            center_radius=fit_center_radius,
            aperture_mode="center-circle",
        )
    elif aperture_mode == "center-box":
        feats = extract_features(
            arr,
            meta["hemisphere"],
            aperture_mode="center-box",
            aperture_box=fit_aperture_box,
        )
    else:
        raise ValueError(f"Unsupported aperture mode {aperture_mode!r}")

    _validate_fits_features(feats)

    return {
        "date": meta["date"],
        "year": meta["year"],
        "month": meta["month"],
        "day": meta["day"],
        "hemisphere": meta["hemisphere"],
        "view_type": meta["view_type"],
        "sequence": meta["sequence"],
        "instrument_epoch": instrument_epoch,
        "camera": meta["camera"],
        "source_format": f"fits-bitpix{meta['bitpix']}",
        "signal_definition": signal_definition,
        "signal_unit": signal_unit,
        "calibration_status": calibration_status,
        "byte_order_normalization": normalization,
        **feats,
        "file_path": str(path),
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def aggregate_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Average multiple observations of the same hemisphere on the same day."""
    metadata_cols = [
        "instrument_epoch",
        "camera",
        "source_format",
        "signal_definition",
        "signal_unit",
        "calibration_status",
        "byte_order_normalization",
    ]
    grouped = (
        df.groupby(["date", "hemisphere", *metadata_cols], dropna=False)
        .agg(
            field_mean_raw=("field_mean_raw", "mean"),
            field_mean_center=("field_mean_center", "mean"),
            field_mean_corrected=("field_mean_corrected", "mean"),
            field_mean_abs=("field_mean_abs", "mean"),
            valid_pixel_ratio=("valid_pixel_ratio", "mean"),
            n_obs=("file_path", "count"),
        )
        .reset_index()
    )
    grouped["date"] = pd.to_datetime(grouped["date"])
    return grouped


def aggregate_monthly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Average daily values into monthly means per hemisphere."""
    df_daily = df_daily.copy()
    df_daily["year"] = df_daily["date"].dt.year
    df_daily["month"] = df_daily["date"].dt.month
    metadata_cols = [
        "instrument_epoch",
        "camera",
        "source_format",
        "signal_definition",
        "signal_unit",
        "calibration_status",
        "byte_order_normalization",
    ]
    grouped = (
        df_daily.groupby(
            ["year", "month", "hemisphere", *metadata_cols], dropna=False
        )
        .agg(
            field_mean_raw=("field_mean_raw", "mean"),
            field_mean_center=("field_mean_center", "mean"),
            field_mean_corrected=("field_mean_corrected", "mean"),
            field_mean_abs=("field_mean_abs", "mean"),
            n_days=("date", "nunique"),
        )
        .reset_index()
    )
    # Retain signed corrected mean as an exploratory diagnostic, but use the
    # unsigned pixel-level absolute value as the primary proxy.
    grouped["polarity_strength"] = grouped["field_mean_corrected"].abs()
    return grouped


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract polar-cap magnetic field proxy from Huairou SMFT magnetograms."
    )
    parser.add_argument(
        "--polar-dir",
        required=True,
        help="Root directory containing YYYY/MON/DD/*.{dat,fit,fits} files.",
    )
    parser.add_argument(
        "--output",
        default="./data/huairou_polar_precursor_daily.csv",
        help="Output daily CSV path.",
    )
    parser.add_argument(
        "--monthly-output",
        default="./data/huairou_polar_precursor_monthly.csv",
        help="Output monthly CSV path.",
    )
    parser.add_argument(
        "--errors-output",
        default="./artifacts/polar_processing_errors.jsonl",
        help="Path for error log.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel workers.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1987,
        help="Start year (inclusive).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2026,
        help="End year (inclusive).",
    )
    parser.add_argument(
        "--include-small-view",
        action="store_true",
        help="Also process small-view 's*' frames (default: skipped).",
    )
    parser.add_argument(
        "--skip-wpl",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip wpl whole-disk frames (default: true).",
    )
    parser.add_argument(
        "--fit-cap-rows",
        type=int,
        default=DEFAULT_FITS_CAP_ROWS,
        help="Number of rows defining the polar cap for 640x480 FITS images.",
    )
    parser.add_argument(
        "--fit-center-box",
        type=_parse_center_box,
        default="320,240",
        help="Central quiet-reference box for 640x480 FITS images, as 'WIDTH,HEIGHT'.",
    )
    parser.add_argument(
        "--fit-plane",
        type=int,
        default=None,
        help="Compatibility selector for plane 0/1; no default while diagnosis is pending.",
    )
    parser.add_argument(
        "--fit-signal",
        choices=FITS_SIGNAL_CHOICES,
        default=None,
        help="Explicit signal for NAXIS3=2 cubes; required until diagnosis is confirmed.",
    )
    parser.add_argument(
        "--fit-aperture-mode",
        choices=FITS_APERTURE_CHOICES,
        default="auto",
        help="Aperture geometry (auto uses strip for 640x480, circle otherwise).",
    )
    parser.add_argument(
        "--fit-center-radius",
        type=int,
        default=DEFAULT_FITS_CENTER_RADIUS,
        help="Polar-cap radius in pixels for 992x... two-plane FITS images.",
    )
    parser.add_argument(
        "--fit-aperture-box",
        type=_parse_center_box,
        default="200,200",
        help="Centered polar aperture box as 'WIDTH,HEIGHT'.",
    )
    parser.add_argument(
        "--allow-unvalidated-geometry",
        action="store_true",
        help=(
            "Allow diagnostic output for epochs without a validated solar WCS/"
            "fixed-latitude mask; never use such output as a production precursor."
        ),
    )
    args = parser.parse_args()

    polar_dir = Path(args.polar_dir)
    out_path = Path(args.output)
    monthly_path = Path(args.monthly_output)
    errors_path = Path(args.errors_output)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    monthly_path.parent.mkdir(parents=True, exist_ok=True)
    errors_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = []
    for year in range(args.start_year, args.end_year + 1):
        year_dir = polar_dir / str(year)
        if not year_dir.exists():
            continue
        for path in year_dir.rglob("*"):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext not in (".dat", ".fit", ".fits"):
                continue
            name = path.stem.lower()
            # Skip non-polar files unless explicitly requested.
            if "npl" not in name and "spl" not in name and args.skip_wpl:
                continue
            if "wpl" in name and args.skip_wpl:
                continue
            if name.startswith("s") and not args.include_small_view:
                continue
            candidates.append(path)

    print(
        f"Found {len(candidates)} polar candidate files for "
        f"{args.start_year}-{args.end_year}"
    )

    records: list[dict] = []
    errors: list[dict] = []

    process_kwargs = {
        "fit_cap_rows": args.fit_cap_rows,
        "fit_center_box": args.fit_center_box,
        "fit_plane": args.fit_plane,
        "fit_signal": args.fit_signal,
        "fit_aperture_mode": args.fit_aperture_mode,
        "fit_center_radius": args.fit_center_radius,
        "fit_aperture_box": args.fit_aperture_box,
        "skip_wpl": args.skip_wpl,
        "include_small_view": args.include_small_view,
        "allow_unvalidated_geometry": args.allow_unvalidated_geometry,
    }

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                process_file, p, polar_dir, **process_kwargs
            ): p
            for p in candidates
        }
        for completed_idx, future in enumerate(as_completed(futures), start=1):
            path = futures[future]
            try:
                rec = future.result()
                records.append(rec)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append({"file": str(path), "error": repr(exc)})
            if completed_idx % 500 == 0:
                print(f"Processed {completed_idx}/{len(candidates)} files")

    with errors_path.open("w", encoding="utf-8") as fh:
        for err in errors:
            fh.write(json.dumps(err, ensure_ascii=False) + "\n")
    if errors:
        print(f"Logged {len(errors)} skipped/error files to {errors_path}")

    if not records:
        print("No valid polar-field records extracted.")
        return

    df = pd.DataFrame(records)
    df_daily = aggregate_daily(df)[DAILY_COLUMNS]
    df_daily.to_csv(out_path, index=False)
    print(f"Wrote {len(df_daily)} daily rows to {out_path}")

    df_monthly = aggregate_monthly(df_daily)[MONTHLY_COLUMNS]
    df_monthly.to_csv(monthly_path, index=False)
    print(f"Wrote {len(df_monthly)} monthly rows to {monthly_path}")


if __name__ == "__main__":
    main()
