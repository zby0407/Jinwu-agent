"""Trusted worker loaded inside the bubblewrap namespace.

User/model code must expose ``run_experiment(context)`` and return one
automatic-experiment-worker-result-v1 object. The trusted worker owns the final
JSON serialization so partial writes cannot be mistaken for a completed result.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import sys
import traceback
from pathlib import Path
from typing import Any

WORKER_VERSION = "automatic-experiment-worker-result-v1"
# The Windows/WSL backend mounts the locked site-packages at /runtime; the
# macOS seatbelt backend passes the host path via AE_LOCKED_SITE_PACKAGES.
LOCKED_SITE_PACKAGES = Path(
    os.environ.get("AE_LOCKED_SITE_PACKAGES", "/runtime/site-packages")
)


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("automatic_experiment_user_code", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load experiment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _finite(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    if not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")


def _basic_validate(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("run_experiment must return one object")
    expected = {
        "schema_version",
        "execution_completed",
        "measurements",
        "result_items",
        "artifacts",
        "warnings",
        "endpoint_results",
        "scientific_payload",
    }
    if set(payload) != expected:
        raise ValueError(
            f"worker result fields mismatch; missing={sorted(expected-set(payload))}, "
            f"unknown={sorted(set(payload)-expected)}"
        )
    if payload["schema_version"] != WORKER_VERSION:
        raise ValueError(f"schema_version must be {WORKER_VERSION}")
    if payload["execution_completed"] is not True:
        raise ValueError("successful worker return must set execution_completed=true")
    if not isinstance(payload["measurements"], list):
        raise ValueError("measurements must be an array")
    for index, row in enumerate(payload["measurements"]):
        if not isinstance(row, dict):
            raise ValueError(f"measurements[{index}] must be an object")
        _finite(row.get("value"), f"measurements[{index}].value")
    if not isinstance(payload["result_items"], list):
        raise ValueError("result_items must be an array")
    for index, row in enumerate(payload["result_items"]):
        if not isinstance(row, dict):
            raise ValueError(f"result_items[{index}] must be an object")
        kind = row.get("value_kind")
        value = row.get("value")
        if kind == "number":
            _finite(value, f"result_items[{index}].value")
        elif kind == "count":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"result_items[{index}].value must be a non-negative integer")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"result_items[{index}].value must be boolean")
        elif kind in {"category", "text"}:
            if not isinstance(value, str) or len(value) > 2000:
                raise ValueError(f"result_items[{index}].value must be bounded text")
        else:
            raise ValueError(f"result_items[{index}].value_kind is unsupported")
    for field in ("artifacts", "warnings", "endpoint_results"):
        if not isinstance(payload[field], list):
            raise ValueError(f"{field} must be an array")
    if not isinstance(payload["scientific_payload"], dict):
        raise ValueError("scientific_payload must be an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    experiment_path = Path(args.experiment)
    request_path = Path(args.request)
    result_path = Path(args.result)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not LOCKED_SITE_PACKAGES.is_dir():
        raise RuntimeError("locked site-packages mount is missing")
    sys.path.insert(0, str(LOCKED_SITE_PACKAGES))
    seed = int(request["seed"])
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    context = {
        "run_id": request["run_id"],
        "attempt_id": request["attempt_id"],
        "stage_id": request["stage_id"],
        "task": request["task"],
        "seed": seed,
        "input_dir": Path(request["input_root"]),
        "output_dir": Path(request["output_root"]),
        "input_manifest": request["input_manifest"],
        "prior_artifact_dir": Path(request["prior_artifact_root"]),
        "prior_artifacts": request["prior_artifacts"],
        "expected_artifacts": request["expected_artifacts"],
    }
    input_files: dict[str, list[Path]] = {}
    input_root = Path(request["input_root"])
    for row in request["input_manifest"].get("inputs", []):
        paths: list[Path] = []
        for item in row.get("files", []):
            relative = str(item["path"]).replace("\\", "/")
            if relative.startswith("inputs/"):
                relative = relative.removeprefix("inputs/")
            parts = Path(relative).parts
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise ValueError("input manifest contains an unsafe sandbox-relative path")
            paths.append(input_root.joinpath(*parts))
        input_files[str(row["id"])] = paths
    context["input_files"] = input_files
    context["input_path_by_id"] = {
        input_id: paths[0] if len(paths) == 1 else None
        for input_id, paths in input_files.items()
    }
    context["artifact_path_by_id"] = {
        str(row["id"]): Path(request["prior_artifact_root"]).joinpath(
            *Path(str(row["path"]).replace("\\", "/")).parts
        )
        for row in request["prior_artifacts"]
    }
    module = _load_module(experiment_path)
    entrypoint = getattr(module, "run_experiment", None)
    if not callable(entrypoint):
        raise RuntimeError("experiment.py must define callable run_experiment(context)")
    result = _basic_validate(entrypoint(context))
    temporary = result_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, result_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(70)
