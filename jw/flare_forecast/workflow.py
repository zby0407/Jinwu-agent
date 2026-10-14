"""Explicit live acquisition and review-gated single issuance; no implicit fit."""

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .assets import child, sha
from .engine import PACKAGE, inventory, load
from .model import FEATURES, load_forest


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def verify_selected_asset(root: Path, relative: str) -> Path:
    path = child(root, relative)
    row = next(r for r in inventory()["files"] if r["path"] == relative)
    if path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
        raise ValueError("Registered source definition differs: " + relative)
    return path


def make_live_contract(root: Path, output: Path) -> Path:
    """Relocate only filesystem bindings; this creates no review acceptance."""
    contract = read(verify_selected_asset(root, "contract/original_contract.json"))
    schema = verify_selected_asset(root, "source/schema.json")
    policy = verify_selected_asset(root, "source/quality_policy.json")
    contract["quality_decision_file"]["path"] = str(policy)
    live = contract["live_source"]
    live["schema_literal_baseline_path"] = str(schema)
    live["source_policy_proofs"]["quality_source_decision"]["path"] = str(policy)
    contract["toolkit_migration"] = {
        "original_contract_sha256": inventory()["model_origin_contract_sha256"],
        "scope": "relocated live filesystem bindings; no new scientific review",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(contract, ensure_ascii=False, indent=2) + "\n"
    with output.open("x", encoding="utf-8") as stream:
        stream.write(payload)
    return output


def fetch_live(assets: Path, output: Path) -> dict:
    assets, output = assets.resolve(), output.resolve()
    if output.is_relative_to(assets) or assets.is_relative_to(output):
        raise ValueError("New acquisition output and read-only assets must be separate")
    if output.exists():
        raise ValueError("Acquisition output already exists")
    contract = make_live_contract(assets, output.with_suffix(".contract.json"))
    result = load("available24_live").fetch(contract, output)
    return {
        "status": result["status"],
        "live_dir": str(output),
        "contract": str(contract),
        "manifest_sha256": sha(output / "source_manifest.json"),
        "forecast_issued": False,
        "scientific_acceptance": "not_reviewed",
    }


def score_csv(assets: Path, source: Path) -> dict:
    """Unissued scores; arbitrary rows are never promoted to eligible live data."""
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or len(rows) > 10000:
        raise ValueError("Require 1-10000 input rows")
    values = [[float(row[name]) for name in FEATURES] for row in rows]
    return {
        "status": "unissued_scores",
        "rows": len(rows),
        "probabilities": load_forest(assets).predict(values),
        "future_window": None,
        "scientific_review": "not_performed",
        "probabilities_are_accuracy": False,
    }


def issue(
    assets: Path, live_dir: Path, contract: Path, data_review: Path, output: Path
) -> dict:
    """Require a real native Data review bound to this exact acquired snapshot."""
    output, assets, live_dir = output.resolve(), assets.resolve(), live_dir.resolve()
    if output.exists():
        raise ValueError("Never overwrite an issue output")
    if (
        output.is_relative_to(assets)
        or assets.is_relative_to(output)
        or output.is_relative_to(live_dir)
    ):
        raise ValueError("Issue output must be separate from read-only inputs")
    core = load("available24_model")
    policy, manifest, before, frame, manifest_path = core.checked_live(
        live_dir, contract
    )
    review, review_hashes = core.native_review(
        data_review, sha(manifest_path), sha(contract)
    )
    before.update(review_hashes)
    core.validate_physical(frame, policy, live=True)
    core.validate_live_at_issue(frame, datetime.now(timezone.utc))
    forest = load_forest(assets)
    model_protocol = read(PACKAGE / "model_protocol.json")
    if model_protocol["origin_checkpoint_sha256"] != forest.document[
        "origin_checkpoint_sha256"
    ] or model_protocol["historical_Evidence"]["decision"] not in {
        "accept",
        "accept_with_limits",
    }:
        raise ValueError("Frozen model lacks its original bounded historical review")
    if (
        forest.document["origin_contract_sha256"]
        != inventory()["model_origin_contract_sha256"]
    ):
        raise ValueError("Model identity differs from the registered source contract")
    if (
        policy.get("toolkit_migration", {}).get("original_contract_sha256")
        != forest.document["origin_contract_sha256"]
    ):
        raise ValueError("Live contract is not bound to this frozen model protocol")
    claim_path = live_dir.parent / (live_dir.name + ".issue_claim.json")
    claim = {
        "status": "attempt_reserved",
        "model_origin": forest.document["origin_checkpoint_sha256"],
        "live_manifest_sha256": sha(manifest_path),
        "no_retry": True,
    }
    with claim_path.open("x", encoding="utf-8") as stream:
        json.dump(claim, stream, indent=2)
    output.mkdir(parents=True, exist_ok=False)
    try:
        before[str(child(assets, "model/forest.json.gz"))] = sha(
            child(assets, "model/forest.json.gz")
        )
        probabilities = forest.predict(frame[FEATURES].to_numpy(float))[0]
        stamp, age = core.validate_live_at_issue(frame, datetime.now(timezone.utc))
        core.verify_unchanged(before)
        end = stamp + timedelta(hours=24)
        value = {
            "schema_version": "jw-flare-issue-v1",
            "status": "issued_experimental_pending_observation",
            "issued_utc": stamp.isoformat().replace("+00:00", "Z"),
            "prediction_window": {
                "start": stamp.isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
            },
            "target_duration_seconds": 86400,
            "target_spatial_scope": "whole_sun_GOES_new_start_no_AR_filter",
            "input_observation_utc": str(frame.iloc[0].observation_utc),
            "input_age_seconds": age,
            "probabilities": probabilities,
            "decisions_at_0_5": {k: v >= 0.5 for k, v in probabilities.items()},
            "observed_outcome": None,
            "future_accuracy": None,
            "future_truth_read": False,
            "no_fit_or_recalibration": True,
            "native_Data_decision": review["decision"],
            "prior_historical_Evidence": model_protocol["historical_Evidence"],
            "native_Data_review_sha256": sha(data_review),
            "live_manifest_sha256": sha(manifest_path),
            "model_origin_checkpoint_sha256": forest.document[
                "origin_checkpoint_sha256"
            ],
            "scientific_status": "not_reviewed",
            "formal_ResearchReviewStore_chain": False,
            "operational_forecast_validated": False,
            "execution_origin_requires_external_receipt": True,
            "limitations": inventory()["limitations"],
        }
        (output / "issued_forecast.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return value
    except BaseException:
        (output / "failure.json").write_text(
            json.dumps(
                {"status": "failed", "issue_claim_retained": True, "no_retry": True}
            ),
            encoding="utf-8",
        )
        raise
