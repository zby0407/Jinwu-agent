from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_state_imports_and_locks_when_fcntl_is_unavailable(tmp_path: Path):
    script = textwrap.dedent(
        """
        import importlib.abc
        import sys
        from pathlib import Path

        class BlockFcntl(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "fcntl":
                    raise ModuleNotFoundError("fcntl is unavailable")
                return None

        sys.meta_path.insert(0, BlockFcntl())

        from automatic_experiment.state import exclusive_file_lock

        lock_path = Path(sys.argv[1])
        with exclusive_file_lock(lock_path):
            with exclusive_file_lock(lock_path):
                assert lock_path.exists()
        """
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "state.lock")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
