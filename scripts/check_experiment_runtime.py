"""Inspect the isolated computation runtime without starting scientific work."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from automatic_experiment.executor import doctor  # noqa: E402
from automatic_experiment.state import task_workspace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Actually test namespace/file/network isolation with a synthetic fixture",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Dedicated technical-probe workspace; required for --probe",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write a new JSON receipt without overwriting an existing one",
    )
    args = parser.parse_args()
    if args.probe and args.workspace is None:
        parser.error("--probe requires a dedicated --workspace")
    if args.workspace is not None:
        with task_workspace(args.workspace.resolve()):
            result = doctor(probe=args.probe)
    else:
        result = doctor(probe=False)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        with args.output.open("x", encoding="utf-8") as output:
            output.write(body)
    print(body, end="")
    return 0 if result["status"] in {"ready", "inspection_only"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
