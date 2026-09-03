"""Submission service for the fixed H1/H2 reproduction suite."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from jw.langgraph_dev.sdk import (
    configured_langgraph_dev_url,
    get_langgraph_async_client,
    messages_input,
)
from jw.workspaces import WorkspaceBinding, ensure_thread_workspace

from .suite import (
    CASES,
    MODEL_NAME,
    MODEL_PROVIDER,
    PROJECT_ID,
    SCHEMA_VERSION,
    SUITE_ID,
    ReproductionCase,
)


class AsyncThreads(Protocol):
    async def create(
        self, *, graph_id: str, metadata: dict[str, str]
    ) -> Mapping[str, Any]: ...

    async def delete(self, thread_id: str) -> object: ...


class AsyncRuns(Protocol):
    async def create(
        self,
        thread_id: str,
        assistant_id: str,
        *,
        input: Mapping[str, Any],
        metadata: dict[str, str],
        config: dict[str, Any],
    ) -> Mapping[str, Any]: ...


class AsyncClient(Protocol):
    threads: AsyncThreads
    runs: AsyncRuns


JsonWriter = Callable[[Path, Mapping[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _case_metadata(
    *,
    case: ReproductionCase,
    batch_id: str,
    trigger: str,
    launched_at: str,
    thread_id: str,
    run_id: str | None,
    binding: WorkspaceBinding,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "batch_id": batch_id,
        "case_id": case.case_id,
        "trigger": trigger,
        "launched_at": launched_at,
        "model": {"name": MODEL_NAME, "provider": MODEL_PROVIDER},
        "thread_id": thread_id,
        "langgraph_run_id": run_id,
        "workspace": binding.workspace,
        "workspace_run_id": binding.run_id,
        "project_id": PROJECT_ID,
        "prompt": case.prompt,
        "prompt_sha256": case.prompt_sha256,
        "declared_inputs": list(case.declared_inputs),
        "expected_artifacts": list(case.expected_artifacts),
        "claim_boundary": (
            "This receipt proves dispatch only; it does not prove LangGraph "
            "success, scientific-review acceptance, or research success."
        ),
    }


def _task_payload(
    *,
    binding: WorkspaceBinding,
    case: ReproductionCase,
    reproduction_launch: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve the standard task contract and attach launch audit metadata."""
    return {
        "schema_version": 2,
        "thread_id": binding.thread_id,
        "project_id": binding.project_id,
        "run_id": binding.run_id,
        "research_question": case.prompt,
        "status": "active",
        "created_at": binding.created_at,
        "reproduction_launch": dict(reproduction_launch),
    }


def _prepare_workspace(
    *,
    case: ReproductionCase,
    batch_id: str,
    trigger: str,
    launched_at: str,
    thread_id: str,
    base_workspace: Path,
    writer: JsonWriter,
) -> WorkspaceBinding:
    binding = ensure_thread_workspace(
        thread_id,
        base_workspace,
        project_id=PROJECT_ID,
        first_request=case.prompt,
    )
    task_path = Path(binding.workspace) / "task.json"
    task = _case_metadata(
        case=case,
        batch_id=batch_id,
        trigger=trigger,
        launched_at=launched_at,
        thread_id=thread_id,
        run_id=None,
        binding=binding,
    )
    writer(
        task_path,
        _task_payload(binding=binding, case=case, reproduction_launch=task),
    )
    return binding


async def _delete_failed_thread(client: AsyncClient, thread_id: str) -> None:
    try:
        await client.threads.delete(thread_id)
    except Exception:
        pass


async def _launch_case(
    *,
    client: AsyncClient,
    case: ReproductionCase,
    batch_id: str,
    trigger: str,
    launched_at: str,
    base_workspace: Path,
    writer: JsonWriter,
) -> tuple[dict[str, Any] | None, dict[str, str] | None, WorkspaceBinding | None]:
    thread_id = ""
    binding: WorkspaceBinding | None = None
    try:
        thread = await client.threads.create(
            graph_id="JW",
            metadata={
                "title": f"一次性复现 {case.case_id}",
                "reproduction_suite": SUITE_ID,
                "reproduction_case": case.case_id,
                "reproduction_batch": batch_id,
            },
        )
        thread_id = str(thread["thread_id"])
        binding = await asyncio.to_thread(
            _prepare_workspace,
            case=case,
            batch_id=batch_id,
            trigger=trigger,
            launched_at=launched_at,
            thread_id=thread_id,
            base_workspace=base_workspace,
            writer=writer,
        )
        configurable = {
            "model": MODEL_NAME,
            "model_provider": MODEL_PROVIDER,
            "project_id": PROJECT_ID,
            "workspace_thread_id": thread_id,
            "reproduction_suite_id": SUITE_ID,
            "reproduction_batch_id": batch_id,
            "reproduction_case_id": case.case_id,
            "auto_mode": True,
            "auto_approve": True,
            "enable_ask_user": False,
        }
        run = await client.runs.create(
            thread_id,
            "JW-reproduction",
            input=messages_input(case.prompt),
            metadata={
                "suite_id": SUITE_ID,
                "batch_id": batch_id,
                "case_id": case.case_id,
                "prompt_sha256": case.prompt_sha256,
            },
            config={"configurable": configurable},
        )
        run_id = str(run["run_id"])
    except Exception as exc:
        if thread_id:
            await _delete_failed_thread(client, thread_id)
        return (
            None,
            {"case_id": case.case_id, "stage": "submit", "message": str(exc)},
            binding,
        )

    record = _case_metadata(
        case=case,
        batch_id=batch_id,
        trigger=trigger,
        launched_at=launched_at,
        thread_id=thread_id,
        run_id=run_id,
        binding=binding,
    )
    launch = {
        "case_id": case.case_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "workspace": binding.workspace,
        "prompt_sha256": case.prompt_sha256,
    }
    try:
        await asyncio.to_thread(
            writer,
            Path(binding.workspace) / "task.json",
            _task_payload(binding=binding, case=case, reproduction_launch=record),
        )
        await asyncio.to_thread(
            writer,
            Path(binding.workspace) / "receipts" / "reproduction_launch.json",
            record,
        )
    except Exception as exc:
        return (
            launch,
            {"case_id": case.case_id, "stage": "audit", "message": str(exc)},
            binding,
        )
    return launch, None, binding


async def launch_solar_h1_h2(
    *,
    trigger: str,
    base_workspace: str | Path,
    client: AsyncClient | None = None,
    writer: JsonWriter = _write_json,
    launched_at: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Submit the two fixed prompts concurrently and persist dispatch receipts."""

    if trigger not in {"webui", "cli"}:
        raise ValueError("trigger must be 'webui' or 'cli'")
    base = Path(base_workspace).expanduser().resolve()
    if not base.is_dir():
        raise ValueError(f"base workspace is not a directory: {base}")
    selected_client = client or get_langgraph_async_client(
        url=configured_langgraph_dev_url()
    )
    selected_batch = batch_id or f"repro-{uuid.uuid4()}"
    selected_time = launched_at or _utc_now()

    outcomes = await asyncio.gather(
        *(
            _launch_case(
                client=selected_client,
                case=case,
                batch_id=selected_batch,
                trigger=trigger,
                launched_at=selected_time,
                base_workspace=base,
                writer=writer,
            )
            for case in CASES
        )
    )
    runs = [run for run, _error, _binding in outcomes if run is not None]
    errors = [error for _run, error, _binding in outcomes if error is not None]
    bindings = [binding for _run, _error, binding in outcomes if binding is not None]
    status = (
        "submitted"
        if len(runs) == 2 and not errors
        else ("partial" if runs else "failed")
    )
    response: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "suite_id": SUITE_ID,
        "batch_id": selected_batch,
        "status": status,
        "model": {"name": MODEL_NAME, "provider": MODEL_PROVIDER},
        "runs": runs,
        "errors": errors,
    }

    audit_root = (
        Path(bindings[0].project_shared)
        if bindings
        else base / "projects" / PROJECT_ID / "shared"
    )
    batch_receipt = (
        audit_root / "decisions" / "reproduction_batches" / f"{selected_batch}.json"
    )
    try:
        await asyncio.to_thread(writer, batch_receipt, response)
    except Exception as exc:
        response["errors"].append(
            {"case_id": "batch", "stage": "audit", "message": str(exc)}
        )
        response["status"] = "partial" if runs else "failed"
    return response
