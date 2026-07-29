"""测试隔离：知识库 db 与 markdown 导出指向临时目录。

P2 起，experiment service 在 validate_design / finalize 时会接触知识库
（方案 §5.4 #3/#4）。不设隔离时这些调用会写真实 ~/.jw/knowledge.db
与真实 knowledge_base/ 导出树。setdefault 不覆盖调用方显式设置的环境变量。
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("JW_DATA_DIR", tempfile.mkdtemp(prefix="kb_test_data_"))
os.environ.setdefault("JW_KB_EXPORT_DIR", tempfile.mkdtemp(prefix="kb_test_export_"))

# Standalone contract roots are mutable runtime state and are intentionally
# absent from a clean checkout. Tests that create TemporaryDirectory children
# need the parent to exist on every CI operating system.
from automatic_experiment.state import inputs_root, runs_root  # noqa: E402

runs_root().mkdir(parents=True, exist_ok=True)
inputs_root().mkdir(parents=True, exist_ok=True)

_LOCKED_RUNTIME_MODULES = {
    "test_execution.py",
    "test_multistage.py",
    "test_replay.py",
    "test_reporting.py",
    "test_sandbox.py",
}


def pytest_collection_modifyitems(config, items) -> None:
    """Skip real execution tests when the locked sandbox is unavailable.

    Contract, policy, path, bridge, and integration tests still run. A
    configured WSL2/bubblewrap or macOS seatbelt environment runs the complete
    suite; ordinary Windows/Linux CI records an explicit skip instead of
    turning an expected boundary block into a misleading product failure.
    """

    del config
    try:
        from automatic_experiment.executor import runtime_environment_snapshot

        snapshot = runtime_environment_snapshot()
        ready = snapshot.get("ready") is True
        reason = str(snapshot.get("diagnostic") or snapshot.get("package_mismatches"))
    except Exception as exc:  # noqa: BLE001
        ready = False
        reason = f"{type(exc).__name__}: {exc}"
    if ready:
        return
    marker = pytest.mark.skip(
        reason=f"locked automatic-experiment runtime unavailable: {reason[:300]}"
    )
    for item in items:
        if item.path.name in _LOCKED_RUNTIME_MODULES:
            item.add_marker(marker)
