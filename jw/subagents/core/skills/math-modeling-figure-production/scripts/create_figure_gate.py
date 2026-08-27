from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "assets" / "figure_gate_template.md"


def default_output(project_root: Path, date_text: str) -> Path:
    return project_root / "01_工作记录" / "reviews" / f"figure_gate_{date_text}.md"


def figure_rows(count: int) -> str:
    if count <= 0:
        return "|  |  |  |  |  |  |  |  | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 |"
    rows = []
    for idx in range(1, count + 1):
        rows.append(
            f"| 图{idx} |  |  |  |  |  |  |  | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 | 未检查 |"
        )
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a math-modeling figure gate record."
    )
    parser.add_argument(
        "--project-root", required=True, type=Path, help="Contest project root"
    )
    parser.add_argument("--project", default="", help="Project display name")
    parser.add_argument("--paper", default="", help="Current paper source or PDF path")
    parser.add_argument(
        "--date", default=datetime.now().strftime("%Y%m%d"), help="Date label"
    )
    parser.add_argument(
        "--figure-count", type=int, default=0, help="Pre-create rows for N figures"
    )
    parser.add_argument("--output", type=Path, help="Output markdown path")
    args = parser.parse_args()

    template = TEMPLATE.read_text(encoding="utf-8")
    output = args.output or default_output(args.project_root, args.date)
    project_name = args.project or args.project_root.name

    content = (
        template.replace("{{DATE}}", args.date)
        .replace("{{PROJECT}}", project_name)
        .replace("{{PAPER}}", args.paper or "未指定")
        .replace("{{FIGURE_ROWS}}", figure_rows(args.figure_count))
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
