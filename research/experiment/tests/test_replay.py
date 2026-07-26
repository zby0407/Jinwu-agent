from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from automatic_experiment import service
from automatic_experiment.state import file_sha256, load_state, read_json, runs_root
from tests.helpers import (
    assessment,
    cleanup_run,
    create_ready_run,
    request,
)


def complete_run(run_id: str, attempt_id: str) -> dict[str, object]:
    service.execute(run_id, attempt_id)
    preview = service.verify(run_id, attempt_id, None)
    if preview["status"] != "assessment_required":
        raise AssertionError(preview)
    service.verify(run_id, attempt_id, assessment())
    return service.finalize(run_id)


class ReplayTests(unittest.TestCase):
    def test_repeated_request_starts_a_fresh_run_without_history_choice(self) -> None:
        req = request("unit_repeated_request")
        source_run_id, source_attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, source_run_id)
        complete_run(source_run_id, source_attempt_id)

        repeated = service.bind_request({"request": req})
        repeated_run_id = repeated["run_id"]
        self.addCleanup(cleanup_run, repeated_run_id)
        _, repeated_state = load_state(repeated_run_id)

        self.assertNotEqual(repeated_run_id, source_run_id)
        self.assertEqual(repeated["lineage"]["mode"], "fresh")
        self.assertEqual(repeated_state["lineage"]["source_run_id"], None)
        self.assertEqual(repeated_state["lineage"]["matching_run_ids"], [])

    def test_exact_replay_reexecutes_and_reproduces_deterministic_evidence(self) -> None:
        req = request("unit_replay_exact")
        source_run_id, source_attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, source_run_id)
        complete_run(source_run_id, source_attempt_id)
        prepared = service.prepare_replay(source_run_id)
        replay_run_id = prepared["run_id"]
        replay_attempt_id = prepared["attempt_id"]
        self.addCleanup(cleanup_run, replay_run_id)

        self.assertNotEqual(replay_run_id, source_run_id)
        _, replay_state = load_state(replay_run_id)
        self.assertEqual(replay_state["phase"], "attempt_prepared")
        self.assertEqual(replay_state["lineage"]["mode"], "exact_replay")
        source_root = runs_root() / source_run_id
        replay_root = runs_root() / replay_run_id
        self.assertEqual(
            read_json(source_root / "design.json"),
            read_json(replay_root / "design.json"),
        )
        self.assertEqual(
            prepared["lineage"]["source_code_sha256"],
            {
                "stage_summary": {
                    row["path"].removeprefix("code/"): row["sha256"]
                    for row in read_json(
                        replay_root / "attempts" / replay_attempt_id / "attempt.json"
                    )["files"]
                    if row["path"].startswith("code/")
                    and row["path"] != "code/worker_request.json"
                }
            },
        )

        entry = complete_run(replay_run_id, replay_attempt_id)
        source_record = read_json(source_root / "record.json")
        replay_record = read_json(replay_root / "record.json")
        self.assertTrue(replay_record["replay"]["exact_replay_verified"])
        self.assertTrue(all(replay_record["replay"]["identity_checks"].values()))
        self.assertEqual(
            source_record["worker_result"]["measurements"],
            replay_record["worker_result"]["measurements"],
        )
        source_artifacts = {
            Path(row["path"]).name: row["sha256"]
            for row in source_record["public_artifacts"]
            if Path(row["path"]).name != "worker_result.json"
        }
        replay_artifacts = {
            Path(row["path"]).name: row["sha256"]
            for row in replay_record["public_artifacts"]
            if Path(row["path"]).name != "worker_result.json"
        }
        self.assertEqual(source_artifacts, replay_artifacts)
        self.assertEqual(entry["audit_sha256"], file_sha256(replay_root / "audit.md"))
        self.assertIn("exact_replay", (replay_root / "audit.md").read_text(encoding="utf-8"))

    def test_replay_rejects_source_code_tampering(self) -> None:
        req = request("unit_replay_tamper")
        source_run_id, source_attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, source_run_id)
        complete_run(source_run_id, source_attempt_id)
        source_root = runs_root() / source_run_id
        code_path = (
            source_root / "attempts" / source_attempt_id / "code" / "experiment.py"
        )
        code_path.write_text(
            code_path.read_text(encoding="utf-8") + "\n# tampered\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "changed after preparation"):
            service.prepare_replay(source_run_id)

    def test_replay_rejects_runtime_environment_drift(self) -> None:
        req = request("unit_replay_environment")
        source_run_id, source_attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, source_run_id)
        complete_run(source_run_id, source_attempt_id)
        with patch(
            "automatic_experiment.service.runtime_environment_snapshot",
            return_value={"ready": True, "drift": "simulated"},
        ):
            with self.assertRaisesRegex(RuntimeError, "environment differs"):
                service.prepare_replay(source_run_id)

    def test_finalize_rejects_report_audit_and_asset_tampering(self) -> None:
        req = request("unit_finalize_integrity")
        run_id, attempt_id = create_ready_run(req)
        self.addCleanup(cleanup_run, run_id)
        complete_run(run_id, attempt_id)
        root = runs_root() / run_id
        report = root / "report.md"
        original = report.read_bytes()
        report.write_bytes(original + "\n篡改".encode("utf-8"))
        with self.assertRaisesRegex(RuntimeError, "report is missing or changed"):
            service.finalize(run_id)
        report.write_bytes(original)
        audit = root / "audit.md"
        audit.write_bytes(audit.read_bytes() + "\n篡改".encode("utf-8"))
        with self.assertRaisesRegex(RuntimeError, "audit attachment"):
            service.finalize(run_id)


if __name__ == "__main__":
    unittest.main()
