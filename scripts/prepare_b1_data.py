"""Prepare registered B1 inputs without running a scientific experiment."""

from __future__ import annotations

import argparse
import json

from jw.b1_data import prepare_b1_inputs, register_b1_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--output", required=True, help="A directory that does not exist"
    )
    parser.add_argument(
        "--register-runtime", help="Optional new isolated B1 runtime directory"
    )
    args = parser.parse_args()
    qa = prepare_b1_inputs(args.manifest, args.output)
    if args.register_runtime:
        qa["registered_inputs"] = len(
            register_b1_inputs(args.output, args.register_runtime)
        )
    print(json.dumps(qa, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
