#!/usr/bin/env python3
"""Internal deterministic bridge for the Pi Research Planner tools.

This module is not a product or model entry point. Pi owns the Agent loop,
provider, prompts, Skills, tool lifecycle, and session state. The extension
invokes this bridge only for bounded contract checks and read-only knowledge
operations that are easier to keep deterministic in Python.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research_planner.contracts import (  # noqa: E402
    ContractError,
    validate_planner_request,
)
from research_planner.harness import (  # noqa: E402
    build_natural_planner_request,
    build_planning_brief,
    freeze_research_plan,
    preflight_planner_response,
)
from research_planner.knowledge import (  # noqa: E402
    extract_source_evidence,
    inspect_dataset,
    resolve_reference,
    search_local_knowledge,
    search_scholarly_literature,
)


def handoff(reason: str, missing: list[str], next_action: str) -> dict[str, Any]:
    return {
        "schema_version": "research-planner-handoff-v1",
        "status": "needs_revision",
        "blocking_reasons": [reason],
        "missing_inputs": missing,
        "safe_next_action": next_action,
    }


def read_stdin_object() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("stdin must contain one UTF-8 JSON object") from exc
    if not isinstance(value, dict):
        raise ContractError("stdin must contain one JSON object")
    return value


def run_pi_bridge(mode: str) -> int:
    try:
        payload = read_stdin_object()
        if mode == "brief":
            if "request" in payload:
                request = validate_planner_request(payload.get("request"))
            else:
                request = build_natural_planner_request(payload.get("research_question"))
            result = build_planning_brief(request)
        elif mode == "local-search":
            result = search_local_knowledge(payload.get("query"), payload.get("limit", 5))
        elif mode == "literature-search":
            result = search_scholarly_literature(
                payload.get("query"),
                payload.get("limit", 5),
                payload.get("from_year"),
                payload.get("to_year"),
            )
        elif mode == "resolve-reference":
            result = resolve_reference(payload.get("reference"))
        elif mode == "extract-evidence":
            result = extract_source_evidence(
                payload.get("source_id"),
                payload.get("claim"),
                source_text=payload.get("source_text"),
                local_path=payload.get("local_path"),
                relationship=payload.get("relationship", "context"),
                limit=payload.get("limit", 5),
            )
        elif mode == "inspect-dataset":
            result = inspect_dataset(
                payload.get("local_path"),
                expected_variables=payload.get("expected_variables"),
                time_field=payload.get("time_field"),
                sample_limit=payload.get("sample_limit", 5_000),
            )
        else:
            request = validate_planner_request(payload.get("request"))
            response = payload.get("response")
            if mode in {"freeze", "preflight"} and not isinstance(response, dict):
                raise ContractError("response must be a JSON object")
            if mode == "preflight":
                result = preflight_planner_response(
                    request, response, include_validated_response=True
                )
            elif mode == "freeze":
                result = freeze_research_plan(request, response)
                result["mode"] = "pi"
            else:
                raise ContractError(f"unsupported Pi bridge mode: {mode}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError, csv.Error) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "research-planner-outcome-v1",
                    "status": "needs_revision",
                    "validation_error": str(exc),
                    "user_message": "研究规划仍在内部校正；请保留已正确内容并一次性修正全部列出问题。",
                    "handoff": handoff(
                        "Pi planner request failed deterministic validation",
                        [],
                        "repair every listed issue together, then resubmit the candidate",
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-brief-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-freeze-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-preflight-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-local-search-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-literature-search-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-resolve-reference-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-extract-evidence-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-inspect-dataset-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    bridge_modes = (
        (args.pi_brief_stdin, "brief"),
        (args.pi_freeze_stdin, "freeze"),
        (args.pi_preflight_stdin, "preflight"),
        (args.pi_local_search_stdin, "local-search"),
        (args.pi_literature_search_stdin, "literature-search"),
        (args.pi_resolve_reference_stdin, "resolve-reference"),
        (args.pi_extract_evidence_stdin, "extract-evidence"),
        (args.pi_inspect_dataset_stdin, "inspect-dataset"),
    )
    selected_modes = [mode for enabled, mode in bridge_modes if enabled]
    if len(selected_modes) != 1:
        print(
            json.dumps(
                {
                    "schema_version": "research-planner-outcome-v1",
                    "status": "pi_required",
                    "error": "This file is an internal Pi Tool bridge; start the Agent through Pi.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    return run_pi_bridge(selected_modes[0])


if __name__ == "__main__":
    raise SystemExit(main())
