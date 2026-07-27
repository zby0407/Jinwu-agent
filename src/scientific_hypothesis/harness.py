"""科学假设 Agent 1.0 的确定性编排：简报、证据绑定、检查、渲染与保存。"""

from __future__ import annotations

import json
import shutil
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_layout import PROJECT_ROOT, contract_runs_root

from . import ranking as ranking_mod
from .contracts import (
    HARD_NUMERIC_CUTOFF,
    NOVELTY_CLAIM,
    OUTCOME_VERSION,
    PORTFOLIO_VERSION,
    PRECISE_PROBABILITY,
    REQUEST_VERSION,
    RESPONSE_VERSION,
    ContractError,
    canonical_json_sha256,
    validate_evidence_bind,
    validate_hypothesis_portfolio,
    validate_hypothesis_request,
    validate_hypothesis_response,
)

RUNS_ROOT = contract_runs_root("hypothesis")

NOVELTY_STATUSES = {"unverified", "likely_novel", "known", "not_assessed"}
NOVELTY_CLAIMED_STATUSES = {"likely_novel", "known"}
# 检索核对证据必须来自文献类材料。
_NOVELTY_EVIDENCE_KINDS = {"literature", "upstream"}


def build_wiki_evidence_excerpt(
    entry: dict[str, Any],
    *,
    read_receipt: dict[str, Any] | None = None,
) -> str:
    """Build the bounded, persisted receipt for one canonical Wiki entry.

    Runtime hypothesis state must retain more than an opaque ``kb_*`` id: the
    exact version, scope, confidence, and source boundary used during candidate
    formation need to survive after the knowledge store changes.  The full
    content remains available through ``kb_read``; this receipt is deliberately
    compact enough for the evidence-register contract.
    """

    def compact(value: object, limit: int) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return encoded if len(encoded) <= limit else encoded[: limit - 3] + "..."

    provenance = entry.get("provenance", {})
    payload = {
        "id": entry.get("id"),
        "type": entry.get("type"),
        "title": entry.get("title"),
        "status": entry.get("status"),
        "version": entry.get("version"),
        "confidence": entry.get("confidence"),
        "valid_range": entry.get("valid_range"),
        "source_type": entry.get("source_type"),
        "source_ref": entry.get("source_ref"),
        "provenance_sha256": canonical_json_sha256(provenance),
        "provenance_summary": compact(provenance, 400),
        "content_summary": compact(entry.get("content", {}), 700),
    }
    if read_receipt is not None:
        payload["kb_read_receipt"] = {
            "log_id": read_receipt.get("id"),
            "run_id": read_receipt.get("run_id"),
            "agent": read_receipt.get("agent"),
            "purpose": read_receipt.get("purpose"),
            "ts": read_receipt.get("ts"),
        }
    receipt = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    # Entry metadata may have unusually long free-text source fields. Keep the
    # immutable identity/version/hash fields and bound only display strings so
    # the evidence-register excerpt contract remains valid.
    for key in (
        "source_ref",
        "valid_range",
        "title",
        "content_summary",
        "provenance_summary",
    ):
        if len(receipt) <= 2_000:
            break
        value = str(payload.get(key) or "")
        overflow = len(receipt) - 1_950
        payload[key] = value[: max(16, len(value) - overflow)] + "..."
        receipt = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if len(receipt) > 2_000:
        raise ContractError("Wiki 读取回执超过证据登记上限，无法安全持久化")
    return receipt


def validate_evidence_provenance(
    request: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    """Require every verified evidence row to resolve inside this request.

    Observation memory is useful for discovery, but it is not automatically a
    verified input to a new scientific run.  A normal evidence row therefore
    has to point to a declared ``upstream_material`` (and its excerpt must occur
    in that material), or to the exact bound user question.  Canonical ``kb_*``
    receipts are the one integrated exception; they are mechanism grounding,
    forced to the limiting role, and are created by the dedicated Wiki-binding
    tool after a successful canonical read.
    """

    material_id = evidence["material_id"]
    kind = evidence["evidence_kind"]
    role = evidence["role"]
    excerpt = " ".join(str(evidence["excerpt"]).split())

    # Discovery-only memories may remain visible as explicit gaps, but they
    # must not acquire verified status merely by entering the register.
    if role == "gap" and not evidence["verified_support"]:
        return

    if material_id.startswith("kb_"):
        if kind != "literature" or role != "limits":
            raise ContractError(
                f"Wiki 材料 {material_id} 只能作为已核验的机制/范围约束登记；"
                "请使用 evidence_kind=literature、role=limits"
            )
        if '"status":"canonical"' not in evidence["excerpt"]:
            raise ContractError(
                f"Wiki 材料 {material_id} 缺少 canonical 读取回执；"
                "请使用 scientific_hypothesis_bind_wiki_evidence"
            )
        return

    if material_id == "user_request":
        if kind != "user":
            raise ContractError("user_request 证据必须使用 evidence_kind=user")
        question = " ".join(request["research_question"].split())
        if excerpt not in question:
            raise ContractError("user_request 证据摘录必须逐字来自当前绑定的研究问题")
        return

    materials = {
        material["id"]: material for material in request.get("upstream_materials", [])
    }
    material = materials.get(material_id)
    if material is None:
        raise ContractError(
            f"证据材料 {material_id} 未在本轮 upstream_materials 中声明；"
            "历史记忆、日志或先前运行结论只能先作为 gap，不能直接标为已核验证据"
        )

    allowed_kinds = {
        "experiment_result": {"experiment"},
        "literature_note": {"literature"},
        "research_plan": {"upstream"},
        "data_feature": {"upstream"},
        "user_material": {"user", "upstream"},
    }[material["material_kind"]]
    if kind not in allowed_kinds:
        raise ContractError(
            f"证据 {evidence['evidence_id']} 的 evidence_kind={kind} 与材料 "
            f"{material_id} 的 material_kind={material['material_kind']} 不一致"
        )
    corpus = " ".join(json.dumps(material, ensure_ascii=False, sort_keys=True).split())
    if excerpt not in corpus:
        raise ContractError(
            f"证据 {evidence['evidence_id']} 的摘录无法在材料 {material_id} 中定位；"
            "请绑定材料中的原文，不要用记忆摘要替代"
        )


def build_natural_hypothesis_request(research_question: str) -> dict[str, Any]:
    """把普通自然语言输入构造成标准请求。"""

    if not isinstance(research_question, str):
        raise ContractError("research_question 必须是字符串")
    normalized = research_question.strip()
    request = {
        "schema_version": REQUEST_VERSION,
        "task_name": f"hypothesis_{canonical_json_sha256({'research_question': normalized})[:12]}",
        "research_question": normalized,
        "upstream_materials": [],
        "prior_hypotheses": [],
        "max_candidates": 6,
    }
    return validate_hypothesis_request(request)


def _compact_response_contract() -> dict[str, Any]:
    """返回完整的字段类型指引，不嵌入 JSON Schema 机制。"""

    candidate_shape = {
        "id": "id",
        "statement": "string（精确、可被证据削弱的假设主张）",
        "applicability": "string（适用范围与边界）",
        "mechanism": {
            "summary": "string（可能机制，不等于可观测预测）",
            "physical_basis": "string（机制依据；没有依据时如实写未知）",
            "required_premises": ["string（必要前提，至少一条）"],
        },
        "assumptions": ["string（关键假设，至少一条）"],
        "predictions": [
            {
                "id": "id",
                "statement": "string（与主张不同的可观测预测）",
                "observable": "string（可观测量与获取方式）",
                "distinguishes_from": ["候选 id 或已有假设 id，至少一个"],
                "would_weaken_if": "string（什么观测结果会削弱本候选）",
            }
        ],
        "supporting_evidence": [
            {"evidence_id": "已绑定证据 id", "relation_note": "string（如何支持）"}
        ],
        "opposing_evidence": [
            {"evidence_id": "已绑定证据 id", "relation_note": "string（如何反对）"}
        ],
        "evidence_gaps": ["string（诚实标注的证据缺口）"],
        "alternative_explanations": ["string（可区分的替代解释，至少一条）"],
        "confounders": ["string（潜在混杂因素）"],
        "falsification_conditions": ["string（可证伪条件，至少一条）"],
        "next_test": {
            "objective": "string（最有区分力的下一项检验要回答什么）",
            "discriminating_power": "string（它如何区分候选，而不是泛泛增加数据）",
            "expected_signals": ["string（各候选下的预期信号差异）"],
            "candidate_ids_distinguished": ["候选 id，至少一个；多个时必须包含本候选"],
        },
        "confidence": {
            "level": "high、medium 或 low",
            "basis": "string（定性理由；不得写精确百分比）",
        },
        "evidence_update": "更新已有假设时填写 {summary, reason}；否则填 null",
        "prior_version_id": "更新已有假设时填其 id；否则填 null",
    }
    return {
        "title": "Scientific Hypothesis Response 1.0",
        "schema_version": RESPONSE_VERSION,
        "response_shapes": {
            "hypotheses_ready": {
                "schema_version": RESPONSE_VERSION,
                "task_name": "逐字复制请求 task_name",
                "research_question": "逐字复制请求 research_question",
                "response_kind": "hypotheses_ready",
                "candidates": [candidate_shape],
                "pairwise_distinctions": [
                    {
                        "left_id": "候选 id",
                        "right_id": "候选 id",
                        "distinction": "string（机制或预测上的本质区别，不是措辞差异）",
                    }
                ],
                "portfolio_notes": "string 或 null（组合层面的诚实说明）",
            },
            "clarification_needed": {
                "schema_version": RESPONSE_VERSION,
                "task_name": "逐字复制请求 task_name",
                "research_question": "逐字复制请求 research_question",
                "response_kind": "clarification_needed",
                "questions": [
                    {
                        "id": "id",
                        "question": "string",
                        "why_it_matters": "string（该答案如何实质改变假设方向）",
                        "expected_answer": "string",
                    }
                ],
            },
            "hypothesis_blocked": {
                "schema_version": RESPONSE_VERSION,
                "task_name": "逐字复制请求 task_name",
                "research_question": "逐字复制请求 research_question",
                "response_kind": "hypothesis_blocked",
                "blockers": [
                    {
                        "id": "id",
                        "code": "allowed value",
                        "reason": "string",
                        "recoverable": "boolean",
                        "resolution": "string",
                    }
                ],
            },
        },
        "array_rule": (
            "所有以方括号展示的字段始终是 JSON 数组，即使只有零或一项；"
            "不要用字符串替代数组。"
        ),
        "allowed_values": {
            "confidence_level": ["high", "medium", "low"],
            "blocker_code": [
                "unsupported_scope",
                "missing_indispensable_evidence",
                "unresearchable_formulation",
                "safety_boundary",
            ],
        },
        "integrity_rules": [
            "只使用所选响应类型列出的字段。",
            "task_name 与 research_question 必须逐字复制已绑定请求。",
            "所有 id 以字母开头；所有证据引用必须指向已绑定且核验通过的 evidence_id。",
            "候选数量由问题复杂度决定；禁止同义改写；多个候选时每个候选必须出现在 pairwise_distinctions 中。",
            "科学表述保持简洁、面向读者；不要在正文中出现 schema 名、枚举名、工具调用或保存机制等工程语言。",
        ],
    }


def build_hypothesis_brief(request_payload: dict[str, Any]) -> dict[str, Any]:
    request = validate_hypothesis_request(request_payload)
    return {
        "schema_version": "scientific-hypothesis-brief-v1",
        "request_sha256": canonical_json_sha256(request),
        "request": request,
        "hypothesis_contract": {
            "response_kinds": [
                "hypotheses_ready",
                "clarification_needed",
                "hypothesis_blocked",
            ],
            "candidate_fields": [
                "statement",
                "applicability",
                "mechanism",
                "assumptions",
                "predictions",
                "supporting_evidence",
                "opposing_evidence",
                "evidence_gaps",
                "alternative_explanations",
                "confounders",
                "falsification_conditions",
                "next_test",
                "confidence",
                "evidence_update",
                "prior_version_id",
            ],
        },
        "harness_owned": [
            "请求绑定与哈希",
            "封闭字段与交叉引用检查",
            "证据绑定与角色门（未核验只能记为证据缺口）",
            "候选去重与区分覆盖检查",
            "定性置信度与新颖性表述检查",
            "不可变 id、时间戳、哈希与落盘",
        ],
        "model_owned": [
            "判断请求是就绪、需要澄清还是被阻塞",
            "形成机制上可区分的候选假设",
            "机制、预测、替代解释与混杂因素的科学内容",
            "证据与候选之间的支持、反对、限制关系",
            "定性置信度等级与理由",
            "最有区分力的下一项检验",
            "证据更新说明",
        ],
        "hard_boundaries": [
            "已有发现不是假设；机制不是可观测预测；观测相关不等于因果解释。",
            "未找到反证不等于获得支持；没有证据时如实标注证据缺口。",
            "技术失败的实验记录不是反对证据，只能记为证据缺口。",
            "文献摘要只是来源发现；只有核对原文确实对应主张后才能用作支持或反对。",
            "置信度只写定性等级与理由，不写精确概率。",
            "没有检索证据时不得声称首次提出；新颖性只能标为待核实。",
            "空结果用于更新假设，不自动证伪全部候选；证据冲突时保留竞争候选。",
            "不执行实验、不声称获得了本次会话之外的实验结果。",
        ],
        "response_contract": _compact_response_contract(),
        "instruction": (
            "探索阶段可以逐个形成和修改候选，不必一次提交完整组合。只有需要结构化交接或"
            "正式发布时，才补齐 scientific-hypothesis-response-v1 合同并创建检查点。"
            "不要在响应对象中加入运行 id、哈希、执行结果或散文。"
        ),
    }


# ---------------------------------------------------------------------------
# 证据登记簿（跨工具调用的确定性会话内登记）
# ---------------------------------------------------------------------------


class EvidenceRegister:
    """登记已核验证据；检查响应时只允许引用登记簿中的 id。"""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}

    def bind(self, payload: object) -> dict[str, Any]:
        row = validate_evidence_bind(payload)
        evidence_id = row["evidence_id"]
        if evidence_id in self._entries and self._entries[evidence_id] != row:
            raise ContractError(
                f"evidence_id {evidence_id} 已被另一份证据占用，请换一个 id"
            )
        self._entries[evidence_id] = row
        return {
            "schema_version": "scientific-hypothesis-evidence-bound-v1",
            "status": "bound",
            "evidence_id": evidence_id,
            "role": row["role"],
            "bound_evidence_count": len(self._entries),
        }

    def get(self, evidence_id: str) -> dict[str, Any] | None:
        return self._entries.get(evidence_id)

    def verified_ids(self) -> set[str]:
        return {
            evidence_id
            for evidence_id, row in self._entries.items()
            if row["verified_support"]
        }

    def all(self) -> list[dict[str, Any]]:
        return [deepcopy(row) for row in self._entries.values()]

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# 科学语义检查
# ---------------------------------------------------------------------------


def collect_hypothesis_semantic_errors(
    request: dict[str, Any],
    response: dict[str, Any],
    register: EvidenceRegister,
) -> list[str]:
    """收集证据、去重、置信度与表述层面的独立问题。"""

    if response.get("response_kind") != "hypotheses_ready":
        return []
    errors: list[str] = []
    candidates = response.get("candidates", [])

    # 1. 登记簿中的每条证据都必须能回溯到本轮请求。该复核也会拦截从旧版
    # 持久化状态恢复的幽灵证据；不能只依赖写入工具的即时校验。
    for entry in register.all():
        try:
            validate_evidence_provenance(request, entry)
        except ContractError as exc:
            errors.append(f"证据 {entry['evidence_id']} 的来源无效：{exc}")

    # 2. 证据引用必须指向登记簿，且支持/反对必须已核验。
    for candidate in candidates:
        label = f"候选 {candidate['id']}"
        for family, links in (
            ("支持证据", candidate["supporting_evidence"]),
            ("反对证据", candidate["opposing_evidence"]),
        ):
            for link in links:
                entry = register.get(link["evidence_id"])
                if entry is None:
                    errors.append(
                        f"{label} 的{family}引用了未绑定的证据：{link['evidence_id']}"
                    )
                    continue
                if not entry["verified_support"]:
                    errors.append(
                        f"{label} 把未核验材料 {link['evidence_id']} 用作{family}；"
                        "未核验内容只能列为证据缺口"
                    )
                if family == "支持证据" and entry["role"] not in {"supports", "limits"}:
                    errors.append(
                        f"{label} 的{family} {link['evidence_id']} 绑定角色为 {entry['role']}，"
                        "与支持关系不一致"
                    )
                if family == "反对证据" and entry["role"] != "opposes":
                    errors.append(
                        f"{label} 的{family} {link['evidence_id']} 绑定角色为 {entry['role']}，"
                        "与反对关系不一致"
                    )

    # 3. 技术失败的实验记录不得作为支持或反对证据（绑定层已挡指标，语义层再挡角色）。
    material_kinds = {
        material["id"]: material["material_kind"]
        for material in request.get("upstream_materials", [])
    }
    for candidate in candidates:
        for family, links in (
            ("支持证据", candidate["supporting_evidence"]),
            ("反对证据", candidate["opposing_evidence"]),
        ):
            for link in links:
                entry = register.get(link["evidence_id"])
                if entry is None:
                    continue
                if entry["evidence_kind"] == "experiment":
                    material_id = entry["material_id"]
                    if material_kinds.get(material_id) != "experiment_result":
                        errors.append(
                            f"候选 {candidate['id']} 的{family} {link['evidence_id']} 声称来自实验，"
                            f"但材料 {material_id} 不是实验执行记录"
                        )

    # 4. 候选去重：陈述不得逐字重复；多候选时不得共用完全相同的机制摘要。
    statements = [c["statement"] for c in candidates]
    if len(statements) != len(set(statements)):
        errors.append("存在逐字重复的候选陈述；请合并或改写为机制上可区分的候选")
    if len(candidates) > 1:
        mechanism_summaries = [c["mechanism"]["summary"] for c in candidates]
        if len(set(mechanism_summaries)) == 1:
            errors.append(
                "所有候选共用完全相同的机制摘要，属于同义改写；"
                "请合并为一个候选，或写出机制上真实的区别"
            )

    # 5. 置信度一致性：Wiki 只能约束机制和适用范围，不能替代观测支持。
    for candidate in candidates:
        confidence = candidate["confidence"]
        empirical_support = []
        for link in candidate["supporting_evidence"]:
            entry = register.get(link["evidence_id"])
            if (
                entry is not None
                and entry["verified_support"]
                and entry["role"] == "supports"
                and not entry["material_id"].startswith("kb_")
            ):
                empirical_support.append(entry)
        if confidence["level"] == "high" and not empirical_support:
            errors.append(
                f"候选 {candidate['id']} 在没有任何已核验的非 Wiki 支持证据时"
                "不得标记 high 置信度；Wiki 只能约束机制与适用范围"
            )
        if candidate["opposing_evidence"] and confidence["level"] == "high":
            errors.append(
                f"候选 {candidate['id']} 存在已核验反对证据，"
                "应保持 medium 或 low 并说明理由，不得标记 high"
            )

    # 6. 文本表述：不得声称首次提出（除非后续由新颖性绑定放行），不得写精确概率。
    texts: list[tuple[str, str]] = []
    for candidate in candidates:
        texts.append((f"候选 {candidate['id']}", candidate["statement"]))
        texts.append(
            (f"候选 {candidate['id']} 的机制", candidate["mechanism"]["summary"])
        )
        for index, prediction in enumerate(candidate["predictions"]):
            texts.append(
                (
                    f"候选 {candidate['id']} 的预测 {prediction['id']}",
                    prediction["statement"],
                )
            )
    notes = response.get("portfolio_notes")
    if isinstance(notes, str):
        texts.append(("组合说明", notes))
    for label, text in texts:
        if NOVELTY_CLAIM.search(text) is not None:
            errors.append(
                f"{label} 声称了“首次提出”类新颖性；没有检索核对证据时只能写“待核实”"
            )
        if PRECISE_PROBABILITY.search(text) is not None:
            errors.append(f"{label} 使用了精确百分比表达置信度，应改为定性等级与理由")

    # 7. 数值门槛追溯：逐个检查候选自己的预测、前提、假设、证伪条件
    # 与下一项检验。另一个候选引用的证据、Wiki 机制条目或全局登记簿中的
    # 无关证据都不能替本候选的观测门槛背书。
    for candidate in candidates:
        grounded_parts = [request["research_question"]]
        candidate_evidence_ids = {
            link["evidence_id"]
            for link in (
                candidate["supporting_evidence"] + candidate["opposing_evidence"]
            )
        }
        for evidence_id in candidate_evidence_ids:
            entry = register.get(evidence_id)
            if (
                entry is not None
                and entry["verified_support"]
                and not entry["material_id"].startswith("kb_")
            ):
                grounded_parts.append(entry["excerpt"])
        normalized_grounding = {
            "".join(match.group(0).split()).lower()
            for part in grounded_parts
            for match in HARD_NUMERIC_CUTOFF.finditer(part)
        }

        threshold_fields: list[tuple[str, list[str]]] = [
            ("必要前提", candidate["mechanism"]["required_premises"]),
            ("关键假设", candidate["assumptions"]),
            ("证伪条件", candidate["falsification_conditions"]),
            ("下一项检验预期信号", candidate["next_test"]["expected_signals"]),
        ]
        for prediction in candidate["predictions"]:
            threshold_fields.extend(
                [
                    (f"预测 {prediction['id']}", [prediction["statement"]]),
                    (f"预测 {prediction['id']} 的观测量", [prediction["observable"]]),
                    (
                        f"预测 {prediction['id']} 的削弱条件",
                        [prediction["would_weaken_if"]],
                    ),
                ]
            )
        for field, values in threshold_fields:
            for value in values:
                for match in HARD_NUMERIC_CUTOFF.finditer(value):
                    token = "".join(match.group(0).split()).lower()
                    if token not in normalized_grounding:
                        errors.append(
                            f"候选 {candidate['id']} 的{field}含有无依据的数值门槛："
                            f"“{match.group(0)}”；请删除该门槛、改为定性表述，"
                            "或让该候选直接引用包含同一门槛的已核验非 Wiki 证据"
                        )
    return list(dict.fromkeys(errors))


def _kb_grounding_warnings(response: dict[str, Any]) -> list[dict[str, Any]]:
    """知识库引用门禁（方案 §5.4 #2，warning 模式）。

    每个候选假设的 supporting/opposing evidence 中至少引用一个真实存在的
    kb_ 条目 id，或用非空 evidence_gaps 显式声明知识缺口；不满足则列入
    ``kb_grounding_missing``。知识库不可用时静默降级为空列表。
    """

    try:
        from knowledge_base import service as kb_service
        from knowledge_base.store import KnowledgeStore
    except Exception:  # noqa: BLE001
        return []
    try:
        subjects = [
            {
                "id": candidate["id"],
                "evidence_ids": [
                    link["evidence_id"]
                    for link in (
                        candidate.get("supporting_evidence", [])
                        + candidate.get("opposing_evidence", [])
                    )
                ],
                "knowledge_gap": bool(candidate.get("evidence_gaps")),
            }
            for candidate in response.get("candidates", [])
        ]
        store = KnowledgeStore()
        try:
            return kb_service.grounding_warnings(store, subjects)
        finally:
            store.close()
    except Exception:  # noqa: BLE001
        return []


def preflight_hypothesis_response(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    register: EvidenceRegister | None = None,
    *,
    include_validated_response: bool = False,
) -> dict[str, Any]:
    """检查任意响应类型；不创建 id、时间戳、文件或运行目录。"""

    request = validate_hypothesis_request(request_payload)
    active_register = register or EvidenceRegister()
    response = validate_hypothesis_response(response_payload, request, active_register)
    semantic_errors = collect_hypothesis_semantic_errors(
        request, response, active_register
    )
    if semantic_errors:
        formatted = "\n".join(f"- {error}" for error in semantic_errors)
        raise ContractError(
            f"科学语义检查发现 {len(semantic_errors)} 组问题，请逐项修正后再检查：\n"
            f"{formatted}"
        )
    result: dict[str, Any] = {
        "schema_version": "scientific-hypothesis-preflight-v1",
        "status": response["response_kind"],
        "task_name": request["task_name"],
        "research_question": request["research_question"],
        "request_sha256": canonical_json_sha256(request),
        "files_written": 0,
        "experiments_executed": 0,
        "bound_evidence_count": len(register) if register is not None else 0,
    }
    if response["response_kind"] == "clarification_needed":
        result["question_count"] = len(response["questions"])
        result["user_display_markdown"] = render_nonportfolio_response_markdown(
            response
        )
        return result
    if response["response_kind"] == "hypothesis_blocked":
        result["blocker_count"] = len(response["blockers"])
        result["user_display_markdown"] = render_nonportfolio_response_markdown(
            response
        )
        return result
    result["candidate_count"] = len(response["candidates"])
    result["distinction_count"] = len(response["pairwise_distinctions"])
    result["warnings"] = {"kb_grounding_missing": _kb_grounding_warnings(response)}
    if include_validated_response:
        result["_validated_response"] = response
    return result


# ---------------------------------------------------------------------------
# 排序检查
# ---------------------------------------------------------------------------


def preflight_hypothesis_ranking(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    ranking_payload: object,
    register: EvidenceRegister | None = None,
    *,
    include_validated_ranking: bool = False,
) -> dict[str, Any]:
    """检查排序请求：锚点闭合、名次连续、与候选/证据的一致性。不落盘。"""

    request = validate_hypothesis_request(request_payload)
    active_register = register or EvidenceRegister()
    response = validate_hypothesis_response(response_payload, request, active_register)
    if response["response_kind"] != "hypotheses_ready":
        raise ContractError("只有 hypotheses_ready 响应可以排序")
    ranking = ranking_mod.validate_ranking_request(
        ranking_payload, response["candidates"], active_register
    )
    consistency_errors = ranking_mod.check_ranking_consistency(
        ranking, response["candidates"]
    )
    if consistency_errors:
        formatted = "\n".join(f"- {error}" for error in consistency_errors)
        raise ContractError(
            f"排序一致性检查发现 {len(consistency_errors)} 组问题，请一次性修正：\n{formatted}"
        )
    scores = ranking_mod.compute_dimension_scores(ranking)
    result: dict[str, Any] = {
        "schema_version": "scientific-hypothesis-ranking-preflight-v1",
        "status": "ranking_ready",
        "candidate_count": len(response["candidates"]),
        "judgment_count": len(ranking["pairwise_judgments"]),
        "dimension_scores": scores,
        "files_written": 0,
    }
    if include_validated_ranking:
        result["_validated_ranking"] = ranking
    return result


# ---------------------------------------------------------------------------
# 读者视图渲染
# ---------------------------------------------------------------------------

_CONFIDENCE_TITLES = {"high": "较高", "medium": "中等", "low": "较低"}
_RUBRIC_LABELS = {spec["key"]: spec["label"] for spec in ranking_mod.RUBRIC_DIMENSIONS}
_EVIDENCE_KIND_TITLES = {
    "experiment": "实验结果",
    "literature": "文献",
    "upstream": "上游材料",
    "user": "用户提供材料",
}


def _md(value: object) -> str:
    return " ".join(str(value).replace("\r", " ").replace("\n", " ").split())


def _md_list(values: list[object]) -> str:
    return "；".join(_md(value) for value in values) if values else "无"


def render_nonportfolio_response_markdown(response: dict[str, Any]) -> str:
    """把通过检查的澄清或阻塞响应渲染为不含机器术语的中文 Markdown。"""

    kind = response.get("response_kind")
    if kind == "clarification_needed":
        lines = [
            "# 还需要你确认",
            "",
            "下面的信息会实质改变假设的形成方向。确认后即可继续生成假设组合。",
            "",
        ]
        for index, item in enumerate(response["questions"], start=1):
            lines.extend(
                [
                    f"{index}. **{_md(item['question'])}**",
                    f"   - 为什么需要确认：{_md(item['why_it_matters'])}",
                    f"   - 请补充：{_md(item['expected_answer'])}",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"
    if kind == "hypothesis_blocked":
        lines = [
            "# 暂时无法形成科学假设",
            "",
            "当前条件下还不能形成诚实、可检验的假设。",
            "",
        ]
        for item in response["blockers"]:
            lines.extend(
                [
                    f"- **原因：** {_md(item['reason'])}",
                    f"  - 如何继续：{_md(item['resolution'])}",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"
    raise ContractError("读者视图渲染只支持澄清或阻塞响应")


def render_hypothesis_portfolio_markdown(portfolio: dict[str, Any]) -> str:
    """把通过检查的机器组合渲染为面向研究者的中文 Markdown。"""

    portfolio = validate_hypothesis_portfolio(portfolio)
    candidates = portfolio["candidates"]
    candidate_index = {c["id"]: i for i, c in enumerate(candidates, start=1)}
    evidence = {e["evidence_id"]: e for e in portfolio["evidence_register"]}

    def evidence_label(evidence_id: str) -> str:
        entry = evidence.get(evidence_id)
        if entry is None:
            return evidence_id
        kind = _EVIDENCE_KIND_TITLES.get(entry["evidence_kind"], entry["evidence_kind"])
        return f"{kind}（{entry['material_id']}）"

    lines: list[str] = [
        "# 科学假设组合",
        "",
        f"**研究问题：** {_md(portfolio['research_question'])}",
        "",
        "本文件基于当前已核验证据形成候选假设，用于说明每个假设主张什么、依据什么、如何检验，"
        "以及现有证据的缺口。文中不包含本次会话之外新执行的实验结果。",
        "",
        f"本次共形成 {len(candidates)} 个候选假设。",
        "",
    ]

    ranking = portfolio.get("ranking")
    rank_position: dict[str, int] = {}
    if ranking is not None:
        rank_position = {
            row["candidate_id"]: row["rank"] for row in ranking.get("ranked", [])
        }
        lines.extend(["## 初步排序（本侧初审，证据审查 Agent 将独立复审）", ""])
        for row in sorted(ranking.get("ranked", []), key=lambda r: r["rank"]):
            cid = row["candidate_id"]
            title = (
                _md(candidates[candidate_index[cid] - 1]["statement"])
                if cid in candidate_index
                else cid
            )
            lines.extend(
                [
                    f"**第 {row['rank']} 名：候选 {candidate_index.get(cid, '?')}** — {title}",
                    f"- 排序理由：{_md(row['rationale'])}",
                    f"- 关键证据锚点：{('、'.join(evidence_label(e) for e in row['key_evidence_ids'])) or '无（该候选无已核验支持证据）'}",
                    f"- 相对薄弱维度：{('、'.join(_RUBRIC_LABELS.get(k, k) for k in row.get('weakest_dimensions', []))) or '无'}",
                    "",
                ]
            )
        lines.append("")

    for index, candidate in enumerate(candidates, start=1):
        heading = f"## 候选 {index}：{_md(candidate['statement'])}"
        if candidate["id"] in rank_position:
            heading = (
                f"## 候选 {index}（第 {rank_position[candidate['id']]} 名）："
                f"{_md(candidate['statement'])}"
            )
        lines.extend(
            [
                heading,
                "",
                f"**适用范围：** {_md(candidate['applicability'])}",
                "",
                f"**可能机制：** {_md(candidate['mechanism']['summary'])}",
                "",
                f"**机制依据：** {_md(candidate['mechanism']['physical_basis'])}",
                "",
                f"**必要前提：** {_md_list(candidate['mechanism']['required_premises'])}",
                "",
                f"**关键假设：** {_md_list(candidate['assumptions'])}",
                "",
                "**可观测预测：**",
                "",
            ]
        )
        for prediction in candidate["predictions"]:
            targets = "、".join(
                f"候选 {candidate_index[t]}" if t in candidate_index else t
                for t in prediction["distinguishes_from"]
            )
            lines.extend(
                [
                    f"- {_md(prediction['statement'])}",
                    f"  - 可观测量：{_md(prediction['observable'])}",
                    f"  - 区别于：{targets}",
                    f"  - 出现以下结果将削弱本候选：{_md(prediction['would_weaken_if'])}",
                ]
            )
        lines.append("")
        if candidate["supporting_evidence"]:
            lines.extend(["**支持证据：**", ""])
            for link in candidate["supporting_evidence"]:
                lines.append(
                    f"- {evidence_label(link['evidence_id'])}：{_md(link['relation_note'])}"
                )
            lines.append("")
        if candidate["opposing_evidence"]:
            lines.extend(["**反对证据：**", ""])
            for link in candidate["opposing_evidence"]:
                lines.append(
                    f"- {evidence_label(link['evidence_id'])}：{_md(link['relation_note'])}"
                )
            lines.append("")
        if candidate["evidence_gaps"]:
            lines.extend(
                ["**证据缺口：**", ""]
                + [f"- {_md(gap)}" for gap in candidate["evidence_gaps"]]
                + [""]
            )
        lines.extend(
            [
                f"**可区分的替代解释：** {_md_list(candidate['alternative_explanations'])}",
                "",
                f"**潜在混杂因素：** {_md_list(candidate['confounders'])}",
                "",
                "**可证伪条件：**",
                "",
            ]
            + [f"- {_md(item)}" for item in candidate["falsification_conditions"]]
            + [
                "",
                "**最有区分力的下一项检验：**",
                "",
                f"- 要回答：{_md(candidate['next_test']['objective'])}",
                f"- 区分力：{_md(candidate['next_test']['discriminating_power'])}",
                f"- 预期信号差异：{_md_list(candidate['next_test']['expected_signals'])}",
                "",
                f"**当前把握：** {_CONFIDENCE_TITLES[candidate['confidence']['level']]}；"
                f"{_md(candidate['confidence']['basis'])}",
            ]
        )
        if candidate["evidence_update"] is not None:
            lines.extend(
                [
                    "",
                    "**本次证据更新：** "
                    f"{_md(candidate['evidence_update']['summary'])}"
                    f"（更新原因：{_md(candidate['evidence_update']['reason'])}）",
                ]
            )
        lines.append("")

    if portfolio["pairwise_distinctions"]:
        lines.extend(["## 候选之间的本质区别", ""])
        for row in portfolio["pairwise_distinctions"]:
            left = f"候选 {candidate_index[row['left_id']]}"
            right = f"候选 {candidate_index[row['right_id']]}"
            lines.append(f"- **{left} 与 {right}：** {_md(row['distinction'])}")
        lines.append("")

    counterexample_table = portfolio.get("counterexample_table")
    if counterexample_table is not None:
        lines.extend(["## 反例与冲突点", ""])
        rows = counterexample_table.get("rows", [])
        if rows:
            for row in rows:
                cid = row.get("candidate_id")
                subject = (
                    f"候选 {candidate_index[cid]}"
                    if cid in candidate_index
                    else "组合层面"
                )
                kind_label = "反例" if row["kind"] == "counterexample" else "冲突点"
                anchor = (
                    f"（依据：{evidence_label(row['evidence_id'])}）"
                    if row.get("evidence_id")
                    else ""
                )
                lines.append(
                    f"- **{subject} · {kind_label}：** {_md(row['summary'])}{anchor}"
                )
        else:
            lines.append(
                "本次未识别出独立的反例或冲突点；各候选的反对证据与证据缺口见上文。"
            )
        notes = counterexample_table.get("notes")
        if notes:
            lines.extend(["", _md(notes)])
        lines.append("")

    if portfolio["evidence_register"]:
        lines.extend(["## 本次使用的证据", ""])
        for entry in portfolio["evidence_register"]:
            lines.append(
                f"- **{_EVIDENCE_KIND_TITLES.get(entry['evidence_kind'], entry['evidence_kind'])}"
                f"（{entry['material_id']}）：** {_md(entry['excerpt'])}"
            )
        lines.append("")

    if portfolio["portfolio_notes"]:
        lines.extend(["## 组合层面的说明", "", _md(portfolio["portfolio_notes"]), ""])
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 保存
# ---------------------------------------------------------------------------


def _safe_run_segment(value: str) -> str:
    import re as _re

    normalized = _re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return normalized[:64] or "hypothesis-portfolio"


def build_counterexample_table(
    response: dict[str, Any], register: EvidenceRegister
) -> dict[str, Any]:
    """从已通过检查的响应构建独立的反例/冲突点区块（代码生成，可追溯）。"""

    rows: list[dict[str, Any]] = []
    for candidate in response["candidates"]:
        for link in candidate["opposing_evidence"]:
            entry = register.get(link["evidence_id"])
            excerpt = entry["excerpt"] if entry is not None else ""
            summary = link["relation_note"]
            if excerpt:
                summary = (
                    f"{summary}（{excerpt[:120]}{'…' if len(excerpt) > 120 else ''}）"
                )
            rows.append(
                {
                    "candidate_id": candidate["id"],
                    "kind": "counterexample",
                    "summary": summary,
                    "evidence_id": link["evidence_id"],
                }
            )
        for gap in candidate["evidence_gaps"]:
            rows.append(
                {
                    "candidate_id": candidate["id"],
                    "kind": "conflict",
                    "summary": f"证据缺口：{gap}",
                    "evidence_id": None,
                }
            )
    return {"rows": rows, "notes": None}


def compile_hypothesis_portfolio(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    register: EvidenceRegister,
    ranking_payload: object | None = None,
) -> dict[str, Any]:
    request = validate_hypothesis_request(request_payload)
    response = validate_hypothesis_response(response_payload, request, register)
    if response["response_kind"] != "hypotheses_ready":
        raise ContractError("只有 hypotheses_ready 响应可以编译为正式组合")
    errors = collect_hypothesis_semantic_errors(request, response, register)
    if errors:
        formatted = "\n".join(f"- {error}" for error in errors)
        raise ContractError(f"科学语义检查发现 {len(errors)} 组问题：\n{formatted}")
    ranking = None
    if ranking_payload is not None:
        ranking = ranking_mod.validate_ranking_request(
            ranking_payload, response["candidates"], register
        )
        consistency_errors = ranking_mod.check_ranking_consistency(
            ranking, response["candidates"]
        )
        if consistency_errors:
            formatted = "\n".join(f"- {error}" for error in consistency_errors)
            raise ContractError(
                f"排序一致性检查发现 {len(consistency_errors)} 组问题：\n{formatted}"
            )
    portfolio_id = (
        f"{request['task_name']}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    portfolio = {
        "schema_version": PORTFOLIO_VERSION,
        "portfolio_id": portfolio_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "frozen",
        "request_sha256": canonical_json_sha256(request),
        "research_question": request["research_question"],
        "candidates": deepcopy(response["candidates"]),
        "pairwise_distinctions": deepcopy(response["pairwise_distinctions"]),
        "evidence_register": register.all(),
        "ranking": ranking,
        "counterexample_table": build_counterexample_table(response, register),
        "portfolio_notes": response["portfolio_notes"],
    }
    portfolio["portfolio_sha256"] = canonical_json_sha256(portfolio)
    return validate_hypothesis_portfolio(portfolio)


def freeze_hypothesis_portfolio(
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    register: EvidenceRegister,
    runs_root: Path | None = None,
    ranking_payload: object | None = None,
    path_root: Path | None = None,
) -> dict[str, Any]:
    """检查响应、落盘机器与读者视图，任一步失败即回滚。"""

    request = validate_hypothesis_request(request_payload)
    portfolio = compile_hypothesis_portfolio(
        request, response_payload, register, ranking_payload
    )
    root = Path(runs_root or RUNS_ROOT).resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_id = _safe_run_segment(portfolio["portfolio_id"])
    target = (root / run_id).resolve()
    if target.parent != root:
        raise ContractError("生成的运行 id 越出了运行目录")
    if target.exists():
        raise ContractError(f"假设组合运行目录已存在：{run_id}")
    try:
        target.mkdir(parents=False, exist_ok=False)
        (target / "hypothesis_request.json").write_text(
            json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (target / "hypothesis_portfolio.json").write_text(
            json.dumps(portfolio, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report_markdown = render_hypothesis_portfolio_markdown(portfolio)
        (target / "hypotheses.md").write_text(report_markdown, encoding="utf-8")
        stored_request = json.loads(
            (target / "hypothesis_request.json").read_text(encoding="utf-8")
        )
        stored_portfolio = json.loads(
            (target / "hypothesis_portfolio.json").read_text(encoding="utf-8")
        )
        validate_hypothesis_request(stored_request)
        validate_hypothesis_portfolio(stored_portfolio)
        if canonical_json_sha256(stored_request) != stored_portfolio["request_sha256"]:
            raise ContractError("落盘请求哈希与组合不一致")
        stored_markdown = (target / "hypotheses.md").read_text(encoding="utf-8")
        if stored_markdown != report_markdown:
            raise ContractError("落盘 Markdown 与渲染结果不一致")
    except BaseException:
        if target.exists() and target.parent == root:
            shutil.rmtree(target, ignore_errors=True)
        raise
    try:
        relative = target.relative_to(
            Path(path_root or PROJECT_ROOT).resolve()
        ).as_posix()
    except ValueError:
        relative = str(target)
    markdown_path = f"{relative}/hypotheses.md"
    user_display_markdown = (
        stored_markdown.rstrip()
        + "\n\n---\n\n"
        + f"完整 Markdown 文件：`{markdown_path}`\n"
    )
    return {
        "schema_version": OUTCOME_VERSION,
        "status": "frozen_and_valid",
        "run_id": run_id,
        "request_path": f"{relative}/hypothesis_request.json",
        "portfolio_path": f"{relative}/hypothesis_portfolio.json",
        "markdown_path": markdown_path,
        "user_message": "科学假设组合已经通过检查并生成。",
        "user_report_markdown": stored_markdown,
        "user_display_markdown": user_display_markdown,
        "request_sha256": portfolio["request_sha256"],
        "portfolio_sha256": portfolio["portfolio_sha256"],
        "candidate_count": len(portfolio["candidates"]),
        "distinction_count": len(portfolio["pairwise_distinctions"]),
        "evidence_count": len(portfolio["evidence_register"]),
        "ranked": portfolio["ranking"] is not None,
        "counterexample_row_count": len(portfolio["counterexample_table"]["rows"]),
        "files_written": 3,
        "experiments_executed": 0,
    }


__all__ = [
    "EvidenceRegister",
    "PROJECT_ROOT",
    "RUNS_ROOT",
    "build_wiki_evidence_excerpt",
    "build_counterexample_table",
    "build_hypothesis_brief",
    "build_natural_hypothesis_request",
    "collect_hypothesis_semantic_errors",
    "compile_hypothesis_portfolio",
    "freeze_hypothesis_portfolio",
    "preflight_hypothesis_ranking",
    "preflight_hypothesis_response",
    "render_hypothesis_portfolio_markdown",
    "render_nonportfolio_response_markdown",
    "validate_evidence_provenance",
]
