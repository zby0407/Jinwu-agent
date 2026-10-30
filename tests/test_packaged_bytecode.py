"""Private runtime imports should use build-produced, relocatable bytecode."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def test_private_runtime_import_does_not_reopen_source(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "packaging" / "build_runtime.py"
    spec = importlib.util.spec_from_file_location("jw_runtime_builder_test", source)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    runtime = tmp_path / "runtime"
    library = runtime / "python" / "Lib"
    library.mkdir(parents=True)
    (library / "packaged_probe.py").write_text("answer = 42\n", encoding="utf-8")
    monkeypatch.setattr(builder, "_interpreter_in", lambda *_: Path(sys.executable))
    builder.precompile_runtime(runtime, platform="win32", env=os.environ.copy())
    relocated = tmp_path / "relocated"
    runtime.rename(relocated)
    library = relocated / "python" / "Lib"
    # A relocated runtime can import without reading module source for cache
    # validation. Test an actual import, not a particular compileall flag.
    script = """
import sys
sys.path.insert(0, sys.argv[1])
def audit(event, args):
    if event == 'open' and str(args[0]).endswith('packaged_probe.py'):
        raise AssertionError('import reopened packaged source')
sys.addaudithook(audit)
import packaged_probe
assert packaged_probe.answer == 42
"""
    subprocess.run([sys.executable, "-I", "-c", script, str(library)], check=True)
