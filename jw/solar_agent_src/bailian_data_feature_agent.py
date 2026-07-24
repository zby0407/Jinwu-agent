from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable

from agent_skills import SkillRegistry
from agent_tools import AgentToolRegistry, utc_now
from bailian_llm import create_bailian_tool_completion
from chat_session import ChatSession


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSION_DIR = ROOT / "data" / "processed" / "agent_sessions"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_MODEL_ROUNDS = 8
APPROVAL_TTL = timedelta(minutes=30)


@dataclass
class AgentResponse:
    status: str
    session_id: str
    answer: str | None = None
    activated_skills: list[str] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    pending_action: dict[str, Any] | None = None
    artifacts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "answer": self.answer,
            "activated_skills": self.activated_skills,
            "tool_trace": self.tool_trace,
            "pending_action": self.pending_action,
            "artifacts": self.artifacts,
            "warnings": self.warnings,
            "error": self.error,
        }


def _normalize_tool_call(call: Any) -> dict[str, Any]:
    if isinstance(call, dict):
        function = call.get("function") or {}
        return {
            "id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
            "type": "function",
            "function": {
                "name": str(function.get("name") or ""),
                "arguments": function.get("arguments", "{}"),
            },
        }
    function = getattr(call, "function", None)
    return {
        "id": str(getattr(call, "id", None) or f"call_{uuid.uuid4().hex}"),
        "type": "function",
        "function": {
            "name": str(getattr(function, "name", "")),
            "arguments": getattr(function, "arguments", "{}"),
        },
    }


def normalize_assistant_message(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        content = message.get("content")
        tool_calls = [
            _normalize_tool_call(call) for call in message.get("tool_calls") or []
        ]
    else:
        content = getattr(message, "content", None)
        tool_calls = [
            _normalize_tool_call(call)
            for call in getattr(message, "tool_calls", None) or []
        ]
    result: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result


def _parse_arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = call.get("function", {}).get("arguments", "{}")
    if isinstance(raw, dict):
        value = raw
    else:
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Tool arguments are not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("Tool arguments must be a JSON object")
    return value


def _signature(name: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"name": name, "arguments": arguments}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BailianDataFeatureAgent:
    """Local tool-using agent whose planner is the Bailian model API."""

    def __init__(
        self,
        *,
        completion_fn: Callable[[list[dict[str, Any]], list[dict[str, Any]]], Any]
        | None = None,
        session_dir: Path = DEFAULT_SESSION_DIR,
        session: ChatSession | None = None,
        skill_registry: SkillRegistry | None = None,
        tool_registry: AgentToolRegistry | None = None,
    ) -> None:
        self.completion_fn = completion_fn or create_bailian_tool_completion
        self.session_dir = session_dir
        self.session_override = session
        self.skills = skill_registry or SkillRegistry()
        self.tools = tool_registry or AgentToolRegistry()

    def _open_session(self, session_id: str | None) -> ChatSession:
        if self.session_override is not None:
            if session_id and session_id != self.session_override.session_id:
                raise ValueError("session_id does not match the supplied ChatSession")
            return self.session_override

        actual_id = session_id or f"agent_{uuid.uuid4().hex[:16]}"
        if not SESSION_ID_PATTERN.fullmatch(actual_id):
            raise ValueError(
                "session_id must contain only letters, digits, underscore, or hyphen"
            )
        path = self.session_dir / f"{actual_id}.json"
        session = ChatSession(path)
        if not path.exists():
            session._data["session_id"] = actual_id
            session.save()
        elif session.session_id != actual_id:
            raise ValueError(f"Session file identity mismatch for {actual_id}")
        return session

    def _system_prompt(self, session: ChatSession) -> str:
        catalog = json.dumps(self.skills.catalog(), ensure_ascii=False, indent=2)
        active_sections = []
        for name in session.get_activated_skills():
            try:
                skill = self.skills.load(name)
            except Exception:
                continue
            active_sections.append(f"## Active skill: {name}\n{skill.instructions}")
        active = "\n\n".join(active_sections) or "None"
        dataset = session.get_current_dataset_path() or "None"
        return dedent(
            f"""
            You are the real Solar-Cycle Data Feature Agent. Bailian is the planner: choose from
            the supplied function tools, observe their results, and continue until the user's task
            is resolved. Deterministic Python tools, not the language model, perform all data math.

            Available project skills (load a relevant skill before a multi-step domain workflow):
            {catalog}

            Activated skill instructions:
            {active}

            Current registered dataset: {dataset}

            Mandatory safety rules:
            - Never invent files, columns, observations, metrics, or completed tool results.
            - Never use any next_cycle_* field as a model input.
            - Treat F10.7, WSO, GOES, and hemispheric signals according to their coverage and
              evidence tier; do not present auxiliary proxies as long-history primary evidence.
            - Do not modify observed values yourself. Use tools and report their warnings.
            - Prefer one tool call at a time. Read-only tools may run automatically. Mutating tools
              are paused by the runtime for explicit user approval.
            - Answer in Chinese unless the user requests another language.
            """
        ).strip()

    def _schemas(self) -> list[dict[str, Any]]:
        return [self.skills.tool_schema(), *self.tools.schemas()]

    def capabilities(self) -> dict[str, Any]:
        """Return the discoverable skills and tools without calling Bailian."""
        return {
            "agent": "bailian_data_feature_agent",
            "planner": "bailian_openai_compatible_function_calling",
            "skills": self.skills.catalog(),
            "tools": self.tools.catalog(),
            "built_in_tools": ["load_skill"],
        }

    def _turn_trace(self, session: ChatSession) -> list[dict[str, Any]]:
        start = int(session.get_agent_state("turn_trace_start", 0) or 0)
        return session.get_tool_trace()[start:]

    def _response(
        self,
        session: ChatSession,
        status: str,
        *,
        answer: str | None = None,
        pending: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
        warnings: list[str] | None = None,
    ) -> AgentResponse:
        trace = self._turn_trace(session)
        artifacts: list[str] = []
        collected_warnings = list(warnings or [])
        for item in trace:
            result = item.get("result") or {}
            artifacts.extend(str(path) for path in result.get("artifacts", []) if path)
            collected_warnings.extend(
                str(value) for value in result.get("warnings", []) if value
            )
        return AgentResponse(
            status=status,
            session_id=session.session_id,
            answer=answer,
            activated_skills=session.get_activated_skills(),
            tool_trace=trace,
            pending_action=pending,
            artifacts=list(dict.fromkeys(artifacts)),
            warnings=list(dict.fromkeys(collected_warnings)),
            error=error,
        )

    def _append_tool_result(
        self,
        session: ChatSession,
        call: dict[str, Any],
        name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        *,
        approved: bool,
    ) -> None:
        messages = session.get_agent_messages()
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": json.dumps(result, ensure_ascii=False),
            }
        )
        session.set_agent_messages(messages)
        session.append_tool_trace(
            {
                "tool_call_id": call["id"],
                "tool": name,
                "arguments": arguments,
                "approved": approved,
                "executed_utc": utc_now(),
                "result": result,
            }
        )

    def _execute_call(
        self,
        session: ChatSession,
        call: dict[str, Any],
        *,
        approved: bool,
    ) -> AgentResponse | None:
        name = call.get("function", {}).get("name", "")
        try:
            arguments = _parse_arguments(call)
        except ValueError as exc:
            arguments = {}
            result = {
                "status": "failed",
                "tool": name or "unknown",
                "summary": {},
                "artifacts": [],
                "warnings": [],
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
            self._append_tool_result(
                session, call, name or "unknown", arguments, result, approved=approved
            )
            return None

        signature = _signature(name, arguments)
        signatures = list(session.get_agent_state("turn_signatures", []))
        if signature in signatures:
            return self._response(
                session,
                "failed",
                error={
                    "type": "RepeatedToolCall",
                    "message": f"Repeated tool call blocked: {name}",
                },
            )
        signatures.append(signature)
        session.set_agent_state("turn_signatures", signatures)

        if name == "load_skill":
            try:
                if set(arguments) != {"name"}:
                    raise ValueError("load_skill accepts only the name argument")
                skill = self.skills.load(str(arguments["name"]))
                session.activate_skill(skill.name)
                result = skill.tool_result()
            except Exception as exc:
                result = {
                    "status": "failed",
                    "tool": "load_skill",
                    "summary": {},
                    "artifacts": [],
                    "warnings": [],
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                }
        else:
            result = self.tools.execute(name, arguments, session)
        self._append_tool_result(
            session, call, name, arguments, result, approved=approved
        )
        return None

    def _pending_for(
        self,
        session: ChatSession,
        call: dict[str, Any],
        remaining_calls: list[dict[str, Any]],
    ) -> AgentResponse:
        name = call.get("function", {}).get("name", "")
        try:
            arguments = _parse_arguments(call)
        except ValueError as exc:
            return self._response(
                session,
                "failed",
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        approval_id = f"approval_{uuid.uuid4().hex}"
        created = datetime.now(timezone.utc)
        pending = {
            "approval_id": approval_id,
            "created_utc": created.isoformat(),
            "expires_utc": (created + APPROVAL_TTL).isoformat(),
            "call": call,
            "remaining_calls": remaining_calls,
            "preview": self.tools.preview(name, arguments, session),
        }
        session.set_pending_action(pending)
        public_pending = {
            "approval_id": approval_id,
            "expires_utc": pending["expires_utc"],
            **pending["preview"],
        }
        return self._response(session, "approval_required", pending=public_pending)

    def _process_calls(
        self,
        session: ChatSession,
        calls: list[dict[str, Any]],
        *,
        first_is_approved: bool = False,
    ) -> AgentResponse | None:
        for index, call in enumerate(calls):
            name = call.get("function", {}).get("name", "")
            approved = first_is_approved and index == 0
            if self.tools.is_mutating(name) and not approved:
                return self._pending_for(session, call, calls[index + 1 :])
            response = self._execute_call(session, call, approved=approved)
            if response is not None:
                return response
        return None

    def _continue(self, session: ChatSession) -> AgentResponse:
        while int(session.get_agent_state("turn_rounds", 0) or 0) < MAX_MODEL_ROUNDS:
            messages = [{"role": "system", "content": self._system_prompt(session)}]
            messages.extend(session.get_agent_messages())
            try:
                raw = self.completion_fn(messages, self._schemas())
            except Exception as exc:
                return self._response(
                    session,
                    "failed",
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            assistant = normalize_assistant_message(raw)
            stored = session.get_agent_messages()
            stored.append(assistant)
            session.set_agent_messages(stored)
            rounds = int(session.get_agent_state("turn_rounds", 0) or 0) + 1
            session.set_agent_state("turn_rounds", rounds)

            calls = list(assistant.get("tool_calls") or [])
            if calls:
                response = self._process_calls(session, calls)
                if response is not None:
                    return response
                continue
            content = assistant.get("content")
            if content and str(content).strip():
                session.clear_pending_action()
                return self._response(session, "completed", answer=str(content).strip())
            return self._response(
                session,
                "failed",
                error={
                    "type": "EmptyModelResponse",
                    "message": "Bailian returned neither content nor tool calls",
                },
            )
        return self._response(
            session,
            "failed",
            error={
                "type": "MaxRoundsExceeded",
                "message": f"Agent exceeded {MAX_MODEL_ROUNDS} model rounds",
            },
        )

    def _resume_approval(self, session: ChatSession, approval_id: str) -> AgentResponse:
        pending = session.get_pending_action()
        if not pending or pending.get("approval_id") != approval_id:
            return self._response(
                session,
                "failed",
                error={
                    "type": "InvalidApproval",
                    "message": "Approval ID is unknown, consumed, or does not match this session",
                },
            )
        try:
            expires = datetime.fromisoformat(str(pending["expires_utc"]))
        except (KeyError, ValueError) as exc:
            session.clear_pending_action()
            return self._response(
                session,
                "failed",
                error={
                    "type": type(exc).__name__,
                    "message": "Pending approval metadata is invalid",
                },
            )
        if datetime.now(timezone.utc) >= expires:
            session.clear_pending_action()
            return self._response(
                session,
                "failed",
                error={"type": "ApprovalExpired", "message": "Approval ID has expired"},
            )
        call = pending["call"]
        remaining = list(pending.get("remaining_calls") or [])
        session.clear_pending_action()  # consume before executing to prevent replay
        response = self._process_calls(
            session, [call, *remaining], first_is_approved=True
        )
        return response or self._continue(session)

    def reject(self, session_id: str, approval_id: str) -> AgentResponse:
        session = self._open_session(session_id)
        pending = session.get_pending_action()
        if not pending or pending.get("approval_id") != approval_id:
            return self._response(
                session,
                "failed",
                error={
                    "type": "InvalidApproval",
                    "message": "Approval ID is unknown or does not match this session",
                },
            )
        calls = [pending["call"], *list(pending.get("remaining_calls") or [])]
        session.clear_pending_action()
        for call in calls:
            name = call.get("function", {}).get("name", "")
            try:
                arguments = _parse_arguments(call)
            except ValueError:
                arguments = {}
            result = {
                "status": "failed",
                "tool": name,
                "summary": {},
                "artifacts": [],
                "warnings": ["User rejected this action."],
                "error": {
                    "type": "ApprovalRejected",
                    "message": "User rejected this action",
                },
            }
            self._append_tool_result(
                session, call, name, arguments, result, approved=False
            )
        return self._continue(session)

    def run(
        self,
        question: str,
        session_id: str | None = None,
        approval_id: str | None = None,
    ) -> AgentResponse:
        session = self._open_session(session_id)
        if approval_id:
            if question and question.strip():
                return self._response(
                    session,
                    "failed",
                    error={
                        "type": "InvalidRequest",
                        "message": "Do not provide a new question while approving an action",
                    },
                )
            return self._resume_approval(session, approval_id)
        if session.get_pending_action():
            pending = session.get_pending_action() or {}
            public = {
                "approval_id": pending.get("approval_id"),
                "expires_utc": pending.get("expires_utc"),
                **pending.get("preview", {}),
            }
            return self._response(session, "approval_required", pending=public)
        if not question or not question.strip():
            return self._response(
                session,
                "failed",
                error={
                    "type": "InvalidRequest",
                    "message": "question must not be empty",
                },
            )

        session.set_agent_state("turn_trace_start", len(session.get_tool_trace()))
        session.set_agent_state("turn_rounds", 0)
        session.set_agent_state("turn_signatures", [])
        explicit = self.skills.explicit_skill(question)
        if explicit:
            session.activate_skill(explicit.name)
        messages = session.get_agent_messages()
        messages.append({"role": "user", "content": question.strip()})
        session.set_agent_messages(messages)
        return self._continue(session)
