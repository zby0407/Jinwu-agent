"""Regression tests for transient-vs-scientific specialist failure accounting."""

from __future__ import annotations

import hashlib

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from jw.middleware.research_review_orchestration import (
    _prior_task_failure_fingerprints,
    _prior_transient_task_failure_count,
    _TRANSIENT_TASK_FAILURE_LIMIT,
)


def _task_call(call_id: str, subagent_type: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"subagent_type": subagent_type, "description": "run"},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def _capsule(call_id: str, summary: str) -> ToolMessage:
    fingerprint = hashlib.sha256(f"task\0{summary}".encode()).hexdigest()
    return ToolMessage(
        content=(
            "[TOOL ERROR CAPSULE]\n"
            f"fingerprint={fingerprint}\n"
            "tool=task\n"
            f"error={summary}"
        ),
        tool_call_id=call_id,
        name="task",
        status="error",
    )


def _state(*messages) -> dict:
    return {"messages": list(messages)}


def test_transient_connection_failures_do_not_count_as_scientific_failures():
    state = _state(
        HumanMessage(content="plan"),
        _task_call("c1", "solar-planner"),
        _capsule("c1", "APIConnectionError: Connection error."),
        _task_call("c2", "solar-planner"),
        _capsule("c2", "httpx.RemoteProtocolError: Server disconnected"),
    )
    # Transient drops must not consume the scientific two-failure budget.
    assert _prior_task_failure_fingerprints(state, "solar-planner") == ()
    assert _prior_transient_task_failure_count(state, "solar-planner") == 2


def test_real_preflight_failures_still_count_toward_blocking():
    state = _state(
        HumanMessage(content="plan"),
        _task_call("c1", "solar-planner"),
        _capsule("c1", "RuntimeError: planner draft is incomplete; missing sections"),
        _task_call("c2", "solar-planner"),
        _capsule("c2", "ValueError: one revision patch may change at most 8 sections"),
    )
    fingerprints = _prior_task_failure_fingerprints(state, "solar-planner")
    assert len(fingerprints) == 2
    assert _prior_transient_task_failure_count(state, "solar-planner") == 0


def test_transient_outage_is_capped_below_scientific_stop():
    messages = [HumanMessage(content="plan")]
    for index in range(_TRANSIENT_TASK_FAILURE_LIMIT):
        call_id = f"c{index}"
        messages.append(_task_call(call_id, "solar-planner"))
        messages.append(_capsule(call_id, "APIConnectionError: Connection error."))
    state = _state(*messages)
    assert (
        _prior_transient_task_failure_count(state, "solar-planner")
        >= _TRANSIENT_TASK_FAILURE_LIMIT
    )
    # Still zero scientific failures, so the cap is what stops a dead provider.
    assert _prior_task_failure_fingerprints(state, "solar-planner") == ()
