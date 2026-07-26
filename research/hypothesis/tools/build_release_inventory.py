#!/usr/bin/env python3
"""重新生成 RELEASE_INVENTORY.json：产品文件全量 sha256 清单。

任何产品文件改动后运行一次：
    PYTHONUTF8=1 python -B tools/build_release_inventory.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDED_NAMES = {"RELEASE_INVENTORY.json"}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def include(relative: str) -> bool:
    if relative in EXCLUDED_NAMES:
        return False
    if relative.startswith("runs/") and relative != "runs/README.md":
        return False
    if relative.startswith("evals/results/") and relative not in {
        "evals/results/deterministic_tests.json",
        "evals/results/live_status.json",
    }:
        return False
    return True


def main() -> int:
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if not include(relative):
            continue
        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    inventory = {
        "schema_version": "scientific-hypothesis-release-inventory-v1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "product": "科学假设 Agent 1.0",
        "inventory_excludes": [
            "RELEASE_INVENTORY.json",
            "runs/* except runs/README.md",
            "raw eval logs under evals/results/* except curated deterministic_tests.json and live_status.json",
        ],
        "file_count": len(files),
        "total_bytes": sum(row["size_bytes"] for row in files),
        "files": files,
    }
    target = ROOT / "RELEASE_INVENTORY.json"
    target.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"RELEASE_INVENTORY.json regenerated: {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
