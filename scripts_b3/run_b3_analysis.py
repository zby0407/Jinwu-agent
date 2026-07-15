from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from b3cycle.analysis import run_b3_analysis


def main() -> None:
    analysis = run_b3_analysis()
    print("Wrote b3/outputs/b3_analysis_report.json")
    print(f"Cycles detected: {len(analysis['cycle_features'])}")
    print(
        "Cycle-26 proxy class: "
        + analysis["cycle26_proxy_forecast"]["strength_class"]
    )
    print(
        "Waldmeier Spearman peak-vs-rise-time: "
        + str(analysis["waldmeier"]["spearman_peak_vs_rise_time"])
    )
    print("Top hypothesis: " + analysis["hypothesis_cards"][0]["id"])
    print(json.dumps(analysis["hypothesis_cards"][0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
