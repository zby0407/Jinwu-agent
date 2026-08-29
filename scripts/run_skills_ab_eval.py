#!/usr/bin/env python3
"""Write a wiring-only A/B report for the current JW Skills treatment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jw.solar_forecast.skills_ab_eval import build_ab_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path("jw/subagents/skill_registry.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--gate",
        action="append",
        default=[],
        metavar="AGENT=PATH",
        help="gate receipt to include, e.g. solar-data=.../forecast_evidence_gate.json",
    )
    args = parser.parse_args()
    gate_paths = {}
    for item in args.gate:
        agent, separator, raw_path = item.partition("=")
        if not separator or not agent or not raw_path:
            parser.error(f"invalid --gate {item!r}; expected AGENT=PATH")
        gate_paths[agent] = Path(raw_path)
    report = build_ab_report(args.registry, gate_paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
