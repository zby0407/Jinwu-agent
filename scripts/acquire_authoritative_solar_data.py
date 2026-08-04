"""Acquire the curated SILSO and MWO/WSO inputs into a JW project."""

from __future__ import annotations

import argparse
import json

from jw.solar_data_catalog import acquire_authoritative_solar_data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--project-id", default="default")
    args = parser.parse_args()
    records = acquire_authoritative_solar_data(
        args.workspace, project_id=args.project_id
    )
    print(json.dumps({"status": "registered", "files": records}, indent=2))


if __name__ == "__main__":
    main()
