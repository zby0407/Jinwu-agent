#!/usr/bin/env python3
"""JSON-only CLI for the reviewed B3 registered-experiment boundary."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from b3cycle.science_agents import (  # noqa: E402
    REGISTERED_EXPERIMENTS,
    RunStore,
    ScienceAgentError,
    canonical_json_sha256,
    run_registered_experiment,
    submit_hypothesis_portfolio_draft,
    submit_research_plan_draft,
    validate_experiment_manifest,
    validate_hypothesis_portfolio_against_run,
    validate_research_plan,
)
from b3cycle.science_toolkit import (  # noqa: E402
    ScientificToolkitError,
    discover_tools,
    inspect_tool,
    run_scientific_tool,
    trace_artifact_lineage,
    verify_tool_result,
)


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)([a-z0-9_-]*(?:api[_-]?key|token|password|secret)[a-z0-9_-]*)"
    r"[\"']?\s*[:=]\s*[\"']?([^\s,;}\"']+)"
)
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s,;}]+)")
_MAX_STDIN_JSON_BYTES = 64 * 1024


def _run_store_root() -> Path:
    value = os.getenv("B3_RUNTIME_ROOT")
    if not value:
        return ROOT / "b3" / "agent_runs"
    runtime = Path(value).expanduser().resolve()
    if runtime != ROOT.resolve() / "runtime":
        raise ScienceAgentError("B3_RUNTIME_ROOT must equal the project runtime directory")
    return runtime / "agent_runs"


class JsonArgumentParser(argparse.ArgumentParser):
    """Keep parser diagnostics inside a single JSON stdout value."""

    def error(self, message: str) -> None:
        raise ScienceAgentError(message)

    def print_help(self, file: Any = None) -> None:
        stream = file or sys.stdout
        stream.write(
            json.dumps(
                {"status": "help", "help": self.format_help()},
                ensure_ascii=False,
            )
            + "\n"
        )


def _safe_message(exc: Exception) -> str:
    message = str(exc).strip() or "science-agent command failed"
    message = _SECRET_ASSIGNMENT_RE.sub(r"\1=<redacted>", message)
    message = _BEARER_RE.sub(r"\1<redacted>", message)
    return message[:500]


def build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-run")
    init.add_argument("--task", required=True)

    run = sub.add_parser("run-experiment")
    run.add_argument("--run-id", required=True)
    run.add_argument(
        "--experiment-id",
        choices=sorted(REGISTERED_EXPERIMENTS),
        required=True,
    )
    run.add_argument("--plan-node-id", required=True)
    run.add_argument("--seed", type=int, default=0)

    submit_plan = sub.add_parser("submit-plan")
    submit_plan.add_argument("--run-id", required=True)

    submit_portfolio = sub.add_parser("submit-portfolio")
    submit_portfolio.add_argument("--run-id", required=True)

    validate_portfolio = sub.add_parser("validate-portfolio")
    validate_portfolio.add_argument("--run-id", required=True)

    validate = sub.add_parser("validate-run")
    validate.add_argument("--run-id", required=True)

    discover = sub.add_parser("discover-tools")
    discover.add_argument("--query", default="")
    discover.add_argument("--agent", required=True)
    discover.add_argument("--limit", type=int, default=20)
    discover.add_argument(
        "--human-offline",
        action="store_true",
        help="explicit untrusted human inspection; never issues a verifiable receipt",
    )

    inspect = sub.add_parser("inspect-tool")
    inspect.add_argument("--tool-id", required=True)
    inspect.add_argument("--agent", required=True)
    inspect.add_argument(
        "--human-offline",
        action="store_true",
        help="explicit untrusted human inspection; never issues a verifiable receipt",
    )

    run_tool = sub.add_parser("run-tool")
    run_tool.add_argument("--tool-id", required=True)
    run_tool.add_argument("--agent", required=True)
    run_tool.add_argument(
        "--human-offline",
        action="store_true",
        help="explicit untrusted human run; output is non-claimable and has no receipt",
    )

    verify_tool = sub.add_parser("verify-tool-result")
    verify_tool.add_argument("--agent", required=True)

    trace = sub.add_parser("trace-artifact")
    trace.add_argument("--run-id", required=True)
    trace.add_argument("--artifact-path", required=True)
    trace.add_argument("--agent", required=True)
    trace.add_argument(
        "--human-offline",
        action="store_true",
        help="explicit untrusted human trace; output is non-claimable",
    )
    return parser


def _read_stdin_json(stream: BinaryIO) -> dict[str, Any]:
    raw = stream.read(_MAX_STDIN_JSON_BYTES + 1)
    if len(raw) > _MAX_STDIN_JSON_BYTES:
        raise ScienceAgentError("stdin JSON exceeds 64 KiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScienceAgentError("stdin must be UTF-8 JSON") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScienceAgentError("stdin is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ScienceAgentError("stdin JSON must be an object")
    return payload


def validate_run(store: RunStore, run_id: str) -> dict[str, Any]:
    run_manifest = store.read_artifact(run_id, "run_manifest.json")
    if run_manifest.get("run_id") != run_id:
        raise ScienceAgentError("run manifest does not match requested run_id")

    run_dir = (store.root / run_id).resolve()
    artifacts: list[str] = []
    payloads: dict[str, dict[str, Any]] = {}
    experiment_manifests: list[tuple[str, dict[str, Any]]] = []
    plan_payload: dict[str, Any] | None = None
    plan_status = "missing"
    portfolio_payload: dict[str, Any] | None = None
    portfolio_status = "missing"
    for path in sorted(run_dir.rglob("*.json")):
        relative = path.relative_to(run_dir).as_posix()
        payload = store.read_artifact(run_id, relative)
        artifacts.append(relative)
        payloads[relative] = payload
        if relative == "research_plan.json":
            validate_research_plan(payload)
            plan_payload = payload
            plan_status = "valid"
        elif relative == "hypothesis_portfolio.json":
            portfolio_payload = payload
        elif relative.startswith("experiments/") and relative.endswith(
            "/manifest.json"
        ):
            validate_experiment_manifest(payload)
            experiment_manifests.append((relative, payload))

    if portfolio_payload is not None:
        validate_hypothesis_portfolio_against_run(
            store, run_id, portfolio_payload
        )
        portfolio_status = "valid"

    if run_manifest.get("research_question_binding") == "exact":
        task = run_manifest.get("task")
        if not isinstance(task, str) or not task.strip():
            raise ScienceAgentError("run manifest task binding is invalid")
        if run_manifest.get("task_sha256") != canonical_json_sha256(task):
            raise ScienceAgentError("run manifest task hash does not match task")
        if plan_payload is not None and plan_payload.get("research_question") != task:
            raise ScienceAgentError(
                "research plan question does not match the initialized task"
            )

    for relative, manifest in experiment_manifests:
        if manifest.get("run_id") != run_id:
            raise ScienceAgentError(f"experiment manifest run_id mismatch: {relative}")
        experiment_id = manifest.get("experiment_id")
        if experiment_id not in REGISTERED_EXPERIMENTS:
            raise ScienceAgentError(
                f"unregistered experiment manifest in run: {relative}"
            )
        expected_node_id = f"{experiment_id}_seed{manifest['seed']}"
        if manifest.get("node_id") != expected_node_id:
            raise ScienceAgentError(f"experiment manifest node_id mismatch: {relative}")
        expected_manifest_path = f"experiments/{expected_node_id}/manifest.json"
        if relative != expected_manifest_path:
            raise ScienceAgentError(
                f"experiment manifest path does not match node_id: {relative}"
            )
        expected_result_path = f"experiments/{expected_node_id}/result.json"
        referenced_paths = {reference["path"] for reference in manifest["artifacts"]}
        if expected_result_path not in referenced_paths:
            raise ScienceAgentError(
                f"canonical referenced result is missing: {expected_result_path}"
            )
        for reference in manifest["artifacts"]:
            referenced_path = reference["path"]
            referenced = payloads.get(referenced_path)
            if referenced is None:
                raise ScienceAgentError(
                    f"referenced artifact is missing: {referenced_path}"
                )
            if referenced.get("artifact_sha256") != reference["sha256"]:
                raise ScienceAgentError(
                    f"referenced artifact hash mismatch: {referenced_path}"
                )
            if referenced_path == expected_result_path:
                expected_result_fields = {
                    "schema_version": "b3-registered-experiment-result-v1",
                    "run_id": run_id,
                    "node_id": expected_node_id,
                    "experiment_id": experiment_id,
                    "seed": manifest["seed"],
                    "status": manifest["status"],
                }
                for field, expected in expected_result_fields.items():
                    if referenced.get(field) != expected:
                        raise ScienceAgentError(
                            f"referenced result envelope {field} mismatch: {referenced_path}"
                        )

        if manifest["status"] != "failed":
            if plan_payload is None:
                raise ScienceAgentError(
                    f"non-failed experiment manifest has no frozen plan: {relative}"
                )
            nodes = {
                node["id"]: node
                for node in plan_payload["task_graph"]
                if isinstance(node, dict)
            }
            parent = nodes.get(manifest["parent_id"])
            if parent is None:
                raise ScienceAgentError(
                    f"experiment manifest parent_id is absent from plan: {relative}"
                )
            if parent.get("tool") != f"registered:{experiment_id}":
                raise ScienceAgentError(
                    f"experiment manifest parent tool mismatch: {relative}"
                )
            provenance = manifest.get("provenance", {})
            if provenance.get("plan_artifact_sha256") != plan_payload.get(
                "artifact_sha256"
            ):
                raise ScienceAgentError(
                    f"experiment manifest frozen-plan hash mismatch: {relative}"
                )

    return {
        "status": "ok" if plan_status == "valid" else "warning",
        "run_id": run_id,
        "research_plan": plan_status,
        "hypothesis_portfolio": portfolio_status,
        "artifact_count": len(artifacts),
        "experiment_manifest_count": len(experiment_manifests),
        "artifacts": artifacts,
    }


def dispatch(
    args: argparse.Namespace,
    store: RunStore,
    stdin: BinaryIO | None = None,
) -> dict[str, Any]:
    if args.command == "init-run":
        run = store.create_run(args.task)
        return {"status": "created", **run}
    if args.command == "run-experiment":
        return run_registered_experiment(
            store,
            args.run_id,
            args.experiment_id,
            args.plan_node_id,
            args.seed,
        )
    if args.command == "submit-plan":
        artifact = submit_research_plan_draft(
            store,
            args.run_id,
            _read_stdin_json(stdin or sys.stdin.buffer),
        )
        return {
            "status": "submitted",
            "run_id": args.run_id,
            "artifact_path": "research_plan.json",
            "artifact": artifact,
        }
    if args.command == "submit-portfolio":
        artifact = submit_hypothesis_portfolio_draft(
            store,
            args.run_id,
            _read_stdin_json(stdin or sys.stdin.buffer),
        )
        return {
            "status": "submitted",
            "run_id": args.run_id,
            "artifact_path": "hypothesis_portfolio.json",
            "artifact": artifact,
        }
    if args.command == "validate-portfolio":
        artifact = store.read_artifact(args.run_id, "hypothesis_portfolio.json")
        validate_hypothesis_portfolio_against_run(store, args.run_id, artifact)
        return {
            "status": "ok",
            "run_id": args.run_id,
            "artifact_path": "hypothesis_portfolio.json",
            "artifact_sha256": artifact["artifact_sha256"],
        }
    if args.command == "validate-run":
        return validate_run(store, args.run_id)
    if args.command == "discover-tools":
        return discover_tools(
            args.query,
            args.agent,
            args.limit,
            human_offline=args.human_offline,
        )
    if args.command == "inspect-tool":
        return inspect_tool(
            args.tool_id, args.agent, human_offline=args.human_offline
        )
    if args.command == "run-tool":
        return run_scientific_tool(
            args.tool_id,
            _read_stdin_json(stdin or sys.stdin.buffer),
            args.agent,
            human_offline=args.human_offline,
        )
    if args.command == "verify-tool-result":
        return verify_tool_result(
            _read_stdin_json(stdin or sys.stdin.buffer), args.agent
        )
    if args.command == "trace-artifact":
        return trace_artifact_lineage(
            args.run_id,
            args.artifact_path,
            args.agent,
            human_offline=args.human_offline,
        )
    raise ScienceAgentError(f"unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        store = RunStore(_run_store_root())
        payload = dispatch(args, store, stdin=sys.stdin.buffer)
        recorded_failure_is_output = args.command in {
            "run-tool",
            "verify-tool-result",
            "trace-artifact",
        }
        exit_code = (
            2
            if not recorded_failure_is_output
            and payload.get("status") in {"failed", "quarantined"}
            else 0
        )
    except (ScienceAgentError, ScientificToolkitError) as exc:
        payload = {"status": "error", "error": _safe_message(exc)}
        exit_code = 2
    except Exception as exc:
        payload = {
            "status": "error",
            "error": "internal science-agent CLI failure",
            "type": type(exc).__name__,
        }
        exit_code = 3
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
