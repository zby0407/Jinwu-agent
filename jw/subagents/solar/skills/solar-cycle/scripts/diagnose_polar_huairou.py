#!/usr/bin/env python3
"""Generate the parameter-selection diagnostics for Huairou SMFT FITS data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import load_polar_huairou as loader
import matplotlib.pyplot as plt

SIGNALS = loader.FITS_SIGNAL_CHOICES


def _fits_files(path: Path) -> list[Path]:
    return sorted(
        p
        for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in {".fit", ".fits"}
    )


def _large_polar(files: list[Path]) -> list[Path]:
    return [
        p
        for p in files
        if p.stem.lower().startswith("l")
        and ("npl" in p.stem.lower() or "spl" in p.stem.lower())
    ]


def _cohorts(root: Path) -> dict[str, list[Path]]:
    files_2002 = _large_polar(_fits_files(root / "2002"))
    return {
        "2002_apr17": [
            p
            for p in files_2002
            if "apr" in {part.lower() for part in p.parts}
            and "17" in p.parts
        ],
        "2002_all_large": files_2002,
        "2009_all": _large_polar(_fits_files(root / "2009")),
        "2015_oct02": _large_polar(_fits_files(root / "2015" / "10" / "20151002")),
    }


def _apertures(single_plane: bool) -> list[dict]:
    if single_plane:
        return [
            {"name": f"polar-strip-{rows}", "mode": "polar-strip", "rows": rows}
            for rows in (80, 100, 120)
        ]
    return [
        *[
            {"name": f"center-circle-{radius}", "mode": "center-circle", "radius": radius}
            for radius in (100, 150, 200)
        ],
        {"name": "center-box-200x200", "mode": "center-box", "box": (200, 200)},
        {"name": "center-box-300x300", "mode": "center-box", "box": (300, 300)},
        {"name": "polar-strip-100", "mode": "polar-strip", "rows": 100},
    ]


def _features(image: np.ndarray, hemisphere: str, aperture: dict) -> dict:
    if aperture["mode"] == "polar-strip":
        return loader.extract_features(
            image,
            hemisphere,
            cap_rows=aperture["rows"],
            center_box=loader.DEFAULT_FITS_CENTER_BOX,
            aperture_mode="polar-strip",
        )
    if aperture["mode"] == "center-circle":
        return loader.extract_features(
            image,
            hemisphere,
            center_radius=aperture["radius"],
            aperture_mode="center-circle",
        )
    return loader.extract_features(
        image,
        hemisphere,
        aperture_mode="center-box",
        aperture_box=aperture["box"],
    )


def _quantiles(image: np.ndarray) -> dict[str, float]:
    values = image[np.isfinite(image) & (image != 0)]
    if values.size == 0:
        return {f"pixel_p{p}": np.nan for p in (1, 25, 50, 75, 99)}
    quantiles = np.percentile(values, [1, 25, 50, 75, 99])
    return {
        f"pixel_p{p}": float(value)
        for p, value in zip((1, 25, 50, 75, 99), quantiles, strict=True)
    }


def _plot_sample(
    cohort: str,
    path: Path,
    data: np.ndarray,
    header: dict,
    output_dir: Path,
) -> None:
    if data.ndim == 2:
        images = {"stored image": data}
    else:
        signals = loader.compute_cube_signals(data, header)
        images = {
            "plane0": signals["plane0"],
            "plane1": signals["plane1"],
            "difference": signals["difference"],
            "V/I": signals["vi"],
            "CALIBRAT x V/I": signals["calibrated_vi"],
        }

    fig, axes = plt.subplots(1, len(images), figsize=(5 * len(images), 4), squeeze=False)
    for axis, (label, image) in zip(axes[0], images.items(), strict=True):
        finite = image[np.isfinite(image)]
        lo, hi = np.percentile(finite, [1, 99])
        axis.imshow(image, origin="lower", cmap="gray", vmin=lo, vmax=hi)
        axis.set_title(label)
        axis.set_xticks([])
        axis.set_yticks([])
    fig.suptitle(f"{cohort}: {path.name}")
    fig.tight_layout()
    fig.savefig(output_dir / f"{cohort}_signal_slices.png", dpi=160)
    plt.close(fig)


def _plot_curves(daily: pd.DataFrame, output_dir: Path) -> None:
    for (cohort, signal), group in daily.groupby(["cohort", "signal"]):
        fig, axis = plt.subplots(figsize=(10, 5))
        for (aperture, hemisphere), sub in group.groupby(["aperture", "hemisphere"]):
            sub = sub.sort_values("date")
            axis.plot(
                pd.to_datetime(sub["date"]),
                sub["field_mean_abs"],
                marker="o",
                label=f"{aperture} {hemisphere}",
            )
        axis.set_title(f"{cohort} — {signal}")
        axis.set_ylabel("field_mean_abs")
        axis.grid(alpha=0.25)
        axis.legend(fontsize=7, ncol=2)
        fig.autofmt_xdate()
        fig.tight_layout()
        safe_signal = signal.replace("/", "_")
        fig.savefig(output_dir / f"{cohort}_{safe_signal}_apertures.png", dpi=160)
        plt.close(fig)


def _plot_valid_ratios(frame: pd.DataFrame, output_dir: Path) -> None:
    for (cohort, signal), group in frame.groupby(["cohort", "signal"]):
        apertures = sorted(group["aperture"].unique())
        values = [
            group.loc[group["aperture"] == aperture, "valid_pixel_ratio"].to_numpy()
            for aperture in apertures
        ]
        fig, axis = plt.subplots(figsize=(max(8, len(apertures) * 1.5), 5))
        axis.boxplot(values, tick_labels=apertures)
        axis.set_title(f"{cohort} — {signal}: valid-pixel ratio")
        axis.set_ylabel("valid_pixel_ratio")
        axis.tick_params(axis="x", rotation=25)
        axis.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        safe_signal = signal.replace("/", "_")
        fig.savefig(output_dir / f"{cohort}_{safe_signal}_valid_ratio.png", dpi=160)
        plt.close(fig)


def run(root: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    errors: list[dict] = []
    cohort_counts: dict[str, int] = {}

    for cohort, files in _cohorts(root).items():
        cohort_counts[cohort] = len(files)
        plotted = False
        for path in files:
            try:
                raw, header = loader._read_fits_image(path)
                skip_reason = loader._should_skip_fits(header, path.name, False)
                if skip_reason:
                    raise ValueError(skip_reason)
                meta = loader.parse_fits_meta(path, header, raw)
                data, normalization = loader.normalize_fits_data(raw, header)
                if not plotted:
                    _plot_sample(cohort, path, data, header, output_dir)
                    plotted = True

                if data.ndim == 2:
                    images = {"stored-image": data}
                    correlation = np.nan
                else:
                    images = loader.compute_cube_signals(data, header)
                    valid = np.isfinite(data[0]) & np.isfinite(data[1])
                    correlation = float(np.corrcoef(data[0][valid], data[1][valid])[0, 1])

                for signal, image in images.items():
                    for aperture in _apertures(data.ndim == 2):
                        feats = _features(image, meta["hemisphere"], aperture)
                        records.append(
                            {
                                "cohort": cohort,
                                "file": str(path),
                                "date": meta["date"],
                                "hemisphere": meta["hemisphere"],
                                "camera": meta["camera"],
                                "normalization": normalization,
                                "signal": signal,
                                "aperture": aperture["name"],
                                "plane_correlation": correlation,
                                **_quantiles(image),
                                **feats,
                            }
                        )
            except Exception as exc:  # pragma: no cover - real archive defense
                errors.append({"cohort": cohort, "file": str(path), "error": repr(exc)})

    frame = pd.DataFrame(records)
    if frame.empty:
        raise RuntimeError("No diagnostic records were generated")
    frame.to_csv(output_dir / "diagnostic_records.csv", index=False)

    daily = (
        frame.groupby(["cohort", "date", "hemisphere", "signal", "aperture"])
        .agg(
            field_mean_abs=("field_mean_abs", "mean"),
            valid_pixel_ratio=("valid_pixel_ratio", "mean"),
            n_obs=("file", "count"),
        )
        .reset_index()
    )
    daily.to_csv(output_dir / "diagnostic_daily.csv", index=False)

    summary = (
        frame.groupby(["cohort", "hemisphere", "signal", "aperture"])
        .agg(
            n_files=("file", "count"),
            n_days=("date", "nunique"),
            field_mean_abs_median=("field_mean_abs", "median"),
            field_mean_abs_std=("field_mean_abs", "std"),
            field_mean_abs_mad=(
                "field_mean_abs",
                lambda values: float(np.median(np.abs(values - np.median(values)))),
            ),
            valid_pixel_ratio_median=("valid_pixel_ratio", "median"),
            plane_correlation_median=("plane_correlation", "median"),
        )
        .reset_index()
    )
    summary.to_csv(output_dir / "diagnostic_summary.csv", index=False)
    _plot_curves(daily, output_dir)
    _plot_valid_ratios(frame, output_dir)

    wpl_2010 = [p for p in _fits_files(root / "2010") if "wpl" in p.stem.lower()]
    with (output_dir / "diagnostic_errors.jsonl").open("w", encoding="utf-8") as handle:
        for error in errors:
            handle.write(json.dumps(error, ensure_ascii=False) + "\n")

    correlations = (
        frame.dropna(subset=["plane_correlation"])
        .groupby("cohort")["plane_correlation"]
        .median()
        .to_dict()
    )
    normalizations = (
        frame.groupby("cohort")["normalization"].first().to_dict()
    )
    report = [
        "# Huairou SMFT FITS diagnostic report",
        "",
        "Production signal and apertures remain intentionally unselected.",
        "",
        "## Cohorts",
        "",
        *[f"- `{name}`: {count} files" for name, count in cohort_counts.items()],
        f"- `2010`: {len(wpl_2010)} FITS files, all classified as `wpl` and excluded",
        "",
        "## Verified decoding facts",
        "",
        *[
            f"- `{name}`: `{normalization}`"
            for name, normalization in normalizations.items()
        ],
        *[
            f"- `{name}` median plane correlation: {correlation:.8f}"
            for name, correlation in correlations.items()
        ],
        "- Signal and aperture defaults are deliberately left pending user review.",
        "",
        "## Outputs",
        "",
        "- `diagnostic_records.csv`: per-file signal/aperture measurements",
        "- `diagnostic_daily.csv`: daily N/S curves",
        "- `diagnostic_summary.csv`: stability and valid-pixel summaries",
        "- `*_signal_slices.png`: plane and derived-signal image comparisons",
        "- `*_apertures.png`: aperture overlay plots",
        "- `*_valid_ratio.png`: valid-pixel-ratio distributions",
        "",
        f"Errors or explicit skips: {len(errors)} (see `diagnostic_errors.jsonl`).",
    ]
    (output_dir / "README.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--polar-dir", required=True)
    parser.add_argument("--output-dir", default="artifacts/aperture_test")
    args = parser.parse_args()
    run(Path(args.polar_dir), Path(args.output_dir))


if __name__ == "__main__":
    main()
