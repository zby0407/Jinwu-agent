#!/usr/bin/env python3
"""Isolated JSON-only worker for one allowlisted solar-cycle experiment."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from b3cycle.analysis import (  # noqa: E402
    REGISTERED_ANALYSIS_IDS,
    run_registered_analysis,
)


def _peak_ram_mb() -> float:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            process, ctypes.byref(counters), counters.cb
        )
        if not ok:
            raise OSError("GetProcessMemoryInfo failed")
        return float(counters.PeakWorkingSetSize) / (1024.0 * 1024.0)

    import resource

    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return peak / (1024.0 * 1024.0)
    return peak / 1024.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--experiment-id",
        choices=REGISTERED_ANALYSIS_IDS,
        default="E8_clean_reproduction",
    )
    parser.add_argument("--seed", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    cpu_started = time.process_time()
    try:
        args = build_parser().parse_args(argv)
        if args.seed < 0:
            raise ValueError("seed must be non-negative")
        analysis = run_registered_analysis(args.experiment_id, args.seed)
        payload = {
            "schema_version": "b3-analysis-worker-v1",
            "analysis": analysis,
            "usage": {
                "cpu_seconds": max(0.0, time.process_time() - cpu_started),
                "peak_ram_mb": max(0.0, _peak_ram_mb()),
            },
        }
        output = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception as exc:
        error = {
            "status": "error",
            "type": type(exc).__name__,
            "message": "isolated B3 analysis failed; diagnostic text omitted",
        }
        sys.stdout.buffer.write(
            (json.dumps(error, ensure_ascii=False, allow_nan=False) + "\n").encode(
                "utf-8"
            )
        )
        return 2
    sys.stdout.buffer.write((output + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
