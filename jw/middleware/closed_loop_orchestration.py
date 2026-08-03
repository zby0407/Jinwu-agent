"""Safety boundaries for explicitly declared end-to-end research tasks.

The middleware deliberately does *not* prescribe a planner → hypothesis →
experiment sequence. Research stages may be skipped, revisited, or returned as
partial work. The remaining deterministic checks protect real execution inputs
and prevent a receipt from claiming an artifact that does not exist.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..research_protocols import sha256_file
from ..research_review import ResearchReviewStore
from ..workspaces import workspace_root_from_config

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest

_SPECIALISTS = (
    "solar-planner",
    "solar-hypothesis",
    "solar-experiment",
)
_RECEIPT_SPECIALISTS = (
    "solar-planner",
    "solar-data",
    "solar-hypothesis",
    "solar-experiment",
    "solar-evidence",
)
_CLOSED_LOOP_MARKERS = (
    "完整研究",
    "完整整合",
    "科研闭环",
    "形成论文",
    "完整报告",
    "complete research",
    "end-to-end research",
    "full research",
)
_CLOSED_LOOP_STAGES = (
    ("数据", "data", "文献", "literature"),
    ("计划", "规划", "plan"),
    ("假设", "解释", "hypothesis"),
    ("实验", "验证", "experiment"),
    ("报告", "论文", "report", "paper"),
)
_DATA_DEPENDENT = re.compile(
    r"(?:现有|已有|workspace|本地|provided|existing).{0,20}(?:数据|文件|data|file)|"
    r"(?:数据|文件|data|file).{0,20}(?:分析|读取|使用|检验|预测|analy|read|use|test|predict)|"
    r"\.(?:csv|tsv|parquet|json|fits?|nc|h5)\b",
    re.IGNORECASE,
)


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return " ".join(
            str(part.get("text", ""))
            for part in value
            if isinstance(part, Mapping) and part.get("text")
        )
    return ""


def _todo_text(todos: object) -> str:
    if not isinstance(todos, Sequence) or isinstance(todos, (str, bytes)):
        return ""
    return " ".join(
        str(todo.get("content", "")) for todo in todos if isinstance(todo, Mapping)
    )


def _state_text(state: object) -> str:
    """Return user/todo text used only for deterministic intent gates."""

    if not isinstance(state, Mapping):
        return ""
    chunks = [_todo_text(state.get("todos"))]
    messages = state.get("messages", [])
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for message in messages:
            role = str(_field(message, "type", _field(message, "role", "")))
            if role in {"human", "user"}:
                chunks.append(_content_text(_field(message, "content", "")))
    return " ".join(chunks)


def _natural_closed_loop_intent(text: str) -> bool:
    folded = text.casefold()
    if not any(marker in folded for marker in _CLOSED_LOOP_MARKERS):
        return False
    covered = sum(
        any(marker in folded for marker in alternatives)
        for alternatives in _CLOSED_LOOP_STAGES
    )
    return covered >= 4


def _declares_full_closed_loop(state: object) -> bool:
    if not isinstance(state, Mapping):
        return False
    route = state.get("research_route")
    if isinstance(route, Mapping) and route.get("mode") == "full_research":
        return True
    chunks = [_todo_text(state.get("todos"))]
    messages = state.get("messages", [])
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        for message in messages:
            role = str(_field(message, "type", _field(message, "role", "")))
            if role in {"human", "user"}:
                chunks.append(_content_text(_field(message, "content", "")))
            calls = _field(message, "tool_calls", [])
            if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
                continue
            for call in calls:
                if _field(call, "name", "") != "write_todos":
                    continue
                args = _field(call, "args", {})
                if isinstance(args, Mapping):
                    chunks.append(_todo_text(args.get("todos")))
    declared = " ".join(chunks).casefold()
    return all(
        name in declared for name in _SPECIALISTS
    ) or _natural_closed_loop_intent(_state_text(state))


def _has_staged_input(workspace_root: Path, description: str = "") -> bool:
    if re.search(r"(?:^|[\s'\"`])(?:/?inputs/|runs/[^\s]+/public/)", description):
        return True
    root = workspace_root / "inputs"
    try:
        return root.is_dir() and any(path.is_file() for path in root.rglob("*"))
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _contains_exact(value: object, target: str) -> bool:
    if isinstance(value, Mapping):
        return any(
            key == target or _contains_exact(item, target)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_exact(item, target) for item in value)
    return value == target


def _valid_dataset_receipt(
    workspace_root: Path,
    payload: dict[str, Any],
) -> bool:
    if payload.get("schema_version") != 1 or payload.get("status") != "verified":
        return False
    artifact_ref = str(payload.get("canonical_artifact") or "").strip()
    expected_sha = str(payload.get("canonical_sha256") or "").strip()
    if not artifact_ref or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        return False
    relative = Path(artifact_ref)
    if relative.is_absolute() or ".." in relative.parts:
        return False
    if len(relative.parts) == 1:
        relative = Path("work") / relative
    artifact = (workspace_root / relative).resolve()
    try:
        artifact.relative_to(workspace_root.resolve())
    except ValueError:
        return False
    return artifact.is_file() and sha256_file(artifact) == expected_sha


def _valid_experiment_receipt(
    payload: dict[str, Any],
    path: Path,
    required_measurement_ids: Sequence[str],
) -> bool:
    if payload.get("phase") != "report_finalized":
        return False
    run_root = path.parent
    entry = _read_json(run_root / "entry_result.json")
    if entry is None:
        return False
    unhashed = dict(entry)
    entry_sha = unhashed.pop("entry_sha256", None)
    try:
        canonical = json.dumps(
            unhashed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return False
    if entry_sha != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
        return False
    if (
        entry.get("schema_version") != "automatic-experiment-entry-result-v1"
        or entry.get("status") != "finalized"
        or entry.get("run_id") != payload.get("run_id")
        or entry.get("outcome") != payload.get("outcome")
        or entry.get("record_path") != "record.json"
        or entry.get("report_path") != "report.md"
    ):
        return False
    for artifact_name, entry_hash_field, state_hash_field in (
        ("record.json", "record_sha256", "verified_record_sha256"),
        ("report.md", "report_sha256", "report_sha256"),
        ("audit.md", "audit_sha256", "audit_sha256"),
    ):
        artifact = run_root / artifact_name
        declared = entry.get(entry_hash_field)
        if (
            not artifact.is_file()
            or not isinstance(declared, str)
            or re.fullmatch(r"[0-9a-f]{64}", declared) is None
            or sha256_file(artifact) != declared
            or payload.get(state_hash_field) != declared
        ):
            return False
    try:
        if entry.get("user_display_markdown") != (run_root / "report.md").read_text(
            encoding="utf-8"
        ):
            return False
    except (OSError, UnicodeError):
        return False
    assets = entry.get("report_assets")
    if not isinstance(assets, list) or payload.get("report_assets") != assets:
        return False
    for asset in assets:
        if not isinstance(asset, Mapping):
            return False
        asset_ref = str(asset.get("path") or "")
        relative = Path(asset_ref)
        if (
            not asset_ref.startswith("report_assets/")
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            return False
        asset_path = run_root / relative
        if not asset_path.is_file() or sha256_file(asset_path) != asset.get("sha256"):
            return False
    if not required_measurement_ids:
        return True
    record = _read_json(run_root / "record.json")
    return record is not None and all(
        _contains_exact(record, measurement_id)
        for measurement_id in required_measurement_ids
    )


def _latest_valid(
    paths: Sequence[Path], predicate: Callable[[dict[str, Any], Path], bool]
) -> Path | None:
    def modified_at(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            # A contract directory may disappear between globbing and sorting.
            # Treat that race as an invalid candidate instead of failing the
            # orchestration guard itself.
            return float("-inf")

    for path in sorted(paths, key=modified_at, reverse=True):
        payload = _read_json(path)
        if payload is not None and predicate(payload, path):
            return path
    return None


def _accepted_release_verdict(workspace_root: Path) -> Path | None:
    """Return only the verdict bound to the current accepted release hash."""

    state = _read_json(workspace_root / "research_review" / "run_state.json")
    task_id = state.get("task_id") if state is not None else None
    if not isinstance(task_id, str) or not task_id:
        return None
    try:
        store = ResearchReviewStore(workspace_root, task_id)
        release = store.latest_artifact("final_release")
        if release is None or store.accepted_release_markdown() is None:
            return None
        verdict = store.matching_verdict("final_release", [store.artifact_ref(release)])
    except (OSError, RuntimeError, ValueError):
        return None
    if verdict is None:
        return None
    path = (
        workspace_root / "research_review" / "verdicts" / f"{verdict['review_id']}.json"
    )
    return path if path.is_file() else None


def closed_loop_receipts(
    workspace_root: Path,
    *,
    required_measurement_ids: Sequence[str] = (),
) -> dict[str, Path | None]:
    """Return verified task-local artifacts for data and closed-loop stages."""

    dataset_receipt = workspace_root / "receipts" / "datasets" / "f107_semantics.json"
    dataset = _latest_valid(
        [dataset_receipt] if dataset_receipt.is_file() else [],
        lambda payload, _path: _valid_dataset_receipt(workspace_root, payload),
    )
    planner = _latest_valid(
        list((workspace_root / "planner" / "runs").glob("*/research_plan.json")),
        lambda payload, _path: payload.get("status") == "frozen",
    )
    hypothesis = _latest_valid(
        list(
            (workspace_root / "hypothesis" / "runs").glob("*/hypothesis_portfolio.json")
        ),
        lambda payload, _path: payload.get("status") == "frozen",
    )

    experiment = _latest_valid(
        list((workspace_root / "experiment" / "runs").glob("*/state.json")),
        lambda payload, path: _valid_experiment_receipt(
            payload,
            path,
            required_measurement_ids,
        ),
    )
    evidence = _accepted_release_verdict(workspace_root)
    return {
        "solar-data": dataset,
        "solar-planner": planner,
        "solar-hypothesis": hypothesis,
        "solar-experiment": experiment,
        "solar-evidence": evidence,
    }


def _claims_success(value: object) -> bool:
    text = str(value or "").casefold()
    negative = ("incomplete", "failed", "failure", "blocked", "error")
    positive = ("success", "frozen", "finalized", "completed")
    return any(word in text for word in positive) and not any(
        word in text for word in negative
    )


class ClosedLoopOrchestrationGuardMiddleware(AgentMiddleware[Any, Any, Any]):
    """Protect execution boundaries without forcing a fixed research route."""

    def _blocked(self, request: ToolCallRequest, reason: str) -> ToolMessage:
        name = str(request.tool_call.get("name") or "unknown_tool")
        return ToolMessage(
            content=f"[CLOSED LOOP BLOCKED] {reason}",
            tool_call_id=str(
                request.tool_call.get("id") or "closed-loop-blocked-tool-call"
            ),
            name=name,
            status="error",
        )

    def _preflight(self, request: ToolCallRequest) -> ToolMessage | None:
        if not _declares_full_closed_loop(request.state):
            return None
        config = getattr(request.runtime, "config", None)
        workspace_root = workspace_root_from_config(config)
        receipts = closed_loop_receipts(workspace_root)
        state_text = _state_text(request.state)
        name = str(request.tool_call.get("name") or "")
        args = request.tool_call.get("args", {})
        args = args if isinstance(args, Mapping) else {}

        if name == "task":
            specialist = str(args.get("subagent_type") or "")
            if (
                specialist == "solar-experiment"
                and _DATA_DEPENDENT.search(state_text)
                and not _has_staged_input(
                    workspace_root, str(args.get("description") or "")
                )
            ):
                return self._blocked(
                    request,
                    "the experiment is data-dependent but no immutable input is staged. "
                    "Copy the exact source file under /inputs/ (or reference a completed "
                    "runs/<id>/public/ artifact), then include that path in the "
                    "solar-experiment task description.",
                )

        if name == "write_file":
            file_path = str(args.get("file_path") or args.get("path") or "")
            if file_path.rstrip("/").endswith("receipts/closed_loop_receipts.json"):
                try:
                    summary = json.loads(str(args.get("content") or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return self._blocked(
                        request,
                        "closed-loop receipt summary must be valid JSON.",
                    )
                statuses = (
                    summary.get("contract_status", {})
                    if isinstance(summary, Mapping)
                    else {}
                )
                if isinstance(statuses, Mapping):
                    for specialist in _RECEIPT_SPECIALISTS:
                        row = statuses.get(specialist)
                        if not isinstance(row, Mapping):
                            continue
                        if (
                            _claims_success(row.get("status"))
                            and receipts[specialist] is None
                        ):
                            return self._blocked(
                                request,
                                f"receipt summary falsely claims {specialist} success; "
                                "no verified task-local artifact exists.",
                            )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._preflight(request)
        return blocked if blocked is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        # Artifact discovery reads task-local JSON and scans three small run
        # directories.  LangGraph dev rejects those blocking filesystem calls
        # on its event loop, so keep the async middleware path genuinely
        # non-blocking while sharing the exact same fail-closed logic.
        blocked = await asyncio.to_thread(self._preflight, request)
        return blocked if blocked is not None else await handler(request)


__all__ = [
    "ClosedLoopOrchestrationGuardMiddleware",
    "closed_loop_receipts",
]
