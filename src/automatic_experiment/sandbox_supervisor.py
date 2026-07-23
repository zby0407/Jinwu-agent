#!/usr/bin/env python3
"""Trusted macOS supervisor: seatbelt + rlimits around the sandbox worker.

This is the macOS counterpart of ``sandbox_runner.sh`` (the WSL2/bubblewrap
backend). It keeps the same contract:

- same arguments (``--attempt-root``/``--runtime-file`` plus resource limits)
- the worker runs ``sandbox-exec``-isolated with the attempt output and a
  per-attempt scratch directory as the only writable locations
- ``sandbox.pid`` holds the worker process-group id for targeted cancellation
- ``resource.txt`` uses the GNU ``time -v`` keys the executor already parses
  (macOS ``ru_maxrss`` is bytes and is converted to kbytes)
- ``sandbox_start.json``/``sandbox_exit.json`` record startup and exit codes;
  124 means wall timeout, 137/152 mean resource kills, exactly like the WSL
  ``timeout``/``prlimit`` combination

Standard library only; this file never imports model code.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _seatbelt_profile(
    *,
    python: str,
    code_root: Path,
    input_root: Path,
    prior_root: Path,
    output_root: Path,
    scratch_root: Path,
    site_packages: str,
    runtime_file: Path,
) -> str:
    python_prefix = Path(python).resolve().parents[1]
    venv_prefix = Path(python).parents[1]
    read_roots = [
        Path("/usr"),
        Path("/System"),
        Path("/Library/Frameworks"),
        Path("/bin"),
        Path("/sbin"),
        Path("/dev"),
        Path("/private/etc"),
        python_prefix,
        code_root.resolve(),
        input_root.resolve(),
        prior_root.resolve(),
        output_root.resolve(),
        scratch_root.resolve(),
    ]
    # A venv interpreter symlinks to the base python; both the resolved base
    # prefix and the venv's own root (pyvenv.cfg, bin/, lib/) must be readable.
    if venv_prefix != python_prefix:
        read_roots.append(venv_prefix)
    if site_packages:
        read_roots.append(Path(site_packages).resolve())
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow mach-lookup)",
        "(allow sysctl-read)",
        "(allow file-read-metadata)",
        "(allow file-read*",
        # Path resolution on macOS reads the root directory itself; without
        # this literal every exec aborts inside dyld before user code starts.
        '  (literal "/")',
    ]
    lines.extend(f'  (subpath "{root.as_posix()}")' for root in read_roots)
    # The trusted worker script itself (mirrors the /runtime ro-bind on WSL).
    lines.append(f'  (literal "{runtime_file.resolve().as_posix()}")')
    lines.append('  (literal "/dev/null"))')
    lines.append("(allow file-write*")
    lines.append(f'  (subpath "{output_root.resolve().as_posix()}")')
    lines.append(f'  (subpath "{scratch_root.resolve().as_posix()}")')
    lines.append('  (literal "/dev/null"))')
    lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def _apply_limits(cpu_seconds: int, memory_bytes: int, file_bytes: int) -> None:
    # Wall time is enforced by the parent; CPU gets a hard kernel limit.
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    # NOTE: macOS does not implement RLIMIT_AS/RLIMIT_DATA (setrlimit raises
    # EINVAL), so the memory budget cannot be kernel-enforced on this backend
    # (bubblewrap's prlimit --as equivalent). This delta is recorded in the
    # sandbox_policy facts; output, stdout, wall, and CPU budgets are still
    # enforced by the supervisor/parent guards. RLIMIT_NPROC is per-UID on
    # macOS (not per-process-tree), so --nproc=32 has no equivalent either.


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--attempt-root", required=True)
    parser.add_argument("--runtime-file", required=True)
    parser.add_argument("--wall-seconds", type=int, required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--memory-bytes", type=int, required=True)
    parser.add_argument("--file-bytes", type=int, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--site-packages", default="")
    args = parser.parse_args()

    # The child runs with cwd=scratch; all paths must be absolute.
    # Keep the venv shim unresolved so Python keeps venv prefix semantics.
    args.python = os.path.abspath(args.python)
    if args.site_packages:
        args.site_packages = os.path.abspath(args.site_packages)
    args.attempt_root = os.path.abspath(args.attempt_root)
    args.runtime_file = os.path.abspath(args.runtime_file)

    attempt_root = Path(args.attempt_root)
    runtime_file = Path(args.runtime_file)
    code_root = attempt_root / "code"
    output_root = attempt_root / "output"
    for required in (code_root, output_root):
        if not required.is_dir():
            print("attempt code directory missing", file=sys.stderr)
            return 66
    if not (code_root / "experiment.py").is_file():
        print("experiment.py missing", file=sys.stderr)
        return 66
    if not (code_root / "worker_request.json").is_file():
        print("worker request missing", file=sys.stderr)
        return 66
    if not runtime_file.is_file():
        print("trusted worker missing", file=sys.stderr)
        return 66
    if not Path(SANDBOX_EXEC).is_file():
        print("sandbox-exec missing", file=sys.stderr)
        return 66
    if not Path(args.python).is_file():
        print("trusted python missing", file=sys.stderr)
        return 66
    input_root = attempt_root.parent.parent / "inputs"
    prior_root = attempt_root.parent.parent / "stage_artifacts"
    for required in (input_root, prior_root):
        if not required.is_dir():
            print("input snapshot directory missing", file=sys.stderr)
            return 66

    scratch = attempt_root / ".scratch"
    scratch.mkdir(exist_ok=True)
    (scratch / "matplotlib").mkdir(exist_ok=True)
    profile_path = scratch / "seatbelt.sb"
    profile_path.write_text(
        _seatbelt_profile(
            python=args.python,
            code_root=code_root,
            input_root=input_root,
            prior_root=prior_root,
            output_root=output_root,
            scratch_root=scratch,
            site_packages=args.site_packages,
            runtime_file=runtime_file,
        ),
        encoding="utf-8",
    )

    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "MPLCONFIGDIR": str(scratch / "matplotlib"),
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }
    if args.site_packages:
        env["AE_LOCKED_SITE_PACKAGES"] = args.site_packages

    command = [
        SANDBOX_EXEC,
        "-f",
        str(profile_path),
        args.python,
        "-I",
        "-B",
        str(runtime_file),
        "--experiment",
        str(code_root / "experiment.py"),
        "--request",
        str(code_root / "worker_request.json"),
        "--result",
        str(output_root / "result.json"),
    ]
    child = subprocess.Popen(
        command,
        cwd=str(scratch),
        env=env,
        stdin=subprocess.DEVNULL,
        preexec_fn=lambda: _apply_limits(
            args.cpu_seconds, args.memory_bytes, args.file_bytes
        ),
        start_new_session=True,
    )
    (attempt_root / "sandbox.pid").write_text(f"{child.pid}\n", encoding="ascii")

    start = time.monotonic()
    exit_code: int | None = None
    while True:
        return_code = child.poll()
        if return_code is not None:
            exit_code = return_code if return_code >= 0 else 128 + (-return_code)
            break
        if time.monotonic() - start > args.wall_seconds:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=5)
            exit_code = 124
            break
        time.sleep(0.05)

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    (attempt_root / "resource.txt").write_text(
        f"User time (seconds): {usage.ru_utime:.2f}\n"
        f"System time (seconds): {usage.ru_stime:.2f}\n"
        # macOS reports ru_maxrss in bytes; GNU time -v reports kilobytes.
        f"Maximum resident set size (kbytes): {usage.ru_maxrss // 1024}\n"
        f"Major (requiring I/O) page faults: {usage.ru_majflt}\n"
        f"Minor (reclaiming a frame) page faults: {usage.ru_minflt}\n",
        encoding="utf-8",
    )
    (attempt_root / "sandbox_start.json").write_text(
        '{"attempts":1,"retries":0}\n', encoding="utf-8"
    )
    (attempt_root / "sandbox_exit.json").write_text(
        json.dumps({"exit_code": exit_code}) + "\n", encoding="utf-8"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
