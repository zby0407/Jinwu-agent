from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from automatic_experiment.state import PROJECT_ROOT, file_sha256


@unittest.skipUnless(
    (PROJECT_ROOT / ".pi").is_dir(),
    "standalone release bundle is not part of the integrated repository",
)
class ReleaseConsistencyTests(unittest.TestCase):
    def test_seven_skills_and_seven_schemas(self) -> None:
        skills = sorted((PROJECT_ROOT / ".pi" / "skills").glob("*/SKILL.md"))
        schemas = sorted((PROJECT_ROOT / "specs").glob("*.schema.json"))
        self.assertEqual(len(skills), 7)
        self.assertEqual(len(schemas), 7)

    def test_primary_documented_example_uses_upstream_handoff(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        manual = (PROJECT_ROOT / "自动实验Agent测试与使用手册.md").read_text(
            encoding="utf-8"
        )
        live = json.loads(
            (PROJECT_ROOT / "evals" / "live_cases_v1.json").read_text(
                encoding="utf-8"
            )
        )
        marker = "inputs/upstream_handoff_demo/research_plan_feedback.md"
        self.assertIn(marker, readme)
        self.assertIn(marker, manual)
        self.assertIn(marker, live["cases"][0]["prompt"])
        self.assertIn("合成演示数据", live["cases"][0]["prompt"])
        self.assertIn("零到多", readme)
        self.assertIn("一份规划反馈、一份数据/特征反馈和一张处理后表格", manual)

    def test_runtime_has_no_legacy_route_leakage(self) -> None:
        excluded = PROJECT_ROOT / "evals" / "legacy_e0_e8"
        patterns = [
            re.compile(r"\bE[0-8]\b"),
            re.compile(r"ExperimentManifest|Runbook|Ledger"),
            re.compile(r"\bV2\b"),
        ]
        for path in PROJECT_ROOT.rglob("*"):
            if not path.is_file() or excluded in path.parents:
                continue
            if PROJECT_ROOT / "tests" in path.parents:
                continue
            if path.suffix.lower() not in {".py", ".ts", ".md", ".json", ".sh"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in patterns:
                self.assertIsNone(pattern.search(text), f"{pattern.pattern} leaked into {path}")

    def test_reference_source_hashes_are_unchanged(self) -> None:
        audit = json.loads((PROJECT_ROOT / "SOURCE_AUDIT.json").read_text(encoding="utf-8"))
        source_root = PROJECT_ROOT.parents[1]
        for relative, expected in audit["reference_files"].items():
            path = source_root / Path(*relative.split("/"))
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(file_sha256(path), expected, relative)

    def test_no_bytecode_or_secret_files_in_product(self) -> None:
        forbidden = []
        for path in PROJECT_ROOT.rglob("*"):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                forbidden.append(path)
            if path.is_file() and re.search(
                r"(?:^|[_.-])(?:env|secret|credential|api[_-]?key)(?:$|[_.-])",
                path.name,
                re.IGNORECASE,
            ):
                forbidden.append(path)
        self.assertEqual(forbidden, [])

    def test_release_inventory_matches_all_nonruntime_product_files(self) -> None:
        inventory = json.loads(
            (PROJECT_ROOT / "RELEASE_INVENTORY.json").read_text(encoding="utf-8")
        )
        listed = {row["path"]: row for row in inventory["files"]}
        actual: dict[str, Path] = {}
        for path in PROJECT_ROOT.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(PROJECT_ROOT).as_posix()
            if relative in {"RELEASE_INVENTORY.json", "SOURCE_AUDIT.json"}:
                continue
            if relative.startswith("runs/") and relative != "runs/README.md":
                continue
            if relative.startswith("evals/results/") and relative not in {
                "evals/results/deterministic_tests.json",
                "evals/results/live_status.json",
            }:
                continue
            actual[relative] = path
        self.assertEqual(set(listed), set(actual))
        self.assertEqual(inventory["file_count"], len(actual))
        self.assertEqual(
            inventory["total_bytes"],
            sum(path.stat().st_size for path in actual.values()),
        )
        for relative, path in actual.items():
            self.assertEqual(listed[relative]["size_bytes"], path.stat().st_size)
            self.assertEqual(listed[relative]["sha256"], file_sha256(path))


if __name__ == "__main__":
    unittest.main()
