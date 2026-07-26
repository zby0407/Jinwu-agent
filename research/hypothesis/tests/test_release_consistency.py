"""发布一致性测试：skills/schemas 数量、文档一致、无密钥/字节码、inventory 对齐。"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SKILLS = 8
EXPECTED_SCHEMAS = 6
EXPECTED_TOOLS = [
    "scientific_hypothesis_bind_request",
    "scientific_hypothesis_inspect_upstream",
    "scientific_hypothesis_bind_evidence",
    "scientific_hypothesis_validate_response",
    "scientific_hypothesis_rank",
    "scientific_hypothesis_freeze",
]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@unittest.skipUnless(
    (ROOT / ".pi").is_dir(),
    "standalone release bundle is not part of the integrated repository",
)
class ReleaseConsistencyTests(unittest.TestCase):
    def test_skills_and_schemas_count(self) -> None:
        skills = sorted((ROOT / ".pi" / "skills").glob("*/SKILL.md"))
        schemas = sorted((ROOT / "specs").glob("*.schema.json"))
        self.assertEqual(len(skills), EXPECTED_SKILLS, [str(s) for s in skills])
        self.assertEqual(len(schemas), EXPECTED_SCHEMAS, [str(s) for s in schemas])

    def test_extension_registers_six_tools(self) -> None:
        extension = (ROOT / ".pi" / "extensions" / "scientific-hypothesis" / "index.ts").read_text(
            encoding="utf-8"
        )
        for tool in EXPECTED_TOOLS:
            self.assertIn(f'"{tool}"', extension)
        declared = re.search(r"const HYPOTHESIS_TOOLS = \[(.*?)\] as const", extension, re.DOTALL)
        self.assertIsNotNone(declared)
        listed = re.findall(r'"(scientific_hypothesis_[a-z_]+)"', declared.group(1))
        self.assertEqual(sorted(listed), sorted(EXPECTED_TOOLS))

    def test_documents_agree_on_counts_and_paths(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        manual = (ROOT / "科学假设Agent测试与使用手册.md").read_text(encoding="utf-8")
        tech = (ROOT / "科学假设Agent技术说明.md").read_text(encoding="utf-8")
        for doc, name in ((readme, "README"), (manual, "手册"), (tech, "技术说明")):
            self.assertIn("六个 Pi Tools", doc, name)
            self.assertIn("八个可组合 Skills", doc, name)
        # README 示例必须指向真实存在的输入文件。
        for match in re.findall(r"inputs/[A-Za-z0-9_./-]+", readme):
            self.assertTrue((ROOT / match).exists(), f"README 引用了不存在的路径：{match}")

    def test_no_bytecode_or_secret_files_in_product(self) -> None:
        forbidden = []
        for path in ROOT.rglob("*"):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                forbidden.append(path)
            if path.is_file() and re.search(
                r"(?:^|[_.-])(?:env|secret|credential|api[_-]?key)(?:$|[_.-])",
                path.name,
                re.IGNORECASE,
            ):
                forbidden.append(path)
        self.assertEqual(forbidden, [])

    def test_no_api_key_values_in_product_files(self) -> None:
        pattern = re.compile(r"sk-[A-Za-z0-9._-]{16,}")
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix.lower() not in {".py", ".ts", ".md", ".json", ".sh", ".mjs", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                offenders.append(path)
        self.assertEqual(offenders, [])

    def test_release_inventory_matches_product_files(self) -> None:
        inventory = json.loads((ROOT / "RELEASE_INVENTORY.json").read_text(encoding="utf-8"))
        listed = {row["path"]: row for row in inventory["files"]}
        actual: dict[str, Path] = {}
        for path in ROOT.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative == "RELEASE_INVENTORY.json":
                continue
            if relative.startswith("runs/") and relative != "runs/README.md":
                continue
            if relative.startswith("evals/results/") and relative not in {
                "evals/results/deterministic_tests.json",
                "evals/results/live_status.json",
            }:
                continue
            actual[relative] = path
        self.assertEqual(
            set(listed),
            set(actual),
            "inventory 与磁盘不一致；改动产品文件后请运行 "
            "PYTHONUTF8=1 python -B tools/build_release_inventory.py",
        )
        self.assertEqual(inventory["file_count"], len(actual))
        self.assertEqual(
            inventory["total_bytes"], sum(path.stat().st_size for path in actual.values())
        )
        for relative, path in actual.items():
            self.assertEqual(listed[relative]["size_bytes"], path.stat().st_size, relative)
            self.assertEqual(listed[relative]["sha256"], file_sha256(path), relative)


if __name__ == "__main__":
    unittest.main()
