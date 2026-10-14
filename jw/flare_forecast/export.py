"""Explicit conversion of the pinned trusted local checkpoint; never refit."""

import gzip
import hashlib
import json
from pathlib import Path

from .assets import child, sha
from .engine import inventory


def export_forest(root: Path) -> dict:
    source = child(root, "model/selected_rf.joblib")
    expected = next(
        r for r in inventory()["files"] if r["path"] == "model/selected_rf.joblib"
    )
    if sha(source) != expected["sha256"]:
        raise ValueError("Refuse to deserialize an unregistered checkpoint")
    output = child(root, "model/forest.json.gz")
    binding = child(root, "model/forest_binding.json")
    if output.exists() or binding.exists():
        raise ValueError("Never overwrite a portable checkpoint")
    import joblib
    from .model import FEATURES, FrozenForest

    bundle = joblib.load(source)
    if bundle["feature_columns"] != FEATURES or bundle["target_offset_hours"] != [
        0,
        24,
    ]:
        raise ValueError("Checkpoint feature or target semantics differ")
    forest = bundle["selected_model"]
    document = {
        "schema_version": "jw-frozen-flare-forest-v1",
        "features": FEATURES,
        "classes": [int(x) for x in forest.classes_],
        "temperature": bundle["selected_temperature"],
        "selected_model_id": bundle["selected_model_id"],
        "fit_ranges": bundle["fit_ranges"],
        "origin_checkpoint_sha256": expected["sha256"],
        "origin_contract_sha256": bundle["contract_sha256"],
        "trees": [
            {
                "left": e.tree_.children_left.tolist(),
                "right": e.tree_.children_right.tolist(),
                "feature": e.tree_.feature.tolist(),
                "threshold": e.tree_.threshold.tolist(),
                "value": e.tree_.value[:, 0, :].tolist(),
            }
            for e in forest.estimators_
        ],
        "scientific_status": "not_reviewed",
        "formal_ResearchReviewStore_chain": False,
        "operational_forecast_validated": False,
        "no_refit_or_recalibration": True,
    }
    FrozenForest(document)
    raw = json.dumps(document, allow_nan=False, separators=(",", ":")).encode("utf-8")
    output.write_bytes(gzip.compress(raw, mtime=0))
    record = {
        "sha256": sha(output),
        "json_sha256": hashlib.sha256(raw).hexdigest(),
        "origin_checkpoint_sha256": expected["sha256"],
        "no_refit_or_recalibration": True,
    }
    binding.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record
