"""Compare desktop builds using an isolated profile and synthetic credentials.

Run baseline and candidate sequentially with the same --output. No model
requests or installer/registry changes are made. The Electron harness exits
through the application's normal shutdown path after each sample.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--electron", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--entry", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--settings", action="store_true")
    parser.add_argument("--first-setup", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / "profile/Documents/Jinwu"
    workspace.mkdir(parents=True, exist_ok=True)
    # Stop dotenv discovery at the isolated workspace, not the source checkout.
    (workspace / ".env").touch(exist_ok=True)
    config = root / "local/Jinwu/config/jw/config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    if args.first_setup and config.exists():
        raise ValueError("First-setup smoke requires a new isolated output directory")
    if not config.exists() and not args.first_setup:
        config.write_text("provider: dashscope\nmodel: qwen3.7-plus\ndashscope_api_key: synthetic-startup-only\ndashscope_proxy_mode: direct\nui_backend: webui\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))}
    for key in ("ELECTRON_RUN_AS_NODE", "PYTHONPATH", "JW_SMOKE_ENTRY", "JW_SMOKE_LEGACY", "JW_DASHSCOPE_PROXY_MODE"):
        env.pop(key, None)
    env.update({
        "USERPROFILE": str(root / "profile"), "LOCALAPPDATA": str(root / "local"),
        "APPDATA": str(root / "roaming"), "JW_SMOKE_OUTPUT": str(root),
        "JW_SMOKE_SETTINGS": "1" if args.settings else "0",
        "JW_SMOKE_FIRST_SETUP": "1" if args.first_setup else "0",
        "JW_DESKTOP_RUNTIME_MANIFEST": str(args.runtime.resolve() / "runtime-manifest.json"),
        "PYTHON_DOTENV_DISABLED": "1", "DASHSCOPE_API_KEY": "synthetic-startup-only",
        "DASHSCOPE_BASE_URL": "http://127.0.0.1:9/v1", "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false", "PYTHONUTF8": "1",
    })
    if args.first_setup:
        env.pop("DASHSCOPE_API_KEY", None)
    if args.entry:
        env["JW_SMOKE_ENTRY"] = str(args.entry.resolve())
    if args.legacy:
        env["JW_SMOKE_LEGACY"] = "1"
    repo = Path(__file__).resolve().parent.parent
    samples = []
    for index in range(args.samples):
        report_path = root / "desktop-smoke.json"
        report_path.unlink(missing_ok=True)
        env["JW_SMOKE_STARTED"] = str(round(time.time() * 1000))
        with (root / f"{args.label}-{index}.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen([str(args.electron.resolve()), str(repo / "desktop/test/live-startup.smoke.mjs")],
                                     cwd=repo / "desktop", env=env, stdout=log, stderr=log)
            try:
                child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True)
                raise
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["index"] = index
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        report["portsClosed"] = True
        for url in (report.get("backendUrl"), report.get("webuiUrl")):
            if not url:
                continue
            try:
                opener.open(url, timeout=1).close()
                report["portsClosed"] = False
            except OSError:
                pass
        samples.append(report)
        (root / f"{args.label}-timings.json").write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if not report.get("ok") or not report["portsClosed"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
