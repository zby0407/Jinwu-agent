"""Portable, non-pickle inference for the frozen calibrated 200-tree RF."""

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

METRICS = (
    "USFLUX",
    "TOTUSJH",
    "TOTUSJZ",
    "ABSNJZH",
    "SAVNCPP",
    "MEANALP",
    "R_VALUE",
    "TOTPOT",
    "MEANSHR",
    "AREA_ACR",
    "MEANPOT",
)
FEATURES = [f"{x}_{a}" for x in METRICS for a in ("max", "median")] + ["patch_count"]


@dataclass
class FrozenForest:
    document: dict

    def __post_init__(self):
        d = self.document
        if (
            d.get("schema_version") != "jw-frozen-flare-forest-v1"
            or d.get("features") != FEATURES
            or d.get("classes") != [0, 1, 2]
            or len(d.get("trees", [])) != 200
            or not 0.25 <= d.get("temperature", 0) <= 4
        ):
            raise ValueError("Frozen forest interface differs")
        self.trees = []
        for tree in d["trees"]:
            left = np.asarray(tree["left"], dtype=int)
            right = np.asarray(tree["right"], dtype=int)
            feature = np.asarray(tree["feature"], dtype=int)
            threshold = np.asarray(tree["threshold"], dtype=float)
            values = np.asarray(tree["value"], dtype=float)
            size = len(left)
            if (
                size == 0
                or right.shape != (size,)
                or feature.shape != (size,)
                or threshold.shape != (size,)
                or values.shape != (size, 3)
                or not np.isfinite(values).all()
                or (values < 0).any()
                or not np.isfinite(threshold).all()
            ):
                raise ValueError("Invalid frozen tree arrays")
            for node in range(size):
                if left[node] == -1:
                    if right[node] != -1 or values[node].sum() <= 0:
                        raise ValueError("Invalid leaf")
                elif not (
                    node < left[node] < size
                    and node < right[node] < size
                    and 0 <= feature[node] < len(FEATURES)
                ):
                    raise ValueError("Invalid or cyclic tree children")
            self.trees.append((left, right, feature, threshold, values))

    def probabilities(self, features) -> np.ndarray:
        raw = np.asarray(features, dtype=float)
        if raw.ndim != 2 or raw.shape[1] != len(FEATURES) or not np.isfinite(raw).all():
            raise ValueError("Exactly 23 finite physical features required")
        x = raw.astype(np.float32)
        if not np.isfinite(x).all():
            raise ValueError(
                "Feature cannot be represented by the original float32 input"
            )
        result = np.zeros((len(x), 3))
        for left, right, feature, threshold, values in self.trees:
            nodes = np.zeros(len(x), dtype=int)
            while (active := np.flatnonzero(left[nodes] != -1)).size:
                at = nodes[active]
                nodes[active] = np.where(
                    x[active, feature[at]] <= threshold[at], left[at], right[at]
                )
            leaves = values[nodes].copy()
            result += leaves / leaves.sum(axis=1, keepdims=True)
        result /= len(self.trees)
        t = self.document["temperature"]
        if t != 1:
            logits = np.log(np.maximum(result, 1e-12)) / t
            weights = np.exp(logits - logits.max(axis=1, keepdims=True))
            result = weights / weights.sum(axis=1, keepdims=True)
        return result

    def predict(self, features) -> list[dict]:
        return [
            {"C1.0+": float(row[1] + row[2]), "M1.0+": float(row[2])}
            for row in self.probabilities(features)
        ]


def load_forest(root: Path) -> FrozenForest:
    from .assets import child, sha
    from .engine import PACKAGE

    path = child(root, "model/forest.json.gz")
    binding = json.loads(
        child(root, "model/forest_binding.json").read_text(encoding="utf-8")
    )
    identity = json.loads(
        (PACKAGE / "portable_model_identity.json").read_text(encoding="utf-8")
    )
    if binding != identity:
        raise ValueError(
            "Portable checkpoint identity differs from the published binding"
        )
    if path.stat().st_size > 16 * 1024 * 1024 or sha(path) != binding["sha256"]:
        raise ValueError("Portable forest hash/size differs")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("Portable forest payload too large")
    if hashlib.sha256(raw).hexdigest() != binding["json_sha256"]:
        raise ValueError("Portable forest JSON differs")
    return FrozenForest(json.loads(raw))
