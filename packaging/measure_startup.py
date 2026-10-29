"""Measure local backend startup with isolated synthetic credentials, no model calls.

This measures backend API/main-agent readiness, not Electron window timing.
Each sample terminates only the process tree it created. Keep the report separate
from user-workspace acceptance measurements.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument(
        "--provider", choices=["dashscope", "moonshot"], default="dashscope"
    )
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace"
    workspace.mkdir(exist_ok=True)
    config = root / "config" / "jw"
    config.mkdir(parents=True, exist_ok=True)
    model = "qwen3.7-plus" if args.provider == "dashscope" else "kimi-k2.6"
    (config / "config.yaml").write_text(
        f"provider: {args.provider}\nmodel: {model}\n{args.provider}_api_key: synthetic-startup-only\n",
        encoding="utf-8",
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "XDG_CONFIG_HOME": str(root / "config"),
            "JW_DATA_DIR": str(root / "data"),
            "JW_WORKSPACE_DIR": str(workspace),
            "JW_RUNTIME_ROOT": str(args.runtime.resolve()),
            "JW_PROJECT_ROOT": str((args.source or args.runtime / "project").resolve()),
            "JW_LANGGRAPH_COMMAND_JSON": json.dumps(
                [str(args.python.resolve()), "-m", "langgraph_cli"]
            ),
            "JW_DESKTOP_MANAGED": "1",
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONUTF8": "1",
            "DASHSCOPE_BASE_URL": "http://127.0.0.1:9/v1",
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if args.source:
        env["PYTHONPATH"] = str(args.source.resolve())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    samples = []
    for index in range(args.samples):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        command = (
            [str(args.python.resolve()), "-m", "jw", "deploy"]
            if args.legacy
            else [str(args.python.resolve()), "-m", "jw.desktop_server"]
        )
        command += ["--workdir", str(workspace), "--port", str(port)]
        started = time.perf_counter()
        sample = {"index": index, "apiMs": None, "agentMs": None}
        with (root / f"sample-{index}.log").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                command,
                cwd=workspace,
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                while time.perf_counter() - started < 120:
                    if proc.poll() is not None:
                        sample["error"] = f"exit:{proc.returncode}"
                        break
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/ok", timeout=0.5):
                            if sample["apiMs"] is None:
                                sample["apiMs"] = round(
                                    (time.perf_counter() - started) * 1000
                                )
                        if args.legacy:
                            sample["agentMs"] = sample["apiMs"]
                            break
                        with opener.open(
                            f"http://127.0.0.1:{port}/api/runtime-status", timeout=0.5
                        ) as response:
                            status = json.load(response)
                        if status.get("canSend"):
                            sample["agentMs"] = round(
                                (time.perf_counter() - started) * 1000
                            )
                            break
                        if status.get("agent", {}).get("state") == "failed":
                            sample["error"] = status["agent"]
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.1)
            finally:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    proc.terminate()
                proc.wait(timeout=20)
        samples.append(sample)
        print(json.dumps(sample), flush=True)
        (root / "timings.json").write_text(
            json.dumps(samples, indent=2), encoding="utf-8"
        )
        if sample.get("error") or sample["agentMs"] is None:
            return 1
    warm = [sample["agentMs"] for sample in samples[1:]] or [samples[0]["agentMs"]]
    print(f"subsequent_agent_median_ms={statistics.median(warm)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
