"""Explicit toolkit commands; nothing runs merely by importing or displaying help."""

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="SHARP scalar 24-hour experimental forecasting toolkit"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("verify-assets")
    p.add_argument("--assets", type=Path, required=True)
    p = commands.add_parser("import-assets")
    p.add_argument("--raw", type=Path, required=True)
    p.add_argument(
        "--sources",
        type=Path,
        required=True,
        help="JSON mapping pinned asset names to local source files",
    )
    p.add_argument("--output", type=Path, required=True)
    for command in ("export-model", "score", "fetch-live", "issue"):
        p = commands.add_parser(command)
        p.add_argument("--assets", type=Path, required=True)
        if command == "score":
            p.add_argument("--input", type=Path, required=True)
        elif command == "fetch-live":
            p.add_argument("--output", type=Path, required=True)
        elif command == "issue":
            for name in ("live-dir", "contract", "data-review", "output"):
                p.add_argument("--" + name, type=Path, required=True)
    for command in ("prepare", "train", "audit-labels"):
        p = commands.add_parser(command)
        p.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    from .assets import import_assets, verify_assets
    from .workflow import fetch_live, issue, score_csv

    if args.command == "verify-assets":
        result = verify_assets(args.assets)
    elif args.command == "import-assets":
        sources = json.loads(args.sources.read_text(encoding="utf-8-sig"))
        result = import_assets(args.raw, sources, args.output)
    elif args.command == "export-model":
        from .export import export_forest

        result = export_forest(args.assets)
    elif args.command == "score":
        result = score_csv(args.assets, args.input)
    elif args.command == "fetch-live":
        result = fetch_live(args.assets, args.output)
    elif args.command == "issue":
        result = issue(
            args.assets, args.live_dir, args.contract, args.data_review, args.output
        )
    else:
        import sys
        from .engine import load

        names = {
            "prepare": "available24_data",
            "train": "available24_model",
            "audit-labels": "independent_label_audit",
        }
        previous = sys.argv
        extra = args.args[1:] if args.args[:1] == ["--"] else args.args
        prefix = [args.command] if args.command in ("prepare", "train") else []
        sys.argv = [names[args.command], *prefix, *extra]
        try:
            return load(names[args.command]).main()
        finally:
            sys.argv = previous
    print(json.dumps(result, ensure_ascii=False))
    return 0
