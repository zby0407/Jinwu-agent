#!/usr/bin/env python3
"""Smoke-test an assembled JW desktop runtime.

This is the CI gate that runs **before** the Electron installer build.  It never
starts the full Agent, never talks to a model provider and never needs network
access or an API key.  It answers one question: *would the Electron shell be able
to find and execute everything the runtime manifest promises, from the final
path?*

Checks performed (see ``runtime_contract.check_runtime``):

* ``runtime-manifest.json`` exists, parses and matches the schema contract
* every declared path exists, uses '/' separators and stays inside the runtime
* ``python`` and ``node`` are executable files for the target platform
* the private Python imports ``jw`` and ``langgraph_cli`` and runs
  ``python -m jw --help`` / ``python -m langgraph_cli --help`` (no network)
* ``sys.prefix`` points inside ``runtime/python`` (the runtime is relocatable)
* declared versions match the versions actually installed
* the WebUI standalone server (``webui/dist/server.js``) is present and valid
* bundled project resources exist and contain no secrets, databases or caches
* license files are present

Usage::

    python packaging/smoke_test.py
    python packaging/smoke_test.py --runtime packaging/dist/runtime --json
    python packaging/smoke_test.py --allow-cross-platform   # static checks only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_contract import (  # noqa: E402 - sibling module
    check_runtime,
    dir_size,
    host_arch,
    host_platform,
)

DEFAULT_RUNTIME = SCRIPT_DIR / "dist" / "runtime"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke-test the assembled JW desktop runtime (offline, no API key).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        default=DEFAULT_RUNTIME,
        help="Runtime root produced by packaging/build_runtime.py.",
    )
    parser.add_argument(
        "--allow-cross-platform",
        action="store_true",
        help="Skip executable probes when the runtime targets another platform/arch.",
    )
    parser.add_argument(
        "--skip-binary-probes",
        action="store_true",
        help="Only run static checks (no interpreter/Node execution).",
    )
    parser.add_argument(
        "--skip-project-scan",
        action="store_true",
        help="Do not scan bundled project resources for forbidden files.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable report instead of the human summary.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print problems (and the final summary line).",
    )
    return parser.parse_args(argv)


def print_human(report, *, quiet: bool) -> None:
    manifest = report.manifest or {}
    if not quiet:
        print(f"runtime: {report.root}")
        print(
            f"target:  {manifest.get('platform', '?')}/{manifest.get('arch', '?')} "
            f"(host {host_platform()}/{host_arch()})"
        )
        print(
            f"report:  schema_version={manifest.get('schema_version')} "
            f"python={manifest.get('pythonVersion')} "
            f"node={manifest.get('nodeVersion')} "
            f"jw={manifest.get('jwVersion')} "
            f"webui={manifest.get('webuiVersion')}"
        )
        print()
        for check in report.checks:
            status = check["status"]
            if status == "fail":
                # Failures are printed once, in the problems section below.
                continue
            marker = {"ok": "PASS", "warn": "WARN", "skip": "SKIP"}[status]
            detail = f" - {check['detail']}" if check["detail"] else ""
            print(f"[{marker}] {check['name']}{detail}")
        print()

    for warning in report.warnings:
        print(f"[WARN] {warning}")
    for problem in report.problems:
        print(f"[FAIL] {problem}")

    size_mib = dir_size(report.root) / 1048576 if report.root.is_dir() else 0.0
    verdict = "PASSED" if report.ok else "FAILED"
    print(
        f"smoke test {verdict}: {report.root} "
        f"({size_mib:.1f} MiB, {len(report.problems)} problem(s), "
        f"{len(report.warnings)} warning(s))"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runtime_root = args.runtime.expanduser().resolve()

    if not runtime_root.is_dir():
        message = (
            f"runtime directory not found: {runtime_root}\n"
            f"hint: run `python packaging/build_runtime.py --clean` first"
        )
        if args.json:
            print(json.dumps({"ok": False, "problems": [message]}, indent=2))
        else:
            print(f"[FAIL] {message}")
        return 1

    report = check_runtime(
        runtime_root,
        require_host_match=not args.allow_cross_platform,
        probe_binaries=not args.skip_binary_probes,
        scan_project=not args.skip_project_scan,
    )

    if args.json:
        payload = report.as_dict()
        if args.strict:
            payload["ok"] = payload["ok"] and not payload["warnings"]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    print_human(report, quiet=args.quiet)
    if args.strict and report.warnings:
        print(f"[FAIL] --strict: {len(report.warnings)} warning(s) treated as failures")
        return 1
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
