"""Pinned historical evaluation inputs for the report; never a new review."""

import csv
import json
import shutil
from pathlib import Path

import numpy as np

from .assets import child, sha
from .engine import PACKAGE


def reference_inventory():
    return json.loads((PACKAGE / "report_reference_inventory.json").read_text("utf-8"))


def verify_reference(root: Path):
    inventory = reference_inventory()
    for row in inventory["files"]:
        p = child(root, row["path"])
        if p.stat().st_size != row["bytes"] or sha(p) != row["sha256"]:
            raise ValueError("Frozen report reference differs: " + row["path"])
    proof = json.loads((root / "proof.json").read_text("utf-8"))
    model = next(p for p in proof["products"] if p["path"] == "models.joblib")
    if (
        model["sha256"] != inventory["origin_checkpoint_sha256"]
        or proof["contract_sha256"] != inventory["original_contract_sha256"]
    ):
        raise ValueError("Historical report reference belongs to a different model")
    return inventory


def import_reference(source: Path, assets: Path):
    """Explicit local byte copy; reject replacements before writing anything."""
    inventory = verify_reference(source)
    destination = child(assets, "report_reference")
    if destination.exists():
        raise ValueError("Report reference already exists; never overwrite")
    destination.mkdir(parents=True, exist_ok=False)
    for row in inventory["files"]:
        shutil.copyfile(child(source, row["path"]), child(destination, row["path"]))
    verify_reference(destination)
    return {
        "status": "verified",
        "files": len(inventory["files"]),
        "no_fit_or_issue": True,
    }


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def load_reference(assets: Path, rows, predictions):
    """Bind the frozen metrics to every current portable-model test score."""
    root = child(assets, "report_reference")
    inventory = verify_reference(root)
    heldout = csv_rows(root / "heldout_predictions.csv")
    if len(heldout) != len(rows) or len({r["sample_id"] for r in heldout}) != len(rows):
        raise ValueError("Historical report cohort count differs")
    lookup = {r["sample_id"]: r for r in heldout}
    for row, score in zip(rows, predictions, strict=True):
        old = lookup[row["sample_id"]]
        for name in (
            "target_start_utc",
            "target_end_utc",
            "label_c1",
            "label_m1",
            "split",
        ):
            if old[name] != row[name]:
                raise ValueError("Historical report window or label differs")
        for prefix, target in (("C", "C1.0+"), ("M", "M1.0+")):
            if not np.isclose(
                float(old[f"p_{prefix}_selected_calibrated"]),
                score[target],
                rtol=0,
                atol=1e-12,
            ):
                raise ValueError("Historical score differs from pinned evaluation")
    return {
        "inventory": inventory,
        "metrics": csv_rows(root / "metrics_primary.csv"),
        "years": csv_rows(root / "year_metrics.csv"),
        "importance": csv_rows(root / "importance.csv"),
        "intervals": csv_rows(root / "bootstrap_ci.csv"),
        "summary": json.loads((root / "summary.json").read_text("utf-8")),
        "calibration": json.loads((root / "calibration.json").read_text("utf-8")),
        "selected_config": json.loads(
            (root / "selected_config_lock.json").read_text("utf-8")
        ),
    }
