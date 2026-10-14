"""Bounded original solar-data reasoning with real typed tool submissions."""

import hashlib
import json
import time
from pathlib import Path
from typing import Literal

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from .assets import sha
from .engine import PACKAGE, load
from .workflow import read

MAX_INLINE_BYTES = 192 * 1024
MAX_MODEL_CALLS = 4
MAX_SECONDS = 600

LIVE_REVIEW_DECISION_CONTRACT = {
    "scope": "current_live_SHARP_input_only_not_future_skill_or_truth",
    "question": "Whether this exact input is usable under the registered restricted experimental cohort, not whether the model or data are harmless or operationally validated.",
    "registered_limits_not_automatic_new_blockers": [
        "Complete primary QUALITY exactly 1024 is the preregistered warning cohort. It remains a suboptimal-condition warning, never a harmlessness certificate; do not clear bits.",
        "QUALLEV1 0x40000000 is bit30 in the independent Level1 namespace. The archived quallev1.h in the source policy identifies Q_NRT; this is not primary QUALITY or bit31. Other/unreviewed secondary flags still need review.",
        "Central SHARP scalar inputs versus full-Sun GOES labels, no pixels/FITS, DATE not first-publication proof, and archived/deployed version and licensing uncertainty remain declared limits of this task.",
        "scientific_status=not_reviewed, formal ResearchReviewStore chain=false and operational validation=false stay unchanged even if Data accepts the restricted input.",
    ],
    "blocking_examples": [
        "Source/hash binding or schema/version mismatch; missing source-policy basis; unsupported new quality flags; incomplete central patches or geometry; invalid/nonfinite features; future-time/as-of violation or input older than three hours.",
        "A specific material gap in the policy or current snapshot that makes the restricted task unsupported, with the relevant field or source and explanation. Deterministic eligibility is evidence, not a preset independent acceptance.",
    ],
    "decision_semantics": {
        "accept_with_limits": "Independent input acceptance with every warning retained; no model skill, safety, formal science acceptance or future truth claim.",
        "revise_block_reject": "Valid decisions; explain the concrete current-input defect or unresolved policy basis in issues. The mere existence of already declared limits does not itself establish a new defect. Never downgrade a genuine concern just to obtain a forecast.",
    },
}


def quality_policy_input(contract):
    """Read the actual source-policy content, which the bounded role cannot open."""
    binding = contract["quality_decision_file"]
    path = Path(binding["path"])
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("Quality policy must be a regular source file")
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    source_proof = contract["live_source"]["source_policy_proofs"][
        "quality_source_decision"
    ]
    if (
        digest != binding["sha256"]
        or digest != contract["quality_policy_sha256"]
        or digest != source_proof["sha256"]
        or Path(source_proof["path"]).resolve() != path.resolve()
    ):
        raise ValueError("Registered quality policy content/hash bindings differ")
    return {
        "sha256": digest,
        "bytes": len(body),
        "document": json.loads(body.decode("utf-8-sig")),
        "read_scope": "Complete archived source-policy document, including authorship and limitations. Historical row counts/old-window statements in it are source-review context, not current snapshot facts or a new JW acceptance.",
    }


class LiveFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_nominal_tai: str
    central_patch_count: int = Field(ge=1)
    full_PRIMARY_QUALITY_word: Literal[1024]
    secondary_bit31_present_in_selected_snapshot: Literal[False]
    future_truth_read: Literal[False]


class DataAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["current_live_SHARP_input_only_not_future_skill_or_truth"]
    checks: list[str] = Field(min_length=1)
    issues: list[str]
    limitations: list[str] = Field(min_length=1)
    facts: LiveFacts


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def review_live(live_dir: Path, contract: Path, output: Path, model) -> Path:
    """No accepted decision is preset. A missing/invalid submission stops issuance."""
    output.mkdir(parents=True, exist_ok=False)
    manifest = live_dir / "source_manifest.json"
    core = load("available24_model")
    policy, _, before, frame, _ = core.checked_live(live_dir, contract)
    core.validate_physical(frame, policy, live=True)
    bundle = {"contract": read(contract)}
    bundle["registered_quality_policy"] = quality_policy_input(bundle["contract"])
    bundle["review_decision_contract"] = LIVE_REVIEW_DECISION_CONTRACT
    for name in (
        "source_manifest.json",
        "live_qualification.json",
        "source_schema_validation.json",
        "source_requests.json",
    ):
        p = live_dir / name
        if p.is_file():
            bundle[name] = read(p)
    audit_path = live_dir / "row_audit.json"
    audit = read(audit_path)
    groups = audit.get("groups", [])
    selected = [g for g in groups if g.get("selected_latest_complete_qualified_group")]
    if groups and len(selected) != 1:
        raise ValueError("Exactly one selected current snapshot must enter Data review")
    bundle["registered_row_audit_projection"] = {
        "original_sha256": sha(audit_path),
        "original_bytes": audit_path.stat().st_size,
        "scope": "complete selected snapshot and all candidate-group selection summaries",
        "summary": {k: v for k, v in audit.items() if k != "groups"},
        "selected_snapshot_complete": selected,
        "all_group_selection_summaries": [
            {k: v for k, v in g.items() if k != "rows"} for g in groups
        ],
        "unselected_per_patch_details_read_by_model": False,
        "unselected_details_archived_not_claimed_read": "row_audit.json",
    }
    bundle["binding"] = {
        "manifest_sha256": sha(manifest),
        "contract_sha256": sha(contract),
    }
    bundle["live_physical_csv"] = (live_dir / "live_physical.csv").read_text(
        encoding="utf-8-sig"
    )
    bundle["review_scope"] = "current_live_SHARP_input_only_not_future_skill_or_truth"
    bundle["limitations"] = [
        "QUALITY=1024 is a warning, not harmless",
        "whole-Sun targets and central patches differ",
        "historical DATE does not prove first availability",
        "source license/version limitations remain",
        "no future truth was read",
    ]
    text = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_INLINE_BYTES:
        raise ValueError("Data bundle exceeds bounded complete-message allowance")
    write(output / "complete_bundle.json", bundle)
    opened = False
    provider_read = False
    submitted = False
    current_call = ""
    witness = []
    review_path = output / "data_review.json"

    @tool
    def available24_read_live_bundle() -> str:
        """Read the complete current live contract, acquisition checks and exact hashes once."""
        nonlocal opened
        if opened:
            raise ValueError("Live bundle may be opened only once")
        opened = True
        return text

    @tool
    def available24_submit_data_review(
        decision: str,
        assessment: DataAssessment,
        manifest_sha256: str,
        contract_sha256: str,
    ) -> str:
        """Submit an independent current-input decision with exact hashes and checks/issues/limitations. accept_with_limits retains registered warnings and does not certify harmlessness or skill. revise/block/reject remain valid: describe a specific defect or unresolved policy basis, rather than merely restating the declared restrictions. Never assert future truth."""
        nonlocal submitted
        assessment = assessment.model_dump()
        if submitted or not provider_read:
            raise ValueError(
                "Submission requires the complete bundle in actual model input, once"
            )
        if (
            manifest_sha256 != bundle["binding"]["manifest_sha256"]
            or contract_sha256 != bundle["binding"]["contract_sha256"]
        ):
            raise ValueError("Review hashes differ from this live acquisition")
        if decision not in {"accept_with_limits", "revise", "block", "reject"}:
            raise ValueError("Warnings prohibit unrestricted acceptance")
        if assessment.get("scope") != bundle["review_scope"] or not all(
            isinstance(assessment.get(k), list)
            for k in ("checks", "issues", "limitations")
        ):
            raise ValueError("Review scope and structured assessments required")
        if not assessment["checks"] or not assessment["limitations"]:
            raise ValueError("Data assessment must retain checks and limitations")
        expected = {
            "selected_nominal_tai": audit.get("selected_nominal_tai"),
            "central_patch_count": audit.get("central_patch_count"),
            "full_PRIMARY_QUALITY_word": 1024,
            "secondary_bit31_present_in_selected_snapshot": False,
            "future_truth_read": False,
        }
        if assessment["facts"] != expected:
            raise ValueError(
                "Native Data facts differ from the current qualified snapshot"
            )
        core.verify_unchanged(before)
        write(
            review_path,
            {"decision": decision, "assessment": assessment, **bundle["binding"]},
        )
        witness.append(
            {
                "tool": "available24_submit_data_review",
                "call_id": current_call,
                "review_sha256": sha(review_path),
            }
        )
        submitted = True
        return json.dumps(
            {
                "status": "submitted",
                "decision": decision,
                "review_sha256": sha(review_path),
            }
        )

    tools = [available24_read_live_bundle, available24_submit_data_review]
    by_name = {t.name: t for t in tools}
    role_path = PACKAGE.parent / "subagents/solar/solar_data.yaml"
    prompt = yaml.safe_load(role_path.read_text(encoding="utf-8"))["solar-data"][
        "system_prompt"
    ]
    prompt += "\nThis is a bounded live flare Data review, not RESEARCH_PRODUCER_V2. Use only the two registered live tools. First read the complete live bundle; next independently submit the decision bound to its exact hashes. The scope must equal review_scope. Keep QUALITY/source/spatial warnings. Do not fit, issue, read future outcomes, draw figures or discover other data. Return a short Chinese final reply after submission. A revise/block/reject is valid and will prevent issuance."
    prompt += "\nFollow the exact assessment schema: checks (not checks_passed), issues, limitations, facts. Facts must match the current projection summary; never infer counts from a prior example. 0x40000000 is bit30; bit31 is 0x80000000. Do not claim all archived unselected per-patch rows were read. Keep the projection's stated review scope."
    prompt += "\nApply the bundle's review_decision_contract and read the complete registered_quality_policy. Source-policy authorship is preserved: operator source-condition review is not your native Data acceptance. Distinguish current snapshot facts from historical examples in that policy. This live task asks whether the registered restricted input is usable, not for a new model-performance/Evidence review or unrestricted scientific acceptance. Preserving a QUALITY warning is compatible with accept_with_limits; it must never be called harmless. For revise/block/reject, explain the concrete current-input defect or unresolved source-policy basis in issues, referencing the supplied field/source. A material source-policy concern may still block; do not preset or force acceptance."
    messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content="审阅本次最新SHARP输入是否可用于受限24小时实验预报。先读取完整工具结果，再提交独立Data裁决；不得预置接受。"
        ),
    ]
    bound = model.bind_tools(tools, parallel_tool_calls=False)
    if getattr(model, "extra_body", None):
        # Passback patches can close over the original model instance. Invocation
        # kwargs still reach its payload builder and preserve the actual override.
        bound = bound.bind(extra_body=model.extra_body)
    started = time.monotonic()
    session = {
        "status": "running",
        "role": "solar-data",
        "actual_original_native_JW_role": True,
        "execution_framework": "JW bounded LangChain native tool loop",
        "role_prompt_sha256": sha(role_path),
        "effective_system_prompt_sha256": hashlib.sha256(
            prompt.encode("utf-8")
        ).hexdigest(),
        "source_quality_policy_sha256": bundle["registered_quality_policy"]["sha256"],
        "model_call_limit": MAX_MODEL_CALLS,
        "tool_call_limit": 2,
        "active_seconds_limit": MAX_SECONDS,
        "model_calls": 0,
        "tool_calls": 0,
    }
    final = False
    try:
        for _ in range(MAX_MODEL_CALLS):
            if time.monotonic() - started > MAX_SECONDS:
                raise TimeoutError("Native live review activity allowance exhausted")
            complete = [
                m
                for m in messages
                if isinstance(m, ToolMessage)
                and m.name == available24_read_live_bundle.name
            ]
            if opened:
                if (
                    len(complete) != 1
                    or complete[0].content != text
                    or complete[0].status != "success"
                ):
                    raise ValueError(
                        "Complete typed live tool message absent from provider input"
                    )
                witness.append(
                    {
                        "tool": complete[0].name,
                        "call_id": complete[0].tool_call_id,
                        "bytes": len(text.encode("utf-8")),
                        "content_sha256": hashlib.sha256(
                            text.encode("utf-8")
                        ).hexdigest(),
                        "complete_exact_text_in_actual_provider_input": True,
                    }
                )
                provider_read = True
            session["model_calls"] += 1
            response = bound.invoke(messages)
            if not isinstance(response, AIMessage):
                raise ValueError("Native reviewer returned no AI message")
            messages.append(response)
            if not response.tool_calls:
                if not submitted:
                    raise ValueError("Native Data ended before a genuine submission")
                final = True
                break
            if submitted or len(response.tool_calls) != 1 or session["tool_calls"] >= 2:
                raise ValueError("No parallel calls, retries or tools after submission")
            call = response.tool_calls[0]
            current_call = call["id"]
            if not current_call or call["name"] not in by_name:
                raise ValueError("Unknown native review tool or missing call ID")
            session["tool_calls"] += 1
            reply = by_name[call["name"]].invoke(call)
            messages.append(reply)
        if not final or not submitted:
            raise ValueError("Native Data did not complete within registered limits")
        session["status"] = "completed"
        core.verify_unchanged(before)
    except BaseException:
        session["status"] = "failed"
        raise
    finally:
        session["active_seconds"] = time.monotonic() - started
        write(output / "session.json", session)
        write(output / "messages.json", [m.model_dump(mode="json") for m in messages])
        (output / "tool_witness.jsonl").write_text(
            "".join(json.dumps(w, ensure_ascii=False) + "\n" for w in witness),
            encoding="utf-8",
        )
    submission = next(
        w for w in witness if w["tool"] == "available24_submit_data_review"
    )
    write(
        output / "native_review_binding.json",
        {
            "role": "data",
            **bundle["binding"],
            "review_path": str(review_path.resolve()),
            "review_sha256": sha(review_path),
            "session_path": str((output / "session.json").resolve()),
            "session_sha256": sha(output / "session.json"),
            "tool_witness_path": str((output / "tool_witness.jsonl").resolve()),
            "tool_witness_sha256": sha(output / "tool_witness.jsonl"),
            "actual_tool_call_id": submission["call_id"],
        },
    )
    return review_path
