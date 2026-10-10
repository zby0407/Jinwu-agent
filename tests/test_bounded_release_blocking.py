"""An accepted stage is not permission to erase a task-level delivery blocker."""

from pathlib import Path

import pytest

from jw.research_review import ResearchDeliveryBlocked, ResearchReviewStore
from tests.test_research_review_v2 import _write_silso_reproduction_artifacts


def _accepted_store(
    workspace: Path, content: str, *, stage: str = "data"
) -> ResearchReviewStore:
    store = ResearchReviewStore(workspace, "bounded-delivery-task")
    artifact = store.checkpoint_producer_result(
        stage=stage,
        producer="solar-hypothesis" if stage == "hypothesis" else "solar-data",
        content=content,
        phase=f"bounded_{stage}",
    )
    store.submit_verdict(
        mode=stage,
        decision="accept_with_limits",
        issues=[],
        accepted_claims=[artifact["claims"][0]["claim_id"]],
        carry_forward_limits=["The accepted stage has bounded scope."],
    )
    return store


@pytest.mark.parametrize(
    "protocol", ["none", "other_protocol", "silso_cycle_reproduction_v1"]
)
@pytest.mark.parametrize("stage", ["data", "hypothesis"])
def test_explicit_task_blocker_precedes_every_bounded_renderer(
    tmp_path: Path, protocol: str, stage: str
) -> None:
    content = (
        "## 已完成：确定性 Data\n\n回执 status=verified。\n\n"
        "### 1. 文献综述部分：`INPUT_BINDING_BLOCKED`（工具缺失，非资料缺失）\n\n"
        "本会话没有 literature_source_read；全文阅读与报告落盘未完成。"
    )
    store = _accepted_store(tmp_path, content, stage=stage)
    before = store.load_state()
    assert store.bounded_stage_action(stage)["kind"] == "released"

    with pytest.raises(ResearchDeliveryBlocked) as caught:
        store.accepted_bounded_markdown(stage, analysis_protocol=protocol)

    assert caught.value.reason_code == "INPUT_BINDING_BLOCKED"
    assert "工具缺失，非资料缺失" in str(caught.value)
    assert store.load_state() == before
    assert store.load_state()["status"] != "released"


@pytest.mark.parametrize(
    ("declaration", "code"),
    [
        ("INPUT_BINDING_BLOCKED", "INPUT_BINDING_BLOCKED"),
        ("## INPUT_BINDING_BLOCKED: missing input manifest", "INPUT_BINDING_BLOCKED"),
        ("- **status**: `INPUT_BINDING_BLOCKED`", "INPUT_BINDING_BLOCKED"),
        ("任务状态：TASK_BLOCKED（缺少必要工具）", "TASK_BLOCKED"),
        ("delivery_status = REQUIRED_STAGE_INCOMPLETE", "REQUIRED_STAGE_INCOMPLETE"),
        ('{"task_status":"TASK_INCOMPLETE"}', "TASK_INCOMPLETE"),
        ('{"status":"INPUT_BINDING_BLOCKED"}', "INPUT_BINDING_BLOCKED"),
    ],
)
def test_explicit_status_contract_blocks_delivery(
    tmp_path: Path, declaration: str, code: str
) -> None:
    store = _accepted_store(tmp_path, declaration)

    with pytest.raises(ResearchDeliveryBlocked) as caught:
        store.accepted_bounded_markdown("data")

    assert caught.value.reason_code == code


@pytest.mark.parametrize(
    "content",
    [
        "The source mentions INPUT_BINDING_BLOCKED as a historical error.",
        "> ## INPUT_BINDING_BLOCKED: quoted source status",
        "```text\nINPUT_BINDING_BLOCKED\n```\nAccepted result follows.",
        "~~~text\nINPUT_BINDING_BLOCKED\n~~~\nAccepted result follows.",
        "Source example:\n\n    INPUT_BINDING_BLOCKED\nAccepted result follows.",
        "### 示例任务：INPUT_BINDING_BLOCKED",
        "### Previous task: INPUT_BINDING_BLOCKED",
        "如果输入缺失，返回 INPUT_BINDING_BLOCKED。",
        "If inputs are missing: INPUT_BINDING_BLOCKED",
        "状态不是 INPUT_BINDING_BLOCKED；输入已核对。",
        "status: not INPUT_BINDING_BLOCKED",
        "No INPUT_BINDING_BLOCKED was observed.",
        "INPUT_BINDING_BLOCKED（已解除）：输入和工具均已核对。",
        "INPUT_BINDING_BLOCKED: resolved; all required inputs verified.",
        "### 阶段引用：INPUT_BINDING_BLOCKED",
        "| Example status | INPUT_BINDING_BLOCKED |",
        '{"status":"accepted","source_quote":{"status":"INPUT_BINDING_BLOCKED"}}',
        "An optional future experiment is incomplete; this bounded data result is verified.",
        "本数据阶段已完成，后续独立机制验证未完成，不扩大本阶段结论。",
    ],
)
def test_mentions_examples_resolved_status_and_limits_remain_deliverable(
    tmp_path: Path, content: str
) -> None:
    store = _accepted_store(tmp_path, content)

    assert store.accepted_bounded_markdown("data") == content
    assert store.load_state()["status"] == "released"


def test_unaccepted_blocked_artifact_does_not_bypass_review(tmp_path: Path) -> None:
    store = ResearchReviewStore(tmp_path, "unaccepted-task")
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content="INPUT_BINDING_BLOCKED",
        phase="bounded_data",
    )

    assert store.accepted_bounded_markdown("data") is None
    assert store.bounded_stage_action("data")["kind"] == "review"


def test_verified_silso_receipt_cannot_hide_blocker_and_corrected_result_can_release(
    tmp_path: Path,
) -> None:
    task_id = "silso-bounded-delivery-task"
    refs = _write_silso_reproduction_artifacts(tmp_path, task_id)
    store = ResearchReviewStore(tmp_path, task_id)
    blocked = (
        " ".join(refs)
        + "\n\n### 文献综述部分：`INPUT_BINDING_BLOCKED`（全文阅读工具缺失）"
    )
    store.checkpoint_producer_result(
        stage="data", producer="solar-data", content=blocked, phase="bounded_data"
    )
    verdict = store.persist_deterministic_preflight_verdict("data")
    assert verdict is not None
    assert verdict["decision"] == "accept"

    with pytest.raises(ResearchDeliveryBlocked, match="INPUT_BINDING_BLOCKED"):
        store.accepted_bounded_markdown(
            "data", analysis_protocol="silso_cycle_reproduction_v1"
        )
    assert store.load_state()["status"] != "released"

    # A corrected producer artifact is checked on its own content: no sticky
    # blocking flag may prevent a now-valid bounded Data delivery.
    store.checkpoint_producer_result(
        stage="data",
        producer="solar-data",
        content=" ".join(refs) + "\n\nAll requested bounded data work is complete.",
        phase="bounded_data",
    )
    verdict = store.persist_deterministic_preflight_verdict("data")
    assert verdict is not None
    assert verdict["decision"] == "accept"
    rendered = store.accepted_bounded_markdown(
        "data", analysis_protocol="silso_cycle_reproduction_v1"
    )

    assert rendered is not None
    assert "周期 21 > 周期 22 > 周期 23 > 周期 24" in rendered
    assert "INPUT_BINDING_BLOCKED" not in rendered
    assert store.load_state()["status"] == "released"
