"""Closed contracts for 自动实验 Agent 1.0.

The model owns task interpretation, proposed methods, code, and scientific
explanation. Deterministic code owns identifiers, paths, hashes, execution
facts, resource observations, and persisted terminal state.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any

REQUEST_VERSION = "automatic-experiment-request-v1"
RESPONSE_VERSION = "automatic-experiment-response-v1"
RECORD_VERSION = "automatic-experiment-record-v1"
ENTRY_RESULT_VERSION = "automatic-experiment-entry-result-v1"
DESIGN_VERSION = "automatic-experiment-design-v1"
WORKER_RESULT_VERSION = "automatic-experiment-worker-result-v1"
SESSION_VERSION = "automatic-experiment-session-v1"

OUTCOMES = {
    "completed_interpretable",
    "partial_result",
    "scientific_null",
    "high_uncertainty",
    "input_missing",
    "method_mismatch",
    "technical_failure",
    "budget_stopped",
    "clarification_required",
    "boundary_blocked",
    "cancelled_by_user",
}
RESPONSE_KINDS = {"experiment_ready", "clarification_required", "execution_blocked"}
METHOD_FIT = {"suitable", "uncertain", "incompatible"}
BASIS_KINDS = {
    "user_request",
    "located_source",
    "data_derived",
    "method_standard",
    "bounded_pragmatic_choice",
    "qualitative_no_fixed_threshold",
}
SEED_MODES = {"fixed", "sequence", "user_provided"}
CRITERION_STATUS = {"met", "not_met", "uncertain", "not_evaluated"}
ENDPOINT_STATUS = {"completed", "failed", "not_evaluated"}
SCIENTIFIC_OUTCOMES = {
    "completed_interpretable",
    "partial_result",
    "scientific_null",
    "high_uncertainty",
}
STAGE_OUTCOMES = {
    "completed",
    "inconclusive",
    "input_missing",
    "evidence_conflict",
    "method_invalid",
    "technical_failure",
    "budget_reached",
}
TERMINAL_STAGE_TARGETS = OUTCOMES - {"clarification_required"}
RESULT_VALUE_KINDS = {"number", "count", "boolean", "category", "text"}
RESERVED_RUNTIME_ARTIFACT_NAMES = {
    "audit.md",
    "design.json",
    "entry_result.json",
    "record.json",
    "report.md",
    "request.json",
    "response.json",
    "result.json",
    "state.json",
}

DEFAULT_BUDGET = {
    "wall_seconds": 600,
    "cpu_seconds": 1200,
    "memory_mb": 4096,
    "disk_mb": 1024,
    "single_file_mb": 256,
    "stdout_kb": 10240,
    "stderr_kb": 10240,
    "max_attempts": 3,
    "gpu_count": 0,
    "gpu_memory_mb": 0,
}
HARD_BUDGET = {
    "wall_seconds": 3600,
    "cpu_seconds": 7200,
    "memory_mb": 8192,
    "disk_mb": 4096,
    "single_file_mb": 1024,
    "stdout_kb": 51200,
    "stderr_kb": 51200,
    "max_attempts": 5,
    "gpu_count": 8,
    "gpu_memory_mb": 262144,
}
DEFAULT_RUN_BUDGET = {
    "total_wall_seconds": 1800,
    "max_stages": 5,
    "max_total_attempts": 6,
}
HARD_RUN_BUDGET = {
    "total_wall_seconds": 5400,
    "max_stages": 5,
    "max_total_attempts": 10,
}
# Model-written experiment tasks commonly describe virtual workspace paths with
# a leading slash ("/inputs/data.csv").  Some agents also repeat the deep-agent
# work mount ("/work/inputs/data.csv") after locating staged files there.  The
# immutable snapshot contract stores both spellings project-relative as
# "inputs/data.csv", so accept and canonicalize either virtual spelling.
_INPUT_PREFIX = r"/?(?:(?:work/)?inputs|runs/[A-Za-z][A-Za-z0-9_-]{0,127}/public)"
_QUOTED_INPUT_PATTERNS = (
    re.compile(rf"`(?P<path>{_INPUT_PREFIX}/[^`\r\n]+)`"),
    re.compile(rf'"(?P<path>{_INPUT_PREFIX}/[^"\r\n]+)"'),
    re.compile(rf"'(?P<path>{_INPUT_PREFIX}/[^'\r\n]+)'"),
    re.compile(rf"“(?P<path>{_INPUT_PREFIX}/[^”\r\n]+)”"),
    re.compile(rf"‘(?P<path>{_INPUT_PREFIX}/[^’\r\n]+)’"),
)
_UNQUOTED_INPUT_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_./-])"
    rf"(?P<path>{_INPUT_PREFIX}/"
    r"""[^\s"'`“”‘’，。；：！？、,;:!?<>{}\[\]()（）]+)"""
)
_STAGED_SIDECAR_PATH = re.compile(
    r"""(?:@|staged\s+sidecar\s+|staged_data_inputs\s*[=:]\s*\[?\"?)"""
    r"""(?:/?(?:work/)?)?(?P<path>inputs/_staged\.json)\b"""
)
_FIXED_SEED_PATTERNS = (
    re.compile(
        r"(?:固定(?:随机)?种子|固定\s*(?:random\s*)?seed)\s*[:=：为]?\s*(?P<seed>\d{1,10})",
        re.IGNORECASE,
    ),
    re.compile(r"\bseed\s*[:=]\s*(?P<seed>\d{1,10})\b", re.IGNORECASE),
)
_ONE_STAGE_PATTERN = re.compile(
    r"(?:最多|至多|仅|只)?\s*(?:一个|一|1\s*个?)\s*(?:实验)?阶段|"
    r"(?:at\s+most|only)\s+one\s+(?:experiment\s+)?stage",
    re.IGNORECASE,
)
_ONE_ATTEMPT_PATTERN = re.compile(
    r"(?:最多|至多|仅|只)?\s*(?:一次|一\s*次|1\s*次)\s*(?:正式)?(?:尝试|运行|执行)|"
    r"(?:at\s+most|only)\s+one\s+(?:formal\s+)?(?:attempt|run|execution)",
    re.IGNORECASE,
)

SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SAFE_REF = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
HARD_NUMERIC_CUTOFF = re.compile(
    r"(?:至少|至多|不低于|不高于|不超过|超过|高于|低于|小于|大于|少于|多于|"
    r"at\s+least|at\s+most|more\s+than|less\s+than|greater\s+than|"
    r"fewer\s+than|above|below|>=|<=|≥|≤|>|<)"
    r"[^。；;.!?！？]{0,32}\d+(?:\.\d+)?",
    re.IGNORECASE,
)
RELATIVE_DECISION_CUTOFF = re.compile(
    r"(?:至少|至多|不低于|不高于|不超过|超过|高于|低于|小于|大于|少于|多于|"
    r"at\s+least|at\s+most|more\s+than|less\s+than|greater\s+than|"
    r"fewer\s+than|above|below|>=|<=|≥|≤|>|<)"
    r"[^。；;.!?！？]{0,48}"
    r"(?:一半|半数|百分之[零〇一二三四五六七八九十百]+|"
    r"[-+]?\d+(?:\.\d+)?\s*%|[-+]?\d+(?:\.\d+)?\s*(?:倍|times?|fold))",
    re.IGNORECASE,
)
SAMPLE_COUNT_GATE = re.compile(
    r"(?:样本|观测|记录|行|条|个|samples?|observations?|records?|rows?)",
    re.IGNORECASE,
)
INTERVAL_BASIS_LANGUAGE = re.compile(
    r"(?:inferential|confidence\s+interval|credible\s+interval|prediction\s+interval|"
    r"bootstrap|resampl(?:e|ing)|interval\s+estimat|uncertainty\s+interval|"
    r"推断|置信区间|可信区间|预测区间|自助法|重采样|区间估计|不确定性区间|误差界)",
    re.IGNORECASE,
)
INTERVAL_NONBASIS_LANGUAGE = re.compile(
    r"(?:无|未|不做|不进行|没有|no|without)"
    r"[^。；;.!?！？]{0,12}"
    r"(?:推断|区间|inferential|interval|bootstrap|resampl)",
    re.IGNORECASE,
)
NUMBER_TOKEN = re.compile(r"[-+]?\d+(?:\.\d+)?")
DERIVED_DIRECTION_TEXT = re.compile(
    r"(?:差值|差异|改善量|变化量|delta|difference|improvement|change)",
    re.IGNORECASE,
)
DERIVED_DIRECTION_MEASUREMENT = re.compile(
    r"(?:^|_)(?:delta|difference|improvement|change)(?:_|$)",
    re.IGNORECASE,
)
CONTRAST_PROMISE = re.compile(
    r"\b(?:difference|contrast|delta)\b|差值|二者之差|两种条件之差|条件间差异",
    re.IGNORECASE,
)
SENSITIVITY_CONTEXT = re.compile(
    r"(?:sensitivity|robust|敏感性|稳健性)",
    re.IGNORECASE,
)
MULTI_CONDITION_CONTEXT = re.compile(
    r"(?:two|both)\s+(?:fit(?:ting|ted)?\s+)?conditions|两种(?:拟合)?条件",
    re.IGNORECASE,
)
UNCALIBRATED_BASELINE_LANGUAGE = re.compile(
    r"(?:未校准|未校正|未经校准|未经校正|原始(?:候选)?读数|"
    r"uncalibrated|uncorrected|(?<![A-Za-z])raw(?![A-Za-z])"
    r"(?:\s+(?:candidate\s+)?(?:reading|value))?)",
    re.IGNORECASE,
)
READER_INTERNAL_TOKEN = re.compile(
    r"(?:automatic-experiment-[a-z0-9-]+|schema_version|proposed_outcome|"
    r"criterion_results|scientific_assessment|\bcrit_[A-Za-z0-9_-]+\b|"
    r"\battempt-\d{3}\b|\bworker(?:_result)?\b|paired_comparison_audits|"
    r"measurement\s+name|result\s+id|artifact\s+id|endpoint\s+id|stage\s+id|"
    r"exit\s+code|退出码|代码(?:成功)?执行|程序(?:成功)?(?:执行|完成)|"
    r"产物文件|文件(?:均|已)?(?:生成|输出))",
    re.IGNORECASE,
)
CODE_LIKE_READER_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*"
    r"(?:_[A-Za-z0-9]+)+(?![A-Za-z0-9_])"
)
QUANTITATIVE_CLAIM_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])[-+−]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    r"\s*(?:%|％)?(?![A-Za-z0-9_])"
)
CJK_TEXT = re.compile(r"[\u3400-\u9fff]")
SYNTHETIC_TASK = re.compile(
    r"(?:合成(?:测试)?夹具|模拟夹具|synthetic\s+(?:test\s+)?fixture)",
    re.IGNORECASE,
)
SYNTHETIC_DISCLOSURE = re.compile(
    r"(?:合成|模拟|夹具|synthetic|fixture)",
    re.IGNORECASE,
)
UNSUPPORTED_SIGNIFICANCE_LANGUAGE = re.compile(r"显著")
UNSUPPORTED_GENERALIZATION_LANGUAGE = re.compile(r"泛化")
UNSUPPORTED_BIAS_ELIMINATION_LANGUAGE = re.compile(
    r"(?:已|完全|彻底|大幅)?消除(?:了)?"
    r"[^。；;.!?！？]{0,40}(?:系统性)?(?:偏差|偏移|高估|低估)"
    r"|(?:系统性)?(?:偏差|偏移|高估|低估)"
    r"[^。；;.!?！？]{0,40}(?:已|被|完全|彻底|大幅)?消除(?:了)?"
)
UNSUPPORTED_TRIVIAL_IMPACT_LANGUAGE = re.compile(
    r"(?:不具有|没有|无)(?:任何)?实质影响|"
    r"(?:差异|差值|影响)(?:很|非常)?(?:微小|可忽略)|"
    r"(?:略有差异|略有变化|变化较小|差异较小|差值较小|"
    r"变化幅度较小|差异幅度较小)|"
    r"(?:差异|差值|变化|影响)?(?:的)?量级(?:很|较)?小|"
    r"(?:参数|估计|结果|关系)[^。；;.!?！？]{0,24}保持稳定|"
    r"(?:可以|可)(?:直接)?忽略|"
    r"影响有限|"
    r"(?:结果|估计|结论|效应|差异|差值|影响)"
    r"[^。；;.!?！？]{0,24}不敏感|negligible|trivial",
    re.IGNORECASE,
)
UNGROUNDED_NONZERO_IMPACT_DEFINITION = re.compile(
    r"(?:非零|不为零)[^。；;.!?！？]{0,24}"
    r"(?:表示|说明|意味着)[^。；;.!?！？]{0,32}"
    r"(?:实质影响|实质性变化)",
    re.IGNORECASE,
)
NONCLAIM_TRIVIAL_IMPACT_LANGUAGE = re.compile(
    r"(?:不能|无法|不足以|尚不能)[^。；;.!?！？]{0,32}"
    r"(?:判定|判断|说明)[^。；;.!?！？]{0,32}"
    r"(?:实质影响|微小|忽略)",
    re.IGNORECASE,
)
UNSUPPORTED_SYSTEMIC_HOLDOUT_LANGUAGE = re.compile(
    r"(?:系统(?:性)?(?:正|负)?偏差|系统性(?:高于|低于|偏高|偏低))"
)
NONCLAIM_SYSTEMIC_HOLDOUT_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|并非|禁止|避免|不支持|不足以|不可)"
    r"[^。；;.!?！？]{0,40}(?:系统(?:性)?(?:正|负)?偏差|系统性(?:高于|低于|偏高|偏低))"
)
UNSUPPORTED_CLOSENESS_LANGUAGE = re.compile(
    r"(?:预测|读数|误差|结果)[^。；;.!?！？]{0,36}接近"
    r"|接近[^。；;.!?！？]{0,24}(?:比较坐标|参考坐标|真值|零)"
)
UNSUPPORTED_NONIDENTITY_LANGUAGE = re.compile(
    r"(?:近似线性[^。；;.!?！？]{0,24})?非恒等(?:的)?关系|"
    r"(?:关系|映射)[^。；;.!?！？]{0,24}(?:并非|不是)恒等",
    re.IGNORECASE,
)
UNSUPPORTED_CORRELATION_DEGREE_LANGUAGE = re.compile(
    r"(?:极强|很强|非常强|强烈|高度)(?:的)?(?:正向|负向)?(?:线性)?(?:相关|关联)"
    r"|相关系数(?:接近|趋近)\s*[+＋]?1(?:\.0+)?"
    r"|线性关联(?:模式)?(?:非常)?清晰"
)
NONCLAIM_CORRELATION_DEGREE_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|不可|不足以)"
    r"[^。；;.!?！？]{0,36}(?:极强|很强|非常强|强烈|高度|接近\s*[+＋]?1|清晰)"
)
CORRELATION_DEGREE_BASIS_LANGUAGE = re.compile(
    r"(?:相关|关联)[^。；;.!?！？]{0,30}(?:分级|等级|阈值|界限|强度标准)"
    r"|(?:分级|等级|阈值|界限|强度标准)[^。；;.!?！？]{0,30}(?:相关|关联)"
)
MONOTONIC_LANGUAGE = re.compile(r"单调(?:递增|递减|增加|下降|变化)?")
NONCLAIM_MONOTONIC_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|不可|不足以)"
    r"[^。；;.!?！？]{0,30}单调(?:递增|递减|增加|下降|变化)?"
)
NONCLAIM_CLOSENESS_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|并非|禁止|避免|不支持|不足以|不可)"
    r"[^。；;.!?！？]{0,24}接近"
)
ROBUSTNESS_LANGUAGE = re.compile(
    r"(?:结果|结论|发现|估计|方向|表现|模型|分析)"
    r"[^。；;.!?！？]{0,24}(?:稳健(?:性)?|robust(?:ness)?)|"
    r"(?:整体|总体|全面)[^。；;.!?！？]{0,12}"
    r"(?:稳健(?:性)?|robust(?:ness)?)",
    re.IGNORECASE,
)
BOUNDED_ROBUSTNESS_LANGUAGE = re.compile(
    r"(?:这一|该|单一)(?:质量)?标记行|方向性结论一致|当前敏感性检查|"
    r"仅限[^。；;.!?！？]{0,24}(?:标记|扰动|条件)",
    re.IGNORECASE,
)
NONCLAIM_ROBUSTNESS_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|并非|不构成|不足以)"
    r"[^。；;.!?！？]{0,36}(?:稳健(?:性)?|robust(?:ness)?)",
    re.IGNORECASE,
)
STAGE_COUNT_DECLARATION = re.compile(
    r"(?:(?P<cjk>[一二三四五])|(?P<digit>[1-5])|"
    r"(?P<english>one|two|three|four|five))"
    r"\s*(?:个)?\s*[- ]?(?:实验)?(?:阶段|stages?)",
    re.IGNORECASE,
)
STAGE_COUNT_WORDS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}
NONCLAIM_SIGNIFICANCE_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|并非|禁止|避免|不支持|不进行)"
    r"[^。；;.!?！？]{0,20}显著(?:性(?:检验|分析|推断|证据)?)?"
    r"|显著性(?:检验|分析|推断|证据)",
)
P_VALUE_PLAN = re.compile(
    r"(?:^|[_\s-])p[_\s-]?value(?:$|[_\s-])|p\s*值", re.IGNORECASE
)
R_SQUARED_PLAN = re.compile(
    r"(?:\br[_ -]?(?:squared|square|2)\b|\br\s*\^\s*2\b|R²|决定系数|拟合优度|解释方差)",
    re.IGNORECASE,
)
AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE = re.compile(
    r"(?:仅|只)保留(?:了)?(?:质量)?标记(?:的)?(?:行|观测|样本|数据)?|"
    r"保留标记(?:的)?(?:行|观测|样本|数据)?\s*[（(]\s*排除",
    re.IGNORECASE,
)
QUALITY_FLAG_EVALUATION_SCOPE = re.compile(
    r"(?:保留|排除|剔除|仅保留|只保留)[^。；;]{0,12}"
    r"(?:质量)?标记[^。；;]{0,16}(?:评价|评估|留出|测试)",
    re.IGNORECASE,
)
# 拟合条件自由文本的归一化：把模型可能写出的各种“包含/排除被标记观测”措辞
# 统一成契约术语，保证 record、audit、报告与图表对同一拟合条件使用同一中文标签。
_FIT_CONDITION_EXCLUDE = re.compile(r"exclude|without|drop|排除|剔除", re.IGNORECASE)
_FIT_CONDITION_INCLUDE = re.compile(
    r"include|with|retain|all|保留|包含|全部", re.IGNORECASE
)


def _normalize_fit_condition_text(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return value
    if _FIT_CONDITION_EXCLUDE.search(value):
        return "排除标记观测"
    if _FIT_CONDITION_INCLUDE.search(value):
        return "包含被标记观测"
    return value


P_VALUE_REQUEST = re.compile(
    r"p\s*值|p[-_\s]?value|(?:^|[^A-Za-z0-9_])p\s*(?:[<=>≤≥]|less\s+than|greater\s+than)|"
    r"显著性|假设检验|hypothesis\s+test|significance",
    re.IGNORECASE,
)
NONCLAIM_GENERALIZATION_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|并非|禁止|避免|不支持|不作|不做|不应|不可)"
    r"[^。；;.!?！？]{0,20}泛化(?:能力|结论|主张)?"
    r"|(?:限制|削弱)[^。；;.!?！？]{0,20}泛化能力"
    r"|泛化(?:能力|结论|主张)?(?:有限|不足|未验证|未经验证|不确定)"
    r"|泛化(?:性|能力)[^。；;.!?！？]{0,16}(?:缺乏|没有|尚无|未有)"
    r"(?:充分)?(?:检验|验证|证据)"
    r"|(?:若|如)(?:需|要)?[^。；;.!?！？]{0,24}(?:验证|检验|评估)"
    r"[^。；;.!?！？]{0,16}泛化(?:性|能力)",
)
NONCLAIM_BIAS_ELIMINATION_LANGUAGE = re.compile(
    r"(?:不|未|没有|无|不能|无法|并非|禁止|避免|不支持|不足以|不可)"
    r"[^。；;.!?！？]{0,40}(?:证明|表明|说明|断定|声称|认为)?"
    r"[^。；;.!?！？]{0,20}(?:已|完全|彻底|大幅)?消除(?:了)?"
    r"[^。；;.!?！？]{0,40}(?:系统性)?(?:偏差|偏移|高估|低估)"
    r"|(?:不|未|没有|无|不能|无法|并非|禁止|避免|不支持|不足以|不可)"
    r"[^。；;.!?！？]{0,40}(?:系统性)?(?:偏差|偏移|高估|低估)"
    r"[^。；;.!?！？]{0,40}(?:已|被|完全|彻底|大幅)?消除(?:了)?"
)
PAIRED_COMPARISON_METRICS = {"mae", "rmse", "mean_signed_error"}
PAIRED_COMPARISON_DELTA_FORMULAS = {
    "baseline_minus_candidate",
    "candidate_minus_baseline",
}
READER_METRIC_TERMS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"平均绝对误差|mean\s+absolute\s+error|\bMAE\b", re.IGNORECASE), "mae"),
    (
        re.compile(r"均方根误差|root\s+mean\s+squared\s+error|\bRMSE\b", re.IGNORECASE),
        "rmse",
    ),
    (re.compile(r"均方误差|mean\s+squared\s+error|\bMSE\b", re.IGNORECASE), "mse"),
    (
        re.compile(r"平均有符号误差|mean\s+signed\s+error", re.IGNORECASE),
        "mean_signed_error",
    ),
)
METRIC_NONCLAIM_PREFIX = re.compile(
    r"(?:不|未|没有|无|不能|无法|未曾|not|no|without)"
    r"\s*[^。；;.!?！？]{0,16}$",
    re.IGNORECASE,
)
EXPLICIT_SCIENTIFIC_OUTCOME_CLAIMS = {
    "completed_interpretable": re.compile(
        r"(?:结果|终态|最终状态|本次实验|本次结果)"
        r"[^。；;.!?！？]{0,16}(?:标记|判定|归类|返回|定为|属于|是|为)"
        r"[^。；;.!?！？]{0,8}完成且结果可解释"
    ),
    "partial_result": re.compile(
        r"(?:结果|终态|最终状态|本次实验|本次结果)"
        r"[^。；;.!?！？]{0,16}(?:标记|判定|归类|返回|定为|属于|是|为)"
        r"[^。；;.!?！？]{0,8}部分结果"
    ),
    "scientific_null": re.compile(
        r"(?:结果|终态|最终状态|本次实验|本次结果)"
        r"[^。；;.!?！？]{0,16}(?:标记|判定|归类|返回|定为|属于|是|为)"
        r"[^。；;.!?！？]{0,8}科学空结果"
    ),
    "high_uncertainty": re.compile(
        r"(?:结果|终态|最终状态|本次实验|本次结果)"
        r"[^。；;.!?！？]{0,16}(?:标记|判定|归类|返回|定为|属于|是|为)"
        r"[^。；;.!?！？]{0,8}高不确定性"
    ),
}


class ContractError(ValueError):
    """A user- or model-correctable contract failure with optional repair metadata."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "contract_validation_failed",
        field_path: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.field_path = field_path
        self.suggestion = suggestion


def _reject_reader_internal_terms(
    label: str,
    *,
    statement: str,
    basis_text: str,
) -> None:
    for field_name, text in (
        ("statement", statement),
        ("basis_text", basis_text),
    ):
        match = READER_INTERNAL_TOKEN.search(text)
        if match is None:
            continue
        matched_term = match.group(0)
        field_path = f"{label}.{field_name}"
        raise ContractError(
            f"{field_path} reader text must not expose internal contracts or "
            f"workflow terms; matched {matched_term!r}",
            error_code="reader_internal_term",
            field_path=field_path,
            suggestion=(
                f"把 {matched_term!r} 改写为该判据直接表达的自然语言科研含义；"
                "不要改动机器字段、引用关系或科学阈值。"
            ),
        )


def _walk_text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for nested in value.values() for text in _walk_text_values(nested)]
    if isinstance(value, list):
        return [text for nested in value for text in _walk_text_values(nested)]
    return []


def _quantitative_claims(text: str) -> list[tuple[float, float]]:
    claims: list[tuple[float, float]] = []
    for match in QUANTITATIVE_CLAIM_TOKEN.finditer(text):
        token = match.group(0).strip().replace("−", "-")
        is_percent = token.endswith(("%", "％"))
        if is_percent:
            token = token[:-1].strip()
        try:
            value = float(token)
        except ValueError:
            continue
        normalized_value = value / 100.0 if is_percent else value
        mantissa, _, exponent_text = token.lower().partition("e")
        decimal_places = len(mantissa.rsplit(".", 1)[1]) if "." in mantissa else 0
        significant_digits = len(mantissa.lstrip("+-").replace(".", "").lstrip("0"))
        exponent = int(exponent_text) if exponent_text else 0
        rounding_tolerance = 0.0
        if decimal_places > 0 and significant_digits >= 1:
            rounding_tolerance = 0.5 * (10.0 ** (exponent - decimal_places))
            if is_percent:
                rounding_tolerance /= 100.0
        claims.append((normalized_value, rounding_tolerance))
    return claims


def _quantitative_values(text: str) -> list[float]:
    return [value for value, _tolerance in _quantitative_claims(text)]


def _unsupported_quantitative_claims(
    texts: list[str],
    design: dict[str, Any],
    worker: dict[str, Any],
    task_text: str | None,
    evidence_basis_texts: list[str] | None = None,
) -> list[float]:
    """Find report numbers absent from outputs, immutable inputs, or the plan."""

    allowed: list[float] = []
    for source_text in [
        *(_walk_text_values(design)),
        *(_walk_text_values(worker)),
        *([task_text] if isinstance(task_text, str) else []),
        *(evidence_basis_texts or []),
    ]:
        allowed.extend(_quantitative_values(source_text))
    for row in worker.get("measurements", []):
        if isinstance(row.get("value"), (int, float)) and not isinstance(
            row.get("value"), bool
        ):
            allowed.append(float(row["value"]))
    for row in worker.get("result_items", []):
        if (
            row.get("value_kind") in {"number", "count"}
            and isinstance(row.get("value"), (int, float))
            and not isinstance(row.get("value"), bool)
        ):
            allowed.append(float(row["value"]))
    # Assessment rationale may accurately summarize the number of verified
    # measurements, typed diagnostics, endpoints, artifacts, or criteria.
    # These are audit counts rather than scientific estimates, but they remain
    # traceable to the current immutable result and design.
    allowed.extend(
        float(len(rows))
        for rows in (
            worker.get("measurements", []),
            worker.get("result_items", []),
            worker.get("endpoint_results", []),
            worker.get("artifacts", []),
            design.get("criteria", []),
        )
        if isinstance(rows, list)
    )

    plan_by_name = {
        str(row.get("name")): row
        for row in design.get("measurement_plan", [])
        if isinstance(row, dict) and row.get("name")
    }
    directional_differences: list[float] = []
    for row in worker.get("measurements", []):
        value = row.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        planned = plan_by_name.get(str(row.get("name")), {})
        semantics = " ".join(
            (
                str(row.get("name", "")),
                str(planned.get("display_name", "")),
                str(planned.get("scientific_meaning", "")),
            )
        )
        if DERIVED_DIRECTION_TEXT.search(semantics):
            directional_differences.append(float(value))

    magnitude_language = re.compile(
        r"(?:较|比)[^。；;.!?！？]{0,24}(?:低|高|少|多)"
        r"|(?:降低|下降|减少|减小|升高|上升|增加|增大)"
        r"|(?:差异|变化)(?:的)?(?:绝对值|幅度)"
        r"|\b(?:lower|higher|decrease|increase|magnitude|absolute\s+difference)\b",
        re.IGNORECASE,
    )
    unsupported: list[float] = []
    for text in texts:
        text_allowed = list(allowed)
        if magnitude_language.search(text):
            text_allowed.extend(abs(value) for value in directional_differences)
        for claimed, rounding_tolerance in _quantitative_claims(text):
            if not any(
                math.isclose(
                    claimed,
                    expected,
                    rel_tol=1e-9,
                    abs_tol=max(1e-12, rounding_tolerance),
                )
                for expected in text_allowed
            ):
                unsupported.append(claimed)
    return list(dict.fromkeys(unsupported))


def _unsupported_reader_claim(
    text: str,
    marker: re.Pattern[str],
    safe_context: re.Pattern[str],
) -> bool:
    return marker.search(safe_context.sub("", text)) is not None


def _measurement_supports_metric(name: str, metric: str) -> bool:
    lowered = name.lower()
    tokens = set(re.split(r"[^a-z0-9]+", lowered))
    if metric == "mae":
        return (
            "mae" in tokens
            or "mean_absolute_error" in lowered
            or "平均绝对误差" in name
        )
    if metric == "rmse":
        return (
            "rmse" in tokens
            or "root_mean_squared_error" in lowered
            or "均方根误差" in name
        )
    if metric == "mse":
        return "mse" in tokens or "mean_squared_error" in lowered or "均方误差" in name
    return (
        "mean_signed_error" in lowered
        or "signed_error" in lowered
        or "mean_bias" in lowered
        or "平均有符号误差" in name
        or "平均符号误差" in name
    )


def _fit_condition_role(value: Any) -> str | None:
    """Classify only the quality-flag inclusion contrast used by an audit."""

    text = str(value or "").casefold()
    marked = re.search(r"flag|mark|quality|标记|可疑|异常", text) is not None
    if re.search(r"exclude|without|drop|filter|排除|剔除|不含|不包含", text):
        return "excluded_flagged"
    if re.search(r"\b(?:all|full|complete)\b|全部|全量|完整", text):
        return "included_flagged"
    if marked and re.search(r"include|with|retain|包含|纳入|保留", text):
        return "included_flagged"
    return None


def _same_fitted_condition(left: Any, right: Any) -> bool:
    """Recognize one fitted condition even when prose order differs slightly."""

    left_text = re.sub(r"\s+", "", str(left or "").casefold())
    right_text = re.sub(r"\s+", "", str(right or "").casefold())
    if not left_text or not right_text:
        return False
    if left_text == right_text:
        return True
    left_role = _fit_condition_role(left_text)
    right_role = _fit_condition_role(right_text)
    if left_role is None or left_role != right_role:
        return False
    left_counts = set(re.findall(r"\d+", left_text))
    right_counts = set(re.findall(r"\d+", right_text))
    return bool(left_counts & right_counts)


def _unsupported_reader_metric(
    texts: list[str], measurement_names: set[str]
) -> str | None:
    for text in texts:
        for marker, metric in READER_METRIC_TERMS:
            for match in marker.finditer(text):
                prefix = text[max(0, match.start() - 32) : match.start()]
                if METRIC_NONCLAIM_PREFIX.search(prefix):
                    continue
                if not any(
                    _measurement_supports_metric(name, metric)
                    for name in measurement_names
                ):
                    return match.group(0)
    return None


def _validate_numeric_cutoff_basis(
    statement: str,
    basis_kind: str,
    basis_text: str,
    label: str,
    source_refs: list[str] | None = None,
) -> None:
    cutoff_match = HARD_NUMERIC_CUTOFF.search(statement)
    if cutoff_match is None:
        return
    if basis_kind == "qualitative_no_fixed_threshold":
        raise ContractError(
            f"{label} contains a numeric cutoff without a grounded basis",
            error_code="ungrounded_numeric_cutoff",
            field_path=f"{label}.statement",
            suggestion=(
                "删除百分比或其他硬阈值并改为有界的方向性判据；只有在用户、"
                "已定位来源、数据推导或方法标准明确支持时，才能保留数值阈值并"
                "在 basis_text 中说明来源。"
            ),
        )
    if basis_kind == "method_standard":
        raise ContractError(
            f"{label} cannot justify a numeric cutoff by calling it a method standard",
            error_code="numeric_cutoff_method_standard_ungrounded",
            field_path=f"{label}.basis_kind",
            suggestion=(
                "方法名称本身不是数值阈值的来源。删除该阈值并采用方向性或定性判据；"
                "若阈值确由用户要求、已提供资料或可复算数据支持，改用相应 basis_kind "
                "并给出可追溯 source_refs。"
            ),
        )
    if basis_kind == "data_derived" and SAMPLE_COUNT_GATE.search(statement):
        raise ContractError(
            f"{label} cannot derive a minimum sample-count gate from the current sample",
            error_code="data_derived_sample_count_gate",
            field_path=f"{label}.basis_kind",
            suggestion=(
                "当前数据只能给出实际样本数，不能自行证明一个最低通过门槛。"
                "删除该门槛并报告样本数及限制；只有用户或已提供资料明确规定时才保留。"
            ),
        )
    if basis_kind in {"located_source", "data_derived"} and not source_refs:
        raise ContractError(
            f"{label} numeric cutoff requires at least one traceable source reference",
            error_code="numeric_cutoff_source_missing",
            field_path=f"{label}.source_refs",
            suggestion=(
                "数值阈值必须指向已提供的资料或用于推导该阈值的数据；若没有这种依据，"
                "删除硬阈值。"
            ),
        )
    cutoff_numbers = [
        float(value) for value in NUMBER_TOKEN.findall(cutoff_match.group(0))
    ]
    basis_numbers = [float(value) for value in NUMBER_TOKEN.findall(basis_text)]
    if not cutoff_numbers or cutoff_numbers[-1] not in basis_numbers:
        raise ContractError(
            f"{label}.basis_text must repeat the numeric cutoff and explain its provenance",
            error_code="numeric_cutoff_provenance_missing",
            field_path=f"{label}.basis_text",
            suggestion=(
                "只有用户或已提供资料明确给出该阈值时，才逐字引用并说明来源；"
                "不得用当前样本数反推任意最低门槛，否则删除硬阈值并报告实际样本数。"
            ),
        )


def _is_grounded_zero_direction_rule(
    text: str,
    criteria: list[dict[str, Any]],
) -> bool:
    """Allow the natural zero boundary of a declared directional contrast.

    Zero is not an externally chosen scientific threshold when the design has
    explicitly defined a difference/improvement measurement: it is the
    arithmetic boundary between the two directions. Non-zero cutoffs remain
    subject to provenance checks.
    """

    matches = list(HARD_NUMERIC_CUTOFF.finditer(text))
    if not matches or DERIVED_DIRECTION_TEXT.search(text) is None:
        return False
    values: list[float] = []
    for match in matches:
        numbers = NUMBER_TOKEN.findall(match.group(0))
        if not numbers:
            return False
        values.append(float(numbers[-1]))
    if any(value != 0.0 for value in values):
        return False
    return any(
        any(
            DERIVED_DIRECTION_MEASUREMENT.search(str(reference)) is not None
            for reference in row.get("measurement_refs", [])
        )
        for row in criteria
    )


def _paired_measurements_requiring_audit(
    names: set[str],
    measurement_plan: dict[str, dict[str, Any]],
) -> set[str]:
    """Find machine- or reader-described measurements that claim paired calibration."""

    required: set[str] = set()
    for name in names:
        marker = "_improvement"
        if marker not in name:
            continue
        base, suffix = name.split(marker, 1)
        candidate_pairs = [
            (f"raw_{base}{suffix}", f"calibrated_{base}{suffix}"),
            (f"{base}_raw{suffix}", f"{base}_calibrated{suffix}"),
        ]
        if "_" in base:
            scope, metric = base.rsplit("_", 1)
            candidate_pairs.extend(
                [
                    (
                        f"{scope}_raw_{metric}{suffix}",
                        f"{scope}_calibrated_{metric}{suffix}",
                    ),
                    (
                        f"raw_{scope}_{metric}{suffix}",
                        f"calibrated_{scope}_{metric}{suffix}",
                    ),
                ]
            )
        for raw_name, calibrated_name in candidate_pairs:
            if raw_name in names and calibrated_name in names:
                required.update({raw_name, calibrated_name, name})
                break

    semantic_groups: dict[tuple[str, tuple[str, ...]], dict[str, set[str]]] = {}
    coarse_groups: dict[tuple[str, tuple[str, ...]], dict[str, set[str]]] = {}
    condition_patterns = {
        "included": r"\b(?:with|include|included)\b|包含|纳入",
        "excluded": r"\b(?:without|exclude|excluded|filtered|clean)\b|排除|剔除|清洁",
        "complete": r"\b(?:all|full|complete|unfiltered)\b|全部|完整",
        "evaluation": r"\b(?:evaluation|holdout|test)\b|评价|评估|留出",
        "fit": r"\b(?:training|fit)\b|训练集|拟合集|校准集",
    }
    for name in names:
        row = measurement_plan.get(name, {})
        text = " ".join(
            (
                str(row.get("display_name", "")),
                str(row.get("scientific_meaning", "")),
            )
        )
        metric = next(
            (
                metric_name
                for marker, metric_name in READER_METRIC_TERMS
                if metric_name in PAIRED_COMPARISON_METRICS
                and marker.search(text) is not None
            ),
            None,
        )
        if metric is None:
            continue
        baseline = re.search(
            r"\b(?:raw|uncalibrated)\b|原始(?:读数|值)?|"
            r"未(?:校准|校正)|未经(?:校准|校正)|(?:校准|校正)前",
            text,
            re.IGNORECASE,
        )
        candidate = re.search(
            r"\bcalibrated\b|(?:校准|校正)后|(?<!未)经(?:校准|校正)|"
            r"重新(?:校准|校正)",
            text,
            re.IGNORECASE,
        )
        if bool(baseline) == bool(candidate):
            continue
        tags = tuple(
            sorted(
                tag
                for tag, pattern in condition_patterns.items()
                if re.search(pattern, text, re.IGNORECASE)
            )
        )
        group = semantic_groups.setdefault(
            (metric, tags),
            {"baseline": set(), "candidate": set()},
        )
        group["baseline" if baseline else "candidate"].add(name)
        coarse_tags = (
            ("evaluation",)
            if "evaluation" in tags
            else (("fit",) if "fit" in tags else ())
        )
        coarse_group = coarse_groups.setdefault(
            (metric, coarse_tags),
            {"baseline": set(), "candidate": set()},
        )
        coarse_group["baseline" if baseline else "candidate"].add(name)
    for group in [*semantic_groups.values(), *coarse_groups.values()]:
        if group["baseline"] and group["candidate"]:
            required.update(group["baseline"])
            required.update(group["candidate"])
    return required


def _sensitivity_criterion_roles(
    names: list[str],
    measurement_plan: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str]]:
    """Use reader-facing scientific definitions, never internal naming conventions."""

    contrasts: set[str] = set()
    for name in names:
        row = measurement_plan.get(name, {})
        description = " ".join(
            (
                str(row.get("display_name", "")),
                str(row.get("scientific_meaning", "")),
            )
        )
        explicit_delta_name = re.search(
            r"(?:^|_)(?:delta|difference|contrast)(?:_|$)",
            name,
            re.IGNORECASE,
        )
        condition_specific_name = re.search(
            r"(?:^|_)(?:include|included|with|exclude|excluded|without|"
            r"all|full|complete|perturbed|sensitivity|condition_[ab])(?:_|$)",
            name,
            re.IGNORECASE,
        )
        derived_name_without_condition = (
            DERIVED_DIRECTION_MEASUREMENT.search(name)
            and condition_specific_name is None
        )
        if (
            explicit_delta_name
            or derived_name_without_condition
            or re.search(
                r"\b(?:delta|difference|contrast)\b|"
                r"\bchange\s+between\b|"
                r"差值|差异|二者之差|两种[^。；;]{0,24}之差|"
                r"(?:斜率|截距|误差|估计量|参数|均值|指标)(?:之)?差|条件间变化",
                description,
                re.IGNORECASE,
            )
        ):
            contrasts.add(name)
    return set(names) - contrasts, contrasts


def _has_explicit_condition_measurement(
    names: list[str] | set[str], measurement_plan: dict[str, dict[str, Any]]
) -> bool:
    """Return whether planned measurements explicitly represent a sensitivity condition.

    A general statement that a conclusion is subject to robustness checks is not
    itself a fitted-condition comparison.  The paired-condition contract applies
    only when the cited measurements actually distinguish included/excluded,
    perturbed/reference, or named condition A/B estimates.
    """

    marker = re.compile(
        r"(?:^|_)(?:include|included|with|exclude|excluded|without|"
        r"perturbed|reference|baseline|sensitivity|condition_(?:[ab]|delta))(?:_|\b)"
        r"|包含|排除|扰动|参考条件|基准条件|条件[甲乙AB]",
        re.IGNORECASE,
    )
    for name in names:
        row = measurement_plan.get(name, {})
        if marker.search(
            " ".join(
                (
                    str(name),
                    str(row.get("display_name", "")),
                    str(row.get("scientific_meaning", "")),
                )
            )
        ):
            return True
    return False


def _linked_sensitivity_roles(
    condition_refs: set[str],
    delta_refs: set[str],
    all_criterion_refs: set[str],
    comparison_audits: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    """Close a split criterion only through an explicit paired-comparison link."""

    conditions = set(condition_refs)
    deltas = set(delta_refs)
    for audit in comparison_audits:
        baseline = audit["baseline_measurement"]
        candidate = audit["candidate_measurement"]
        delta = audit["delta_measurement"]
        triplet = {baseline, candidate, delta}
        if (
            delta
            and triplet <= all_criterion_refs
            and (delta in deltas or {baseline, candidate} <= conditions)
        ):
            conditions.update({baseline, candidate})
            deltas.add(delta)
    return conditions, deltas


def _reader_model_direction_conflict(
    measurement_rows: list[dict[str, Any]],
    comparison_audits: list[dict[str, Any]],
) -> str | None:
    """Return a measurement whose prose reverses a declared candidate-to-reference fit."""

    candidate_to_reference = any(
        any(
            "candidate" in str(column).casefold()
            for column in audit.get("candidate_model_input_columns", [])
        )
        and "reference"
        in str(audit.get("candidate_model_target_column", "")).casefold()
        for audit in comparison_audits
    )
    if not candidate_to_reference:
        return None
    for row in measurement_rows:
        reader_text = " ".join(
            (
                str(row.get("display_name", "")),
                str(row.get("scientific_meaning", "")),
            )
        )
        if re.search(
            r"候选读数(?:相对于|关于|对)参考读数的"
            r"(?:普通)?最小二乘(?:回归)?斜率",
            reader_text,
        ):
            return str(row.get("name", ""))
    return None


def _missing_requested_parameter_differences(
    task: str,
    measurement_rows: list[dict[str, Any]],
) -> list[str]:
    """Find explicitly requested linear-parameter differences omitted from a plan."""

    requests_parameter_differences = re.search(
        r"(?:校正|模型)?参数[^。；;]{0,40}(?:两种|两个|不同)?条件"
        r"[^。；;]{0,24}(?:差异|差值)"
        r"|(?:校正|模型)?参数[^。；;]{0,40}(?:差异|差值)"
        r"|各自(?:的)?估计量[^。；;]{0,24}(?:二者)?(?:差异|差值)"
        r"|(?:calibration|model)\s+parameters?[^.!?]{0,50}"
        r"(?:difference|contrast)",
        task,
        re.IGNORECASE,
    )
    if requests_parameter_differences is None:
        return []
    missing: list[str] = []
    for label, pattern in (
        ("斜率", re.compile(r"斜率|\bslope\b", re.IGNORECASE)),
        ("截距", re.compile(r"截距|\bintercept\b", re.IGNORECASE)),
    ):
        matching = [
            " ".join(
                (
                    str(row.get("display_name", "")),
                    str(row.get("scientific_meaning", "")),
                )
            )
            for row in measurement_rows
            if pattern.search(
                " ".join(
                    (
                        str(row.get("display_name", "")),
                        str(row.get("scientific_meaning", "")),
                    )
                )
            )
        ]
        condition_estimates = [
            text
            for text in matching
            if not re.search(
                r"差值|差异|之差|\bdifference\b|\bcontrast\b",
                text,
                re.IGNORECASE,
            )
        ]
        has_difference = any(
            re.search(
                r"差值|差异|之差|\bdifference\b|\bcontrast\b",
                text,
                re.IGNORECASE,
            )
            for text in matching
        )
        if len(condition_estimates) >= 2 and not has_difference:
            missing.append(label)
    return missing


def _loss_delta_direction_conflicts(text: str, formula: str) -> bool:
    """Detect a reader-facing sign claim that contradicts a lower-is-better loss."""

    positive_better = re.search(
        r"(?:正值|positive(?:\s+values?)?)[^。；;.!?！？]{0,64}"
        r"(?:改善|更优|误差(?:降低|减小)|better|improv)",
        text,
        re.IGNORECASE,
    )
    positive_worse = re.search(
        r"(?:正值|positive(?:\s+values?)?)[^。；;.!?！？]{0,64}"
        r"(?:恶化|更差|误差(?:增加|增大)|worse|deteriorat)",
        text,
        re.IGNORECASE,
    )
    negative_better = re.search(
        r"(?:负值|negative(?:\s+values?)?)[^。；;.!?！？]{0,64}"
        r"(?:改善|更优|误差(?:降低|减小)|better|improv)",
        text,
        re.IGNORECASE,
    )
    negative_worse = re.search(
        r"(?:负值|negative(?:\s+values?)?)[^。；;.!?！？]{0,64}"
        r"(?:恶化|更差|误差(?:增加|增大)|worse|deteriorat)",
        text,
        re.IGNORECASE,
    )
    if formula == "baseline_minus_candidate":
        return bool(positive_worse or negative_better)
    return bool(positive_better or negative_worse)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError("payload must contain only finite JSON values") from exc


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def clone(value: object, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} must contain only finite JSON values") from exc


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def _exact(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise ContractError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ContractError(f"{label} has unknown fields: {', '.join(unknown)}")


def _text(
    value: object,
    label: str,
    *,
    minimum: int = 1,
    maximum: int = 4000,
    preserve: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a string")
    result = value if preserve else value.strip()
    if len(result) < minimum or (minimum > 0 and not result.strip()):
        raise ContractError(f"{label} must contain at least {minimum} characters")
    if len(result) > maximum:
        raise ContractError(f"{label} exceeds {maximum} characters")
    return result


def _nullable_text(value: object, label: str, maximum: int = 4000) -> str | None:
    if value is None:
        return None
    return _text(value, label, maximum=maximum)


def _nullable_object(value: object, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _object(value, label)


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ContractError(f"{label} must be boolean")
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ContractError(f"{label} must be in [{minimum}, {maximum}]")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(f"{label} must be finite")
    return result


def _enum(value: object, choices: set[str], label: str) -> str:
    result = _text(value, label, maximum=100)
    if result not in choices:
        raise ContractError(f"{label} must be one of: {sorted(choices)}")
    return result


def _array(value: object, label: str, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be an array")
    if not minimum <= len(value) <= maximum:
        raise ContractError(f"{label} must contain {minimum} to {maximum} items")
    return value


def _text_array(
    value: object, label: str, maximum: int, item_maximum: int = 1000
) -> list[str]:
    rows = _array(value, label, 0, maximum)
    result = [
        _text(row, f"{label}[{index}]", maximum=item_maximum)
        for index, row in enumerate(rows)
    ]
    if len(result) != len(set(result)):
        raise ContractError(f"{label} must contain unique values")
    return result


def _safe_id(value: object, label: str) -> str:
    result = _text(value, label, maximum=64)
    if SAFE_ID.fullmatch(result) is None:
        raise ContractError(f"{label} must match {SAFE_ID.pattern}")
    return result


def _safe_ref(value: object, label: str) -> str:
    result = _text(value, label, maximum=128)
    if SAFE_REF.fullmatch(result) is None:
        raise ContractError(f"{label} must be a safe reference")
    return result


def _unique_ids(rows: list[dict[str, Any]], label: str) -> set[str]:
    ids: set[str] = set()
    for index, row in enumerate(rows):
        item_id = _safe_id(row.get("id"), f"{label}[{index}].id")
        if item_id in ids:
            raise ContractError(f"{label} has duplicate id: {item_id}")
        ids.add(item_id)
    return ids


def normalize_budget(value: object) -> dict[str, int]:
    budget = clone(_object(value, "resource_budget"), "resource_budget")
    _exact(budget, set(DEFAULT_BUDGET), "resource_budget")
    for field, maximum in HARD_BUDGET.items():
        minimum = 0 if field in {"gpu_count", "gpu_memory_mb"} else 1
        _integer(budget[field], f"resource_budget.{field}", minimum, maximum)
    if budget["single_file_mb"] > budget["disk_mb"]:
        raise ContractError("resource_budget.single_file_mb cannot exceed disk_mb")
    return budget


def normalize_run_budget(value: object) -> dict[str, int]:
    budget = clone(_object(value, "run_budget"), "run_budget")
    _exact(budget, set(DEFAULT_RUN_BUDGET), "run_budget")
    for field, maximum in HARD_RUN_BUDGET.items():
        _integer(budget[field], f"run_budget.{field}", 1, maximum)
    return budget


def _explicit_input_refs(task: str) -> list[dict[str, Any]]:
    """Extract explicit input paths from a natural-language task string.

    The _staged.json sidecar (written by the research state machine) takes
    precedence over regex-parsed paths: when the task references it, every
    entry it declares is a hash-bound input_ref that the experiment contract
    must honour.
    """
    # _staged.json sidecar takes absolute priority.
    staged_match = _STAGED_SIDECAR_PATH.search(task)
    if staged_match:
        sidecar_path = staged_match.group("path")
        try:
            from automatic_experiment.paths import current_task_workspace

            workspace = current_task_workspace()
            if workspace is not None:
                sidecar_file = workspace / sidecar_path
                if sidecar_file.is_file():
                    sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
                    refs = sidecar.get("input_refs")
                    if isinstance(refs, list) and refs:
                        return [
                            {
                                "id": row.get("id", f"input_{index:02d}"),
                                "path": row["path"],
                                "description": row.get("description", ""),
                                "required": bool(row.get("required", True)),
                            }
                            for index, row in enumerate(refs, start=1)
                            if isinstance(row, dict)
                            and isinstance(row.get("path"), str)
                        ]
        except Exception:
            pass  # Fall through to regex-based extraction.
    matches: list[tuple[int, str]] = []
    for pattern in _QUOTED_INPUT_PATTERNS:
        matches.extend(
            (match.start("path"), match.group("path").strip())
            for match in pattern.finditer(task)
        )
    matches.extend(
        (match.start("path"), match.group("path"))
        for match in _UNQUOTED_INPUT_PATTERN.finditer(task)
    )
    seen: set[str] = set()
    paths: list[str] = []
    for _, path in sorted(matches, key=lambda row: row[0]):
        normalized_path = path.lstrip("/")
        if normalized_path.startswith("work/inputs/"):
            normalized_path = normalized_path.removeprefix("work/")
        if normalized_path not in seen:
            seen.add(normalized_path)
            paths.append(normalized_path)
    return [
        {
            "id": f"input_{index:02d}",
            "path": path,
            "description": "Explicit input path referenced in the natural-language task.",
            "required": True,
        }
        for index, path in enumerate(paths, start=1)
    ]


def _explicit_seed(task: str) -> int | None:
    """Extract an unambiguous user-specified fixed seed from natural text."""

    seeds = {
        int(match.group("seed"))
        for pattern in _FIXED_SEED_PATTERNS
        for match in pattern.finditer(task)
    }
    valid = {seed for seed in seeds if 0 <= seed <= 2**31 - 1}
    return next(iter(valid)) if len(valid) == 1 else None


def default_request(task: str) -> dict[str, Any]:
    exact = _text(task, "task", minimum=8, maximum=4000, preserve=True)
    digest = hashlib.sha256(exact.encode("utf-8")).hexdigest()[:12]
    resource_budget = deepcopy(DEFAULT_BUDGET)
    run_budget = deepcopy(DEFAULT_RUN_BUDGET)
    if _ONE_ATTEMPT_PATTERN.search(exact):
        resource_budget["max_attempts"] = 1
        run_budget["max_total_attempts"] = 1
    if _ONE_STAGE_PATTERN.search(exact):
        run_budget["max_stages"] = 1
    explicit_seed = _explicit_seed(exact)
    refs = _explicit_input_refs(exact)
    # If the regex-based extraction returned an _staged.json entry, replace it
    # with the sidecar's actual declared inputs (prevents loading the sidecar
    # file as if it were a data input).
    staged_paths = [r["path"] for r in refs if r["path"].endswith("_staged.json")]
    if staged_paths:
        try:
            from automatic_experiment.paths import current_task_workspace

            workspace = current_task_workspace()
            if workspace is not None:
                sidecar_file = workspace / staged_paths[0]
                if sidecar_file.is_file():
                    sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
                    declared = sidecar.get("input_refs")
                    if isinstance(declared, list) and declared:
                        refs = [
                            {
                                "id": row.get("id", f"input_{index:02d}"),
                                "path": row["path"],
                                "description": row.get("description", ""),
                                "required": bool(row.get("required", True)),
                            }
                            for index, row in enumerate(declared, start=1)
                            if isinstance(row, dict)
                            and isinstance(row.get("path"), str)
                        ]
        except Exception:
            pass  # sidecar load failure is non-fatal; keep regex result
    return {
        "schema_version": REQUEST_VERSION,
        "task_name": f"question_{digest}",
        "task": exact,
        "input_refs": refs,
        "success_criteria": [],
        "method_constraints": [],
        "resource_budget": resource_budget,
        "run_budget": run_budget,
        "seed_policy": {
            "mode": "fixed",
            "seeds": [explicit_seed if explicit_seed is not None else 1729],
        },
        "replay_of": None,
        "user_notes": "",
    }


def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = clone(_object(payload, "request"), "request")
    fields = {
        "schema_version",
        "task_name",
        "task",
        "input_refs",
        "success_criteria",
        "method_constraints",
        "resource_budget",
        "run_budget",
        "seed_policy",
        "replay_of",
        "user_notes",
    }
    _exact(request, fields, "request")
    if request["schema_version"] != REQUEST_VERSION:
        raise ContractError(f"request.schema_version must be {REQUEST_VERSION}")
    _safe_id(request["task_name"], "request.task_name")
    _text(request["task"], "request.task", minimum=8, maximum=4000, preserve=True)
    input_rows = [
        _object(row, f"request.input_refs[{index}]")
        for index, row in enumerate(
            _array(request["input_refs"], "request.input_refs", 0, 50)
        )
    ]
    _unique_ids(input_rows, "request.input_refs")
    for index, row in enumerate(input_rows):
        label = f"request.input_refs[{index}]"
        _exact(row, {"id", "path", "description", "required"}, label)
        _text(row["path"], f"{label}.path", maximum=1000)
        _text(row["description"], f"{label}.description", maximum=1000)
        _boolean(row["required"], f"{label}.required")
    criteria = [
        _object(row, f"request.success_criteria[{index}]")
        for index, row in enumerate(
            _array(request["success_criteria"], "request.success_criteria", 0, 30)
        )
    ]
    _unique_ids(criteria, "request.success_criteria")
    for index, row in enumerate(criteria):
        label = f"request.success_criteria[{index}]"
        _exact(
            row,
            {
                "id",
                "statement",
                "basis_kind",
                "basis_text",
                "source_refs",
                "artifact_refs",
            },
            label,
        )
        statement = _text(row["statement"], f"{label}.statement")
        basis_kind = _enum(row["basis_kind"], BASIS_KINDS, f"{label}.basis_kind")
        basis_text = _text(row["basis_text"], f"{label}.basis_text")
        _reject_reader_internal_terms(
            label,
            statement=statement,
            basis_text=basis_text,
        )
        _text_array(row["source_refs"], f"{label}.source_refs", 20)
        _text_array(row["artifact_refs"], f"{label}.artifact_refs", 20)
        _validate_numeric_cutoff_basis(
            statement,
            basis_kind,
            basis_text,
            label,
            _text_array(row["source_refs"], f"{label}.source_refs", 20),
        )
    _text_array(request["method_constraints"], "request.method_constraints", 20)
    request["resource_budget"] = normalize_budget(request["resource_budget"])
    request["run_budget"] = normalize_run_budget(request["run_budget"])
    seed_policy = _object(request["seed_policy"], "request.seed_policy")
    _exact(seed_policy, {"mode", "seeds"}, "request.seed_policy")
    _enum(seed_policy["mode"], SEED_MODES, "request.seed_policy.mode")
    seeds = _array(seed_policy["seeds"], "request.seed_policy.seeds", 1, 20)
    normalized_seeds = [
        _integer(seed, f"request.seed_policy.seeds[{index}]", 0, 2**31 - 1)
        for index, seed in enumerate(seeds)
    ]
    if len(normalized_seeds) != len(set(normalized_seeds)):
        raise ContractError("request.seed_policy.seeds must be unique")
    _nullable_text(request["replay_of"], "request.replay_of", maximum=128)
    _text(
        request["user_notes"],
        "request.user_notes",
        minimum=0,
        maximum=4000,
        preserve=True,
    )
    return request


def validate_response(
    payload: dict[str, Any], request_payload: dict[str, Any]
) -> dict[str, Any]:
    request = validate_request(request_payload)
    response = clone(_object(payload, "response"), "response")
    fields = {
        "schema_version",
        "task_name",
        "task",
        "response_kind",
        "normalized_task",
        "design_summary",
        "clarifications",
        "blockers",
        "method_fit",
    }
    _exact(response, fields, "response")
    if response["schema_version"] != RESPONSE_VERSION:
        raise ContractError(f"response.schema_version must be {RESPONSE_VERSION}")
    if _safe_id(response["task_name"], "response.task_name") != request["task_name"]:
        raise ContractError("response.task_name must exactly match request.task_name")
    if _text(response["task"], "response.task", preserve=True) != request["task"]:
        raise ContractError("response.task must exactly match request.task")
    kind = _enum(response["response_kind"], RESPONSE_KINDS, "response.response_kind")
    _text(response["normalized_task"], "response.normalized_task", minimum=8)
    _text(response["design_summary"], "response.design_summary")
    clarifications = _text_array(
        response["clarifications"], "response.clarifications", 3
    )
    blockers = _text_array(response["blockers"], "response.blockers", 10)
    method_fit = _enum(response["method_fit"], METHOD_FIT, "response.method_fit")
    if kind == "experiment_ready" and (
        clarifications or blockers or method_fit == "incompatible"
    ):
        raise ContractError(
            "experiment_ready cannot contain clarifications, blockers, or incompatible method_fit"
        )
    if kind == "clarification_required" and not clarifications:
        raise ContractError(
            "clarification_required requires at least one clarification"
        )
    if kind == "execution_blocked" and not blockers:
        raise ContractError("execution_blocked requires at least one blocker")
    return response


def validate_design(
    payload: dict[str, Any],
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    *,
    allow_legacy: bool = False,
) -> dict[str, Any]:
    request = validate_request(request_payload)
    response = validate_response(response_payload, request)
    design = clone(_object(payload, "design"), "design")
    fields = {
        "schema_version",
        "task_name",
        "normalized_task",
        "design_summary",
        "method_fit",
        "input_ids",
        "research_frame",
        "measurement_plan",
        "result_plan",
        "method_decisions",
        "paired_comparison_audits",
        "criteria",
        "artifact_plan",
        "experiment_stages",
        "interpretation_policy",
    }
    _exact(design, fields, "design")
    if design["schema_version"] != DESIGN_VERSION:
        raise ContractError(f"design.schema_version must be {DESIGN_VERSION}")
    if _safe_id(design["task_name"], "design.task_name") != request["task_name"]:
        raise ContractError("design.task_name must match request.task_name")
    if (
        _text(design["normalized_task"], "design.normalized_task")
        != response["normalized_task"]
    ):
        raise ContractError(
            "design.normalized_task must match response.normalized_task"
        )
    _text(design["design_summary"], "design.design_summary")
    _enum(design["method_fit"], {"suitable", "uncertain"}, "design.method_fit")
    input_ids = _text_array(design["input_ids"], "design.input_ids", 50)
    request_input_ids = {row["id"] for row in request["input_refs"]}
    if not set(input_ids).issubset(request_input_ids):
        raise ContractError("design.input_ids references unknown request inputs")
    research_frame = _object(design["research_frame"], "design.research_frame")
    _exact(
        research_frame,
        {
            "primary_question",
            "analysis_mode",
            "claim_scope",
            "input_evidence",
            "supported_questions",
            "deferred_questions",
            "assumptions",
            "threats_to_validity",
            "literature_basis",
        },
        "design.research_frame",
    )
    _text(research_frame["primary_question"], "design.research_frame.primary_question")
    _text(research_frame["analysis_mode"], "design.research_frame.analysis_mode")
    _text(research_frame["claim_scope"], "design.research_frame.claim_scope")
    evidence_rows = _array(
        research_frame["input_evidence"],
        "design.research_frame.input_evidence",
        len(input_ids),
        len(input_ids),
    )
    evidence_ids: set[str] = set()
    for index, raw in enumerate(evidence_rows):
        row = _object(raw, f"design.research_frame.input_evidence[{index}]")
        label = f"design.research_frame.input_evidence[{index}]"
        _exact(row, {"input_id", "role", "intended_use", "limitations"}, label)
        input_id = _safe_id(row["input_id"], f"{label}.input_id")
        if input_id in evidence_ids:
            raise ContractError(
                f"design.research_frame.input_evidence duplicate input_id: {input_id}"
            )
        evidence_ids.add(input_id)
        _text(row["role"], f"{label}.role")
        _text(row["intended_use"], f"{label}.intended_use")
        _text(row["limitations"], f"{label}.limitations")
    if evidence_ids != set(input_ids):
        raise ContractError(
            "design.research_frame.input_evidence must describe every design input exactly once"
        )
    supported_questions = _array(
        research_frame["supported_questions"],
        "design.research_frame.supported_questions",
        1,
        20,
    )
    for index, row in enumerate(supported_questions):
        _text(row, f"design.research_frame.supported_questions[{index}]")
    for field in ("deferred_questions", "assumptions", "threats_to_validity"):
        _text_array(
            research_frame[field],
            f"design.research_frame.{field}",
            20,
        )
    if not research_frame["threats_to_validity"]:
        raise ContractError(
            "design.research_frame.threats_to_validity requires at least one explicit boundary"
        )
    _text(research_frame["literature_basis"], "design.research_frame.literature_basis")
    measurement_plan_rows = _array(
        design["measurement_plan"],
        "design.measurement_plan",
        0,
        200,
    )
    measurement_plan_names: set[str] = set()
    measurement_plan_by_name: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(measurement_plan_rows):
        row = _object(raw, f"design.measurement_plan[{index}]")
        label = f"design.measurement_plan[{index}]"
        _exact(
            row,
            {"name", "display_name", "role", "unit", "scientific_meaning"},
            label,
        )
        name = _safe_ref(row["name"], f"{label}.name")
        if name in measurement_plan_names:
            raise ContractError(f"design.measurement_plan has duplicate name: {name}")
        measurement_plan_names.add(name)
        display_name = _text(
            row["display_name"],
            f"{label}.display_name",
            maximum=200,
        )
        _enum(row["role"], {"primary", "secondary", "diagnostic"}, f"{label}.role")
        _text(row["unit"], f"{label}.unit", minimum=0, maximum=100, preserve=True)
        scientific_meaning = _text(
            row["scientific_meaning"],
            f"{label}.scientific_meaning",
            maximum=1200,
        )
        if AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
            f"{display_name} {scientific_meaning}"
        ):
            raise ContractError(
                f"{label} uses ambiguous quality-flag inclusion language",
                error_code="ambiguous_quality_flag_language",
                field_path=label,
                suggestion=(
                    "明确写成“包含被标记观测的拟合”或“排除被标记观测的拟合”。"
                    "“仅保留标记观测”会被理解为只使用异常观测，不能用于表示排除标记后"
                    "保留其余观测。"
                ),
            )
        raw_reader_fields = CODE_LIKE_READER_IDENTIFIER.findall(
            f"{display_name} {scientific_meaning}"
        )
        if raw_reader_fields:
            raise ContractError(
                f"{label} exposes raw field or category names: "
                + ", ".join(sorted(set(raw_reader_fields))),
                error_code="reader_definition_exposes_raw_fields",
                field_path=label,
                suggestion=(
                    "显示名和释义会进入科研报告。改用自然语言科研名称；"
                    "原始列名和类别代码只放在成对证据映射或实验代码中。"
                ),
            )
        if UNGROUNDED_NONZERO_IMPACT_DEFINITION.search(scientific_meaning):
            raise ContractError(
                f"{label}.scientific_meaning treats any nonzero difference as substantive",
                error_code="ungrounded_substantive_impact_definition",
                field_path=f"{label}.scientific_meaning",
                suggestion=(
                    "只把差值定义为两种条件下估计量的变化方向与幅度；是否具有"
                    "实际意义必须结合测量精度、不确定性或预先给定的科学依据判断。"
                ),
            )
        if re.search(
            r"(?:^|_)mse(?:_|$)|\bMSE\b", f"{name} {display_name}"
        ) and re.search(
            r"(?:平均有符号|平均符号|mean\s+signed)",
            f"{display_name} {scientific_meaning}",
            re.IGNORECASE,
        ):
            raise ContractError(
                f"{label} uses MSE for mean signed error",
                error_code="metric_abbreviation_conflict",
                field_path=f"{label}.name",
                suggestion=(
                    "MSE 通常表示均方误差，不能表示平均有符号误差。若计算有符号"
                    "偏差，请把名称改为 mean_signed_error 或 bias，并同步改写显示名。"
                ),
            )
        measurement_plan_by_name[name] = row

    result_plan_rows = _array(
        design["result_plan"],
        "design.result_plan",
        0,
        100,
    )
    result_plan_ids: set[str] = set()
    numeric_diagnostic_result_ids: set[str] = set()
    for index, raw in enumerate(result_plan_rows):
        row = _object(raw, f"design.result_plan[{index}]")
        label = f"design.result_plan[{index}]"
        _exact(
            row,
            {
                "id",
                "display_name",
                "value_kind",
                "role",
                "unit",
                "scientific_meaning",
            },
            label,
        )
        result_id = _safe_ref(row["id"], f"{label}.id")
        if result_id in result_plan_ids:
            raise ContractError(f"design.result_plan has duplicate id: {result_id}")
        result_plan_ids.add(result_id)
        result_display = _text(
            row["display_name"], f"{label}.display_name", maximum=200
        )
        value_kind = _enum(row["value_kind"], RESULT_VALUE_KINDS, f"{label}.value_kind")
        result_role = _enum(
            row["role"], {"primary", "secondary", "diagnostic"}, f"{label}.role"
        )
        if value_kind in {"number", "count"} and result_role != "diagnostic":
            raise ContractError(
                f"{label} answer-bearing numeric results must use measurement_plan",
                error_code="numeric_result_must_be_measurement",
                field_path=label,
                suggestion=(
                    "把主要或次要数值移入 measurement_plan，并由科研判据引用；"
                    "result_plan 中的数值或计数只用于不参与结论的诊断信息。"
                ),
            )
        if value_kind in {"number", "count"}:
            numeric_diagnostic_result_ids.add(result_id)
        _text(row["unit"], f"{label}.unit", minimum=0, maximum=100, preserve=True)
        result_meaning = _text(
            row["scientific_meaning"],
            f"{label}.scientific_meaning",
            maximum=1200,
        )
        if AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
            f"{result_display} {result_meaning}"
        ):
            raise ContractError(
                f"{label} uses ambiguous quality-flag inclusion language",
                error_code="ambiguous_quality_flag_language",
                field_path=label,
                suggestion=(
                    "明确写成“包含被标记观测的拟合”或“排除被标记观测的拟合”，"
                    "不要用“仅保留标记观测”表示保留正常观测。"
                ),
            )
        raw_reader_fields = CODE_LIKE_READER_IDENTIFIER.findall(
            f"{result_display} {result_meaning}"
        )
        if raw_reader_fields:
            raise ContractError(
                f"{label} exposes raw field or category names: "
                + ", ".join(sorted(set(raw_reader_fields))),
                error_code="reader_definition_exposes_raw_fields",
                field_path=label,
                suggestion=(
                    "显示名和释义会进入科研报告。改用自然语言科研名称；"
                    "原始列名和类别代码只用于实验代码。"
                ),
            )
    if not measurement_plan_rows and not result_plan_rows:
        raise ContractError(
            "design must plan at least one numeric measurement or typed result"
        )
    planned_p_value_labels = [
        str(row.get(field, ""))
        for row in [*measurement_plan_rows, *result_plan_rows]
        for field in ("name", "id", "display_name")
    ]
    if (
        any(P_VALUE_PLAN.search(label) for label in planned_p_value_labels)
        and P_VALUE_REQUEST.search(request["task"]) is None
    ):
        raise ContractError(
            "design adds an unrequested p-value or significance route",
            error_code="unrequested_inferential_metric",
            field_path="design.measurement_plan",
            suggestion=(
                "当前问题未要求显著性或假设检验；删除 p 值及相应端点、判据和结果，"
                "只保留问题要求的关联或估计量。"
            ),
        )

    method_decision_rows = _array(
        design["method_decisions"],
        "design.method_decisions",
        0,
        20,
    )
    decision_ids: set[str] = set()
    decision_keys: set[str] = set()
    for index, raw in enumerate(method_decision_rows):
        row = _object(raw, f"design.method_decisions[{index}]")
        label = f"design.method_decisions[{index}]"
        _exact(
            row,
            {
                "id",
                "decision_key",
                "decision",
                "rationale",
                "basis_kind",
                "source_refs",
                "alternatives",
                "claim_limit",
            },
            label,
        )
        decision_id = _safe_id(row["id"], f"{label}.id")
        if decision_id in decision_ids:
            raise ContractError(
                f"design.method_decisions has duplicate id: {decision_id}"
            )
        decision_ids.add(decision_id)
        decision_key = _safe_id(row["decision_key"], f"{label}.decision_key")
        if decision_key in decision_keys:
            raise ContractError(
                f"design.method_decisions has duplicate decision_key: {decision_key}"
            )
        decision_keys.add(decision_key)
        decision_text = _text(row["decision"], f"{label}.decision", maximum=1200)
        rationale_text = _text(row["rationale"], f"{label}.rationale", maximum=2000)
        basis_kind = _enum(
            row["basis_kind"],
            {
                "user_request",
                "located_source",
                "data_derived",
                "method_standard",
                "bounded_pragmatic_choice",
            },
            f"{label}.basis_kind",
        )
        source_refs = _text_array(row["source_refs"], f"{label}.source_refs", 20)
        alternatives = _text_array(row["alternatives"], f"{label}.alternatives", 8)
        claim_limit_text = _text(
            row["claim_limit"], f"{label}.claim_limit", maximum=1600
        )
        if AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
            " ".join((decision_text, rationale_text, claim_limit_text))
        ):
            raise ContractError(
                f"{label} uses ambiguous quality-flag inclusion language",
                error_code="ambiguous_quality_flag_language",
                field_path=label,
                suggestion=(
                    "方法说明须明确区分“包含被标记观测”和“排除被标记观测”，"
                    "不要把排除后保留的其余观测写成“仅保留标记观测”。"
                ),
            )
        if basis_kind == "bounded_pragmatic_choice" and not alternatives:
            raise ContractError(
                f"{label}.alternatives must list at least one alternative for a bounded pragmatic choice"
            )
        if basis_kind in {"located_source", "data_derived"} and not source_refs:
            raise ContractError(
                f"{label}.source_refs is required for the declared method-decision basis"
            )
    comparison_audits = _array(
        design["paired_comparison_audits"],
        "design.paired_comparison_audits",
        0,
        20,
    )
    comparison_ids: set[str] = set()
    comparison_measurements: set[str] = set()
    comparison_artifacts: set[str] = set()
    comparison_audit_semantics: list[dict[str, Any]] = []
    for index, raw in enumerate(comparison_audits):
        row = _object(raw, f"design.paired_comparison_audits[{index}]")
        label = f"design.paired_comparison_audits[{index}]"
        row.setdefault("row_filter", None)
        _exact(
            row,
            {
                "id",
                "comparison_kind",
                "evaluation_scope",
                "row_filter",
                "source_input_id",
                "source_row_id_column",
                "source_target_column",
                "source_baseline_column",
                "candidate_model_input_columns",
                "candidate_model_target_column",
                "baseline_model_input_columns",
                "baseline_model_target_column",
                "baseline_fit_condition",
                "candidate_fit_condition",
                "fit_evaluation_relation",
                "evaluation_target_usage",
                "evidence_artifact",
                "evidence_row_id_column",
                "evidence_target_column",
                "evidence_baseline_column",
                "evidence_candidate_column",
                "metric",
                "baseline_measurement",
                "candidate_measurement",
                "delta_measurement",
                "delta_formula",
            },
            label,
        )
        comparison_id = _safe_id(row["id"], f"{label}.id")
        if comparison_id in comparison_ids:
            raise ContractError(
                f"design.paired_comparison_audits has duplicate id: {comparison_id}"
            )
        comparison_ids.add(comparison_id)
        comparison_kind = _enum(
            row["comparison_kind"],
            {"source_baseline_vs_candidate", "candidate_vs_candidate"},
            f"{label}.comparison_kind",
        )
        evaluation_scope = _text(
            row["evaluation_scope"], f"{label}.evaluation_scope", maximum=2000
        )
        row_filter = _nullable_object(row["row_filter"], f"{label}.row_filter")
        if row_filter is not None:
            _exact(row_filter, {"column", "in"}, f"{label}.row_filter")
            _text(row_filter["column"], f"{label}.row_filter.column", maximum=200)
            _text_array(
                row_filter["in"], f"{label}.row_filter.in", 50, item_maximum=200
            )
            if not row_filter["in"]:
                raise ContractError(f"{label}.row_filter.in must not be empty")
        source_input_id = _safe_id(row["source_input_id"], f"{label}.source_input_id")
        if source_input_id not in input_ids:
            raise ContractError(
                f"{label}.source_input_id must reference design.input_ids"
            )
        for field in (
            "source_row_id_column",
            "source_target_column",
            "source_baseline_column",
            "candidate_model_target_column",
            "evidence_row_id_column",
            "evidence_target_column",
            "evidence_baseline_column",
            "evidence_candidate_column",
        ):
            _text(row[field], f"{label}.{field}", maximum=200)
        if row["source_target_column"] == row["source_baseline_column"]:
            raise ContractError(
                f"{label} source target and baseline columns must be distinct"
            )
        model_inputs = _text_array(
            row["candidate_model_input_columns"],
            f"{label}.candidate_model_input_columns",
            30,
            item_maximum=200,
        )
        if not model_inputs:
            raise ContractError(
                f"{label}.candidate_model_input_columns requires at least one column"
            )
        if row["candidate_model_target_column"] != row["source_target_column"]:
            raise ContractError(
                f"{label}.candidate_model_target_column must equal source_target_column"
            )
        if row["source_baseline_column"] not in model_inputs:
            raise ContractError(
                f"{label}.candidate_model_input_columns must include source_baseline_column"
            )
        if row["source_target_column"] in model_inputs:
            raise ContractError(
                f"{label}.candidate_model_input_columns must exclude source_target_column"
            )
        baseline_model_inputs = _text_array(
            row["baseline_model_input_columns"],
            f"{label}.baseline_model_input_columns",
            30,
            item_maximum=200,
        )
        baseline_model_target = _nullable_text(
            row["baseline_model_target_column"],
            f"{label}.baseline_model_target_column",
            maximum=200,
        )
        baseline_fit_condition = _nullable_text(
            row["baseline_fit_condition"],
            f"{label}.baseline_fit_condition",
            maximum=1000,
        )
        candidate_fit_condition = _text(
            row["candidate_fit_condition"],
            f"{label}.candidate_fit_condition",
            maximum=1000,
        )
        for field_name, field_value in (
            ("baseline_fit_condition", baseline_fit_condition),
            ("candidate_fit_condition", candidate_fit_condition),
        ):
            if field_value and AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(field_value):
                raise ContractError(
                    f"{label}.{field_name} uses ambiguous quality-flag language",
                    error_code="ambiguous_quality_flag_language",
                    field_path=f"{label}.{field_name}",
                    suggestion=(
                        "拟合条件必须明确写成包含或排除被标记观测；不要用“仅保留标记”"
                        "表示排除标记后保留其余观测。"
                    ),
                )
        if comparison_kind == "source_baseline_vs_candidate":
            if (
                baseline_model_inputs
                or baseline_model_target is not None
                or baseline_fit_condition is not None
            ):
                raise ContractError(
                    f"{label} source_baseline_vs_candidate requires empty/null baseline model fields"
                )
        else:
            if QUALITY_FLAG_EVALUATION_SCOPE.search(evaluation_scope):
                raise ContractError(
                    f"{label}.evaluation_scope mixes the fitted quality-flag condition into the evaluation rows",
                    error_code="evaluation_scope_mixes_fit_condition",
                    field_path=f"{label}.evaluation_scope",
                    suggestion=(
                        "两种拟合条件应在同一批固定留出观测上比较。evaluation_scope 只描述"
                        "这批评价观测；包含或排除被标记观测写在两个 fit_condition 字段中。"
                    ),
                )
            if not baseline_model_inputs:
                raise ContractError(
                    f"{label}.baseline_model_input_columns requires at least one column for candidate_vs_candidate"
                )
            if row["source_baseline_column"] not in baseline_model_inputs:
                raise ContractError(
                    f"{label}.baseline_model_input_columns must include source_baseline_column"
                )
            if row["source_target_column"] in baseline_model_inputs:
                raise ContractError(
                    f"{label}.baseline_model_input_columns must exclude source_target_column"
                )
            if baseline_model_target != row["source_target_column"]:
                raise ContractError(
                    f"{label}.baseline_model_target_column must equal source_target_column"
                )
            if baseline_fit_condition is None:
                raise ContractError(
                    f"{label}.baseline_fit_condition is required for candidate_vs_candidate"
                )
            if baseline_fit_condition == candidate_fit_condition:
                raise ContractError(
                    f"{label} candidate_vs_candidate fit conditions must be distinct"
                )
        if row["fit_evaluation_relation"] != "disjoint_rows":
            raise ContractError(
                f"{label}.fit_evaluation_relation must be disjoint_rows"
            )
        if row["evaluation_target_usage"] != "metrics_and_evidence_only":
            raise ContractError(
                f"{label}.evaluation_target_usage must be metrics_and_evidence_only"
            )
        evidence_columns = [
            row["evidence_row_id_column"],
            row["evidence_target_column"],
            row["evidence_baseline_column"],
            row["evidence_candidate_column"],
        ]
        if len(evidence_columns) != len(set(evidence_columns)):
            raise ContractError(f"{label} evidence column names must be distinct")
        evidence_artifact = _text(
            row["evidence_artifact"],
            f"{label}.evidence_artifact",
            maximum=500,
        )
        if (
            not evidence_artifact.endswith(".csv")
            or evidence_artifact.startswith("/")
            or "\\" in evidence_artifact
            or ".." in evidence_artifact.split("/")
        ):
            raise ContractError(
                f"{label}.evidence_artifact must be a safe relative CSV path"
            )
        comparison_artifacts.add(evidence_artifact)
        comparison_metric = _enum(
            row["metric"], PAIRED_COMPARISON_METRICS, f"{label}.metric"
        )
        baseline_measurement = _safe_ref(
            row["baseline_measurement"],
            f"{label}.baseline_measurement",
        )
        candidate_measurement = _safe_ref(
            row["candidate_measurement"],
            f"{label}.candidate_measurement",
        )
        if baseline_measurement == candidate_measurement:
            raise ContractError(
                f"{label} baseline_measurement and candidate_measurement must differ"
            )
        if not _measurement_supports_metric(
            baseline_measurement, comparison_metric
        ) or not _measurement_supports_metric(candidate_measurement, comparison_metric):
            raise ContractError(
                f"{label} baseline and candidate measurement names must match "
                f"the declared {comparison_metric} metric semantics"
            )
        comparison_measurements.update({baseline_measurement, candidate_measurement})
        delta_measurement = _nullable_text(
            row["delta_measurement"],
            f"{label}.delta_measurement",
            maximum=128,
        )
        delta_formula = _nullable_text(
            row["delta_formula"],
            f"{label}.delta_formula",
            maximum=100,
        )
        if (delta_measurement is None) != (delta_formula is None):
            raise ContractError(
                f"{label}.delta_measurement and delta_formula must both be null or both be set"
            )
        if delta_measurement is not None:
            delta_measurement = _safe_ref(
                delta_measurement,
                f"{label}.delta_measurement",
            )
            _enum(
                delta_formula,
                PAIRED_COMPARISON_DELTA_FORMULAS,
                f"{label}.delta_formula",
            )
            comparison_measurements.add(delta_measurement)
            delta_plan = measurement_plan_by_name.get(delta_measurement, {})
            delta_reader_text = " ".join(
                (
                    str(delta_plan.get("display_name", "")),
                    str(delta_plan.get("scientific_meaning", "")),
                )
            )
            if (
                re.search(
                    r"(?:差值|差异|变化量|改善量|改进量|delta|difference|contrast|improvement)",
                    delta_reader_text,
                    re.IGNORECASE,
                )
                is None
                or re.search(
                    r"(?:减去|相减|减|minus|subtract(?:ed|ion)?)",
                    delta_reader_text,
                    re.IGNORECASE,
                )
                is None
            ):
                raise ContractError(
                    f"{label}.delta_measurement must describe the metric subtraction and its direction",
                    error_code="delta_measurement_semantics_incomplete",
                    field_path=f"design.measurement_plan[{delta_measurement}]",
                    suggestion=(
                        "差值测量的名称要明确写成某指标的差值或改善量；科学释义必须"
                        "用自然语言写清哪一条件减哪一条件，并与 delta_formula 一致。"
                        "不能把两个预测值的平均绝对差冒充两项误差指标之差。"
                    ),
                )
            if comparison_metric in {"mae", "rmse"} and _loss_delta_direction_conflicts(
                delta_reader_text,
                str(delta_formula),
            ):
                raise ContractError(
                    f"{label}.delta_measurement sign interpretation contradicts delta_formula",
                    error_code="delta_direction_interpretation_conflict",
                    field_path=f"design.measurement_plan[{delta_measurement}]",
                    suggestion=(
                        "MAE/RMSE 越低越好。按 delta_formula 逐项核对正负号；"
                        "不需要解释方向时只陈述相减顺序，不写正值或负值代表什么。"
                    ),
                )
        comparison_audit_semantics.append(
            {
                "id": comparison_id,
                "comparison_kind": comparison_kind,
                "source_input_id": source_input_id,
                "source_target_column": row["source_target_column"],
                "source_baseline_column": row["source_baseline_column"],
                "metric": comparison_metric,
                "baseline_measurement": baseline_measurement,
                "candidate_measurement": candidate_measurement,
                "delta_measurement": delta_measurement,
                "candidate_model_input_columns": model_inputs,
                "candidate_model_target_column": row["candidate_model_target_column"],
                "baseline_model_input_columns": baseline_model_inputs,
                "baseline_model_target_column": baseline_model_target,
                "baseline_fit_condition": baseline_fit_condition,
                "candidate_fit_condition": candidate_fit_condition,
                "evaluation_scope": evaluation_scope,
            }
        )

        def condition_tags(name: str) -> set[str]:
            planned = measurement_plan_by_name.get(name, {})
            normalized = " ".join(
                (
                    str(planned.get("display_name", "")),
                    str(planned.get("scientific_meaning", "")),
                )
            ).lower()
            tags: set[str] = set()
            tag_patterns = {
                "included": r"\b(?:with|include|included)\b|包含|纳入",
                "excluded": r"\b(?:without|exclude|excluded|filtered)\b|排除|剔除",
                "complete": r"\b(?:all|full|complete|unfiltered)\b|全部|完整",
                "perturbed": r"\b(?:perturbed|sensitivity)\b|扰动条件",
            }
            for tag, pattern in tag_patterns.items():
                if re.search(pattern, normalized):
                    tags.add(tag)
            return tags

        baseline_condition_tags = condition_tags(baseline_measurement)
        candidate_condition_tags = condition_tags(candidate_measurement)
        baseline_plan = measurement_plan_by_name.get(baseline_measurement, {})
        baseline_reader_text = " ".join(
            (
                baseline_measurement,
                str(baseline_plan.get("display_name", "")),
                str(baseline_plan.get("scientific_meaning", "")),
            )
        )
        baseline_is_uncalibrated_source = bool(
            UNCALIBRATED_BASELINE_LANGUAGE.search(baseline_reader_text)
        )
        if baseline_is_uncalibrated_source:
            baseline_condition_tags = set()
            if comparison_kind == "candidate_vs_candidate":
                raise ContractError(
                    f"{label} an uncalibrated source baseline cannot be treated as a fitted candidate",
                    error_code="raw_baseline_must_use_source_comparison",
                    field_path=f"{label}.comparison_kind",
                    suggestion=(
                        "原始或未校准读数与拟合后预测的比较应使用 "
                        "source_baseline_vs_candidate，并把 baseline_model_input_columns "
                        "设为空数组、baseline_model_target_column 与 "
                        "baseline_fit_condition 设为 null。candidate_vs_candidate 仅用于"
                        "两套实际拟合模型在同一批评价观测上的比较。"
                    ),
                )
        compares_distinct_conditions = (
            bool(baseline_condition_tags)
            and bool(candidate_condition_tags)
            and baseline_condition_tags != candidate_condition_tags
        )
        if compares_distinct_conditions and comparison_kind != "candidate_vs_candidate":
            raise ContractError(
                f"{label} condition-comparison measurements require candidate_vs_candidate"
            )
    source_condition_audits = [
        row
        for row in comparison_audit_semantics
        if row["comparison_kind"] == "source_baseline_vs_candidate"
    ]
    for condition_audit in comparison_audit_semantics:
        if condition_audit["comparison_kind"] != "candidate_vs_candidate":
            continue
        for side in ("baseline", "candidate"):
            fitted_condition = condition_audit[f"{side}_fit_condition"]
            measurement = condition_audit[f"{side}_measurement"]
            for source_audit in source_condition_audits:
                same_problem = all(
                    condition_audit[field] == source_audit[field]
                    for field in (
                        "source_input_id",
                        "source_target_column",
                        "source_baseline_column",
                        "metric",
                    )
                )
                if not same_problem or not _same_fitted_condition(
                    fitted_condition,
                    source_audit["candidate_fit_condition"],
                ):
                    continue
                if measurement != source_audit["candidate_measurement"]:
                    raise ContractError(
                        "one fitted condition is declared under two measurement names",
                        error_code="duplicate_paired_measurement_alias",
                        field_path="design.paired_comparison_audits",
                        suggestion=(
                            "同一模型、同一拟合条件和同一评价观测的指标只能声明一次。"
                            "在条件敏感性比较中复用已有 candidate_measurement，不要为同一数值"
                            "另建别名；相应删除重复 measurement_plan 项。"
                        ),
                    )
                if (
                    condition_audit["evaluation_scope"].strip().casefold()
                    != source_audit["evaluation_scope"].strip().casefold()
                ):
                    raise ContractError(
                        "linked fitted-condition comparisons must use one identical evaluation scope",
                        error_code="paired_evaluation_scope_mismatch",
                        field_path="design.paired_comparison_audits",
                        suggestion=(
                            "复用同一条只描述评价观测的 evaluation_scope；拟合条件只写在"
                            " baseline_fit_condition 和 candidate_fit_condition 中。"
                        ),
                    )
    source_audits_by_candidate = {
        row["candidate_measurement"]: row
        for row in comparison_audit_semantics
        if row["comparison_kind"] == "source_baseline_vs_candidate"
    }
    for row in comparison_audit_semantics:
        if row["comparison_kind"] != "candidate_vs_candidate":
            continue
        baseline_source = source_audits_by_candidate.get(row["baseline_measurement"])
        candidate_source = source_audits_by_candidate.get(row["candidate_measurement"])
        if not baseline_source or not candidate_source:
            continue
        scopes = {
            str(item["evaluation_scope"]).strip().casefold()
            for item in (row, baseline_source, candidate_source)
        }
        if len(scopes) != 1:
            raise ContractError(
                "linked fitted-condition comparisons must use one identical evaluation scope",
                error_code="paired_evaluation_scope_mismatch",
                field_path="design.paired_comparison_audits",
                suggestion=(
                    "三项比较都填写同一条只描述评价观测的 evaluation_scope，例如"
                    "“相同的时间顺序留出观测”；保留或排除标记观测属于拟合条件，"
                    "应只写在 baseline_fit_condition 和 candidate_fit_condition 中。"
                ),
            )
    direction_conflict = _reader_model_direction_conflict(
        measurement_plan_rows,
        comparison_audit_semantics,
    )
    if direction_conflict:
        raise ContractError(
            "a reader-facing slope definition reverses the declared model input and target: "
            f"{direction_conflict}",
            error_code="reader_model_direction_conflict",
            field_path=f"design.measurement_plan[{direction_conflict}].scientific_meaning",
            suggestion=(
                "若模型把候选读数映射到参考读数，斜率应表述为参考读数相对于"
                "候选读数的回归斜率；同时核对方法、代码和成对证据中的输入与目标方向。"
            ),
        )
    criteria = _array(design["criteria"], "design.criteria", 1, 30)
    all_criterion_measurement_refs = {
        ref
        for raw_criterion in criteria
        if isinstance(raw_criterion, dict)
        for ref in raw_criterion.get("measurement_refs", [])
        if isinstance(ref, str)
    }
    criterion_ids: set[str] = set()
    planned_measurements: set[str] = set()
    planned_results: set[str] = set()
    for index, raw in enumerate(criteria):
        row = _object(raw, f"design.criteria[{index}]")
        label = f"design.criteria[{index}]"
        _exact(
            row,
            {
                "id",
                "statement",
                "basis_kind",
                "basis_text",
                "source_refs",
                "artifact_refs",
                "measurement_refs",
                "result_refs",
                "endpoint_refs",
            },
            label,
        )
        criterion_id = _safe_id(row["id"], f"{label}.id")
        if criterion_id in criterion_ids:
            raise ContractError(f"design.criteria has duplicate id: {criterion_id}")
        criterion_ids.add(criterion_id)
        statement = _text(row["statement"], f"{label}.statement")
        basis_kind = _enum(row["basis_kind"], BASIS_KINDS, f"{label}.basis_kind")
        basis_text = _text(row["basis_text"], f"{label}.basis_text")
        _reject_reader_internal_terms(
            label,
            statement=statement,
            basis_text=basis_text,
        )
        source_refs = _text_array(row["source_refs"], f"{label}.source_refs", 20)
        _text_array(row["artifact_refs"], f"{label}.artifact_refs", 20)
        measurement_refs = _array(
            row["measurement_refs"], f"{label}.measurement_refs", 0, 30
        )
        normalized_measurements = [
            _safe_ref(value, f"{label}.measurement_refs[{item_index}]")
            for item_index, value in enumerate(measurement_refs)
        ]
        if len(normalized_measurements) != len(set(normalized_measurements)):
            raise ContractError(f"{label}.measurement_refs must contain unique values")
        planned_measurements.update(normalized_measurements)
        result_refs = _array(row["result_refs"], f"{label}.result_refs", 0, 30)
        normalized_results = [
            _safe_ref(value, f"{label}.result_refs[{item_index}]")
            for item_index, value in enumerate(result_refs)
        ]
        if len(normalized_results) != len(set(normalized_results)):
            raise ContractError(f"{label}.result_refs must contain unique values")
        unknown_results = sorted(set(normalized_results) - result_plan_ids)
        if unknown_results:
            raise ContractError(
                f"{label}.result_refs references unknown typed results: {unknown_results}"
            )
        planned_results.update(normalized_results)
        endpoint_refs = _array(row["endpoint_refs"], f"{label}.endpoint_refs", 0, 20)
        normalized_endpoints = [
            _safe_id(value, f"{label}.endpoint_refs[{item_index}]")
            for item_index, value in enumerate(endpoint_refs)
        ]
        if len(normalized_endpoints) != len(set(normalized_endpoints)):
            raise ContractError(f"{label}.endpoint_refs must contain unique values")
        if (
            not normalized_measurements
            and not normalized_results
            and not normalized_endpoints
        ):
            raise ContractError(
                f"{label} must reference at least one planned measurement, typed result, or endpoint"
            )
        criterion_semantics = " ".join((criterion_id, statement, basis_text))
        condition_refs, delta_refs = _sensitivity_criterion_roles(
            normalized_measurements,
            measurement_plan_by_name,
        )
        requires_condition_contrast = bool(
            (
                SENSITIVITY_CONTEXT.search(criterion_semantics)
                and _has_explicit_condition_measurement(
                    normalized_measurements, measurement_plan_by_name
                )
            )
            or (
                MULTI_CONDITION_CONTEXT.search(criterion_semantics)
                and CONTRAST_PROMISE.search(criterion_semantics)
            )
        )
        if requires_condition_contrast:
            # A report may use one criterion for the two condition estimates and
            # a neighboring criterion for their declared difference. Treat that
            # as one closed comparison when the paired audit links the exact
            # three measurements; do not force redundant criterion wording.
            condition_refs, delta_refs = _linked_sensitivity_roles(
                condition_refs,
                delta_refs,
                all_criterion_measurement_refs,
                comparison_audit_semantics,
            )
            if len(condition_refs) < 2 or not delta_refs:
                raise ContractError(
                    f"{label} sensitivity criterion must cite both condition estimates and their difference",
                    error_code="sensitivity_criterion_incomplete",
                    field_path=f"{label}.measurement_refs",
                    suggestion=(
                        "敏感性判据必须同时引用条件 A、条件 B 和二者差值，不能只引用"
                        "一个或多个变化量。"
                    ),
                )
            for delta_ref in delta_refs:
                delta_unit = str(
                    measurement_plan_by_name.get(delta_ref, {}).get("unit", "")
                )
                same_unit_conditions = [
                    ref
                    for ref in condition_refs
                    if str(measurement_plan_by_name.get(ref, {}).get("unit", ""))
                    == delta_unit
                ]
                if len(same_unit_conditions) < 2:
                    raise ContractError(
                        f"{label} sensitivity difference lacks two same-unit condition estimates",
                        error_code="sensitivity_unit_mismatch",
                        field_path=f"{label}.measurement_refs",
                        suggestion=(
                            "每个差值分别引用同单位、同统计口径的条件 A 与条件 B；"
                            "一条判据可以包含多组不同单位的参数比较。"
                        ),
                    )
        elif (
            CONTRAST_PROMISE.search(statement)
            and len(normalized_measurements) >= 2
            and not delta_refs
        ):
            raise ContractError(
                f"{label} promises a difference but cites no difference measurement",
                error_code="contrast_measurement_missing",
                field_path=f"{label}.measurement_refs",
                suggestion=(
                    "若结论需要差值，在 measurement_plan 中声明该差值并由判据引用；"
                    "否则删去“差值”表述，只分别报告两侧估计量。"
                ),
            )
        if re.search(
            r"\b(?:difference|contrast)\b|差值|差异|二者之差|条件间",
            statement,
            re.IGNORECASE,
        ):
            source_delta_audits = [
                audit
                for audit in comparison_audit_semantics
                if audit["comparison_kind"] == "source_baseline_vs_candidate"
                and audit["delta_measurement"] in normalized_measurements
            ]
            if len(source_delta_audits) >= 2:
                condition_measurements = {
                    audit["candidate_measurement"] for audit in source_delta_audits
                }
                has_condition_audit = any(
                    audit["comparison_kind"] == "candidate_vs_candidate"
                    and {
                        audit["baseline_measurement"],
                        audit["candidate_measurement"],
                    }
                    == condition_measurements
                    and audit["delta_measurement"] in normalized_measurements
                    for audit in comparison_audit_semantics
                )
                if not has_condition_audit:
                    raise ContractError(
                        f"{label} compares fitted-condition effects without a same-row condition audit",
                        error_code="sensitivity_condition_comparison_unaudited",
                        field_path=f"{label}.measurement_refs",
                        suggestion=(
                            "若比较两种拟合条件，请增加 candidate_vs_candidate 成对审计，"
                            "让两套模型在同一批留出行上计算条件 A、条件 B 与二者差值；"
                            "不得直接相减来自不同留出样本的性能量。"
                        ),
                    )
        _validate_numeric_cutoff_basis(
            statement, basis_kind, basis_text, label, source_refs
        )
        if (
            HARD_NUMERIC_CUTOFF.search(statement) is not None
            and basis_kind in {"located_source", "data_derived"}
            and not set(source_refs).issubset(request_input_ids)
        ):
            raise ContractError(
                f"{label} numeric cutoff source_refs must identify supplied inputs",
                error_code="numeric_cutoff_source_not_supplied",
                field_path=f"{label}.source_refs",
                suggestion=(
                    "当前 Agent 只能核对本次任务已提供的材料。用 input_refs 中的 id "
                    "指向阈值的资料或数据；否则删除该硬阈值。"
                ),
            )
    fit_quality_measurements = {
        str(row["name"])
        for row in measurement_plan_rows
        if R_SQUARED_PLAN.search(
            " ".join(
                str(row.get(field, ""))
                for field in ("name", "display_name", "scientific_meaning")
            )
        )
    }
    fit_quality_results = {
        str(row["id"])
        for row in result_plan_rows
        if R_SQUARED_PLAN.search(
            " ".join(
                str(row.get(field, ""))
                for field in ("id", "display_name", "scientific_meaning")
            )
        )
    }
    if (fit_quality_measurements or fit_quality_results) and R_SQUARED_PLAN.search(
        request["task"]
    ) is None:
        located_basis_refs = {
            ref
            for row in criteria
            if row.get("basis_kind") == "located_source" and row.get("source_refs")
            for ref in [
                *row.get("measurement_refs", []),
                *row.get("result_refs", []),
            ]
        }
        ungrounded_fit_quality = sorted(
            (fit_quality_measurements | fit_quality_results) - located_basis_refs
        )
        if ungrounded_fit_quality:
            raise ContractError(
                "design adds an unrequested coefficient-of-determination route: "
                + ", ".join(ungrounded_fit_quality),
                error_code="unrequested_fit_quality_diagnostic",
                field_path="design.result_plan",
                suggestion=(
                    "当前问题没有要求决定系数或拟合优度，也没有已定位来源要求该诊断。"
                    "删除这些输出及为其新增的判据；只保留直接回答研究问题的误差、"
                    "参数和条件差值。"
                ),
            )
    orphan_numeric_results = sorted(numeric_diagnostic_result_ids - planned_results)
    if orphan_numeric_results:
        raise ContractError(
            "numeric diagnostic results are not used by any scientific criterion: "
            + ", ".join(orphan_numeric_results),
            error_code="planned_result_not_criterion_bound",
            field_path="design.result_plan",
            suggestion=(
                "删除未参与研究判断的数值或计数诊断项；不要仅为保留这些输出而新增"
                "无关判据。若某项确实回答研究问题，应将其移入 measurement_plan "
                "并由相应科研判据引用。"
            ),
        )
    stage_rows = _array(
        design["experiment_stages"],
        "design.experiment_stages",
        1,
        min(5, request["run_budget"]["max_stages"]),
    )
    stages: list[dict[str, Any]] = []
    stage_ids: list[str] = []
    for index, raw in enumerate(stage_rows):
        stage = _object(raw, f"design.experiment_stages[{index}]")
        stage_id = _safe_id(stage.get("id"), f"design.experiment_stages[{index}].id")
        if stage_id in stage_ids:
            raise ContractError(
                f"design.experiment_stages has duplicate id: {stage_id}"
            )
        stages.append(stage)
        stage_ids.append(stage_id)
    stage_index = {stage_id: index for index, stage_id in enumerate(stage_ids)}
    summaries = {
        "response.design_summary": response["design_summary"],
        "design.design_summary": design["design_summary"],
    }
    for label, summary_text in summaries.items():
        for match in STAGE_COUNT_DECLARATION.finditer(summary_text):
            token = match.group("cjk") or match.group("english") or match.group("digit")
            declared = (
                int(token) if token.isdigit() else STAGE_COUNT_WORDS[token.lower()]
            )
            if declared != len(stages):
                raise ContractError(
                    f"{label} declares {declared} stages but design.experiment_stages "
                    f"contains {len(stages)}",
                    error_code="stage_count_summary_mismatch",
                    field_path=label,
                    suggestion=(
                        "让设计摘要中的阶段数量与实际阶段图一致；如果合并了步骤，"
                        "应同时改写摘要，不得保留旧的阶段数。"
                    ),
                )

    artifact_rows = _array(
        design["artifact_plan"],
        "design.artifact_plan",
        0,
        100,
    )
    artifact_ids: set[str] = set()
    artifact_paths: set[str] = set()
    artifact_by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(artifact_rows):
        row = _object(raw, f"design.artifact_plan[{index}]")
        label = f"design.artifact_plan[{index}]"
        _exact(
            row,
            {"id", "path", "kind", "description", "producer_stage_id"},
            label,
        )
        artifact_id = _safe_id(row["id"], f"{label}.id")
        if artifact_id in artifact_ids:
            raise ContractError(f"design.artifact_plan has duplicate id: {artifact_id}")
        artifact_ids.add(artifact_id)
        path = _text(row["path"], f"{label}.path", maximum=500)
        if path.startswith("/") or "\\" in path or ".." in path.split("/"):
            raise ContractError(f"{label}.path must use a safe relative POSIX path")
        if path.split("/", 1)[0].casefold() in {"output", "outputs"}:
            raise ContractError(
                f"{label}.path is already relative to the sandbox output root; "
                "do not prefix it with output/ or outputs/",
                error_code="artifact_output_prefix_redundant",
                field_path=f"{label}.path",
                suggestion=(
                    "删除 output/ 或 outputs/ 前缀，例如把 outputs/result.json "
                    "改为 result.json；代码应写入 context['output_dir'] 下的同一路径。"
                ),
            )
        artifact_name = path.rstrip("/").rsplit("/", 1)[-1].casefold()
        if artifact_name in RESERVED_RUNTIME_ARTIFACT_NAMES:
            raise ContractError(
                f"{label}.path cannot use reserved {artifact_name}",
                field_path=f"{label}.path",
                suggestion=(
                    "实验阶段只生成数据、图表或科研中间产物；正式报告、审计和运行状态"
                    "由现有汇报器生成，不得在沙箱内重复创建。"
                ),
            )
        if path in artifact_paths:
            raise ContractError(f"design.artifact_plan has duplicate path: {path}")
        artifact_paths.add(path)
        _enum(
            row["kind"],
            {
                "json",
                "csv",
                "text",
                "markdown",
                "image",
                "fits",
                "netcdf",
                "hdf5",
                "parquet",
                "other",
            },
            f"{label}.kind",
        )
        _text(row["description"], f"{label}.description", maximum=1000)
        producer = _safe_id(row["producer_stage_id"], f"{label}.producer_stage_id")
        if producer not in stage_index:
            raise ContractError(
                f"{label}.producer_stage_id references an unknown stage"
            )
        artifact_by_id[artifact_id] = row

    expected_artifacts: set[str] = set()
    produced_ids: set[str] = set()
    targeted_stage_ids: set[str] = set()
    endpoint_ids: set[str] = set()
    produced_measurement_names: set[str] = set()
    produced_result_ids: set[str] = set()
    for index, stage in enumerate(stages):
        label = f"design.experiment_stages[{index}]"
        _exact(
            stage,
            {
                "id",
                "objective",
                "input_ids",
                "consumes_artifact_ids",
                "produces_artifact_ids",
                "prerequisite_stage_ids",
                "join_policy",
                "method_outline",
                "measurement_refs",
                "result_refs",
                "endpoint_ids",
                "criterion_refs",
                "outcome_rules",
                "transitions",
                "execution",
            },
            label,
        )
        stage_objective = _text(stage["objective"], f"{label}.objective", maximum=2000)
        stage_input_ids = _text_array(stage["input_ids"], f"{label}.input_ids", 50)
        if not set(stage_input_ids).issubset(set(input_ids)):
            raise ContractError(f"{label}.input_ids references an unknown design input")
        prerequisites = [
            _safe_id(value, f"{label}.prerequisite_stage_ids[{item_index}]")
            for item_index, value in enumerate(
                _array(
                    stage["prerequisite_stage_ids"],
                    f"{label}.prerequisite_stage_ids",
                    0,
                    5,
                )
            )
        ]
        if len(prerequisites) != len(set(prerequisites)):
            raise ContractError(f"{label}.prerequisite_stage_ids must be unique")
        for prerequisite in prerequisites:
            if prerequisite not in stage_index or stage_index[prerequisite] >= index:
                raise ContractError(
                    f"{label}.prerequisite_stage_ids must reference earlier stages"
                )
        if index == 0 and prerequisites:
            raise ContractError("the first experiment stage cannot have prerequisites")
        _enum(stage["join_policy"], {"all", "any"}, f"{label}.join_policy")
        method_outline = _text(
            stage["method_outline"],
            f"{label}.method_outline",
            maximum=3000,
        )
        if AMBIGUOUS_FLAGGED_RETENTION_LANGUAGE.search(
            f"{stage_objective} {method_outline}"
        ):
            raise ContractError(
                f"{label} uses ambiguous quality-flag inclusion language",
                error_code="ambiguous_quality_flag_language",
                field_path=f"{label}.method_outline",
                suggestion=(
                    "明确写成包含或排除被标记观测的拟合；评价范围应另写为同一批固定"
                    "留出观测。"
                ),
            )
        code_identifiers = [
            match.group(0)
            for match in CODE_LIKE_READER_IDENTIFIER.finditer(method_outline)
            if len(match.group(0)) > 4
        ]
        if code_identifiers:
            raise ContractError(
                f"{label}.method_outline exposes raw field names: "
                + ", ".join(sorted(set(code_identifiers))),
                error_code="reader_method_exposes_raw_fields",
                field_path=f"{label}.method_outline",
                suggestion=(
                    "方法说明会进入用户报告。把原始列名和类别代码改写为已经定义的"
                    "科研名称，例如“候选仪器读数”“参考读数”“质量标记”；字段名只"
                    "用于后续实验代码。"
                ),
            )

        consumes = [
            _safe_id(value, f"{label}.consumes_artifact_ids[{item_index}]")
            for item_index, value in enumerate(
                _array(
                    stage["consumes_artifact_ids"],
                    f"{label}.consumes_artifact_ids",
                    0,
                    100,
                )
            )
        ]
        produces = [
            _safe_id(value, f"{label}.produces_artifact_ids[{item_index}]")
            for item_index, value in enumerate(
                _array(
                    stage["produces_artifact_ids"],
                    f"{label}.produces_artifact_ids",
                    0,
                    100,
                )
            )
        ]
        if len(consumes) != len(set(consumes)) or len(produces) != len(set(produces)):
            raise ContractError(f"{label} artifact references must be unique")
        if not set(consumes + produces).issubset(artifact_ids):
            raise ContractError(f"{label} references an unknown artifact id")
        for artifact_id in consumes:
            producer = artifact_by_id[artifact_id]["producer_stage_id"]
            if stage_index[producer] >= index:
                raise ContractError(
                    f"{label}.consumes_artifact_ids must reference artifacts from earlier stages"
                )
        for artifact_id in produces:
            if artifact_by_id[artifact_id]["producer_stage_id"] != stage["id"]:
                raise ContractError(
                    f"{label}.produces_artifact_ids disagrees with artifact_plan producer"
                )
            if artifact_id in produced_ids:
                raise ContractError(
                    f"artifact has more than one producer: {artifact_id}"
                )
            produced_ids.add(artifact_id)

        stage_measurements = [
            _safe_ref(value, f"{label}.measurement_refs[{item_index}]")
            for item_index, value in enumerate(
                _array(stage["measurement_refs"], f"{label}.measurement_refs", 0, 200)
            )
        ]
        if not set(stage_measurements).issubset(measurement_plan_names):
            raise ContractError(
                f"{label}.measurement_refs references an unknown measurement"
            )
        repeated_measurements = sorted(
            produced_measurement_names.intersection(stage_measurements)
        )
        if repeated_measurements:
            raise ContractError(
                f"measurements must have one producing stage: {repeated_measurements}",
                field_path=f"{label}.measurement_refs",
                suggestion=(
                    "同一测量只能由一个阶段产生。若后续阶段需要该结果，前一阶段应生成"
                    "只读 Artifact 并由后续阶段显式消费；若无需传递数据，则合并阶段。"
                ),
            )
        produced_measurement_names.update(stage_measurements)
        stage_results = [
            _safe_ref(value, f"{label}.result_refs[{item_index}]")
            for item_index, value in enumerate(
                _array(stage["result_refs"], f"{label}.result_refs", 0, 100)
            )
        ]
        if not set(stage_results).issubset(result_plan_ids):
            raise ContractError(
                f"{label}.result_refs references an unknown typed result"
            )
        repeated_results = sorted(produced_result_ids.intersection(stage_results))
        if repeated_results:
            raise ContractError(
                f"typed results must have one producing stage: {repeated_results}",
                field_path=f"{label}.result_refs",
                suggestion=(
                    "同一定性或离散结果只能由一个阶段产生；需要复核时使用新的结果标识，"
                    "并说明与前一结果的关系。"
                ),
            )
        produced_result_ids.update(stage_results)
        stage_endpoints = [
            _safe_id(value, f"{label}.endpoint_ids[{item_index}]")
            for item_index, value in enumerate(
                _array(stage["endpoint_ids"], f"{label}.endpoint_ids", 0, 100)
            )
        ]
        if endpoint_ids.intersection(stage_endpoints):
            raise ContractError("endpoint ids must be unique across experiment stages")
        endpoint_ids.update(stage_endpoints)
        stage_criteria = [
            _safe_id(value, f"{label}.criterion_refs[{item_index}]")
            for item_index, value in enumerate(
                _array(stage["criterion_refs"], f"{label}.criterion_refs", 0, 30)
            )
        ]
        if not set(stage_criteria).issubset(criterion_ids):
            raise ContractError(
                f"{label}.criterion_refs references an unknown criterion"
            )
        if (
            not stage_measurements
            and not stage_results
            and not stage_endpoints
            and not produces
        ):
            raise ContractError(f"{label} must produce at least one reviewable result")

        outcome_rules = _object(stage["outcome_rules"], f"{label}.outcome_rules")
        _exact(outcome_rules, STAGE_OUTCOMES, f"{label}.outcome_rules")
        transitions = _object(stage["transitions"], f"{label}.transitions")
        _exact(transitions, STAGE_OUTCOMES, f"{label}.transitions")
        for outcome in sorted(STAGE_OUTCOMES):
            _text(outcome_rules[outcome], f"{label}.outcome_rules.{outcome}")
            target = _text(
                transitions[outcome],
                f"{label}.transitions.{outcome}",
                maximum=128,
            )
            if target in stage_index:
                if stage_index[target] <= index:
                    raise ContractError(
                        f"{label}.transitions.{outcome} must target a later stage"
                    )
                targeted_stage_ids.add(target)
            elif target not in TERMINAL_STAGE_TARGETS:
                raise ContractError(
                    f"{label}.transitions.{outcome} targets an unknown stage or terminal outcome"
                )

        execution = _object(stage["execution"], f"{label}.execution")
        _exact(
            execution,
            {
                "entry_file",
                "dependencies",
                "deterministic",
                "seed",
                "expected_artifacts",
            },
            f"{label}.execution",
        )
        if (
            _text(execution["entry_file"], f"{label}.execution.entry_file", maximum=100)
            != "experiment.py"
        ):
            raise ContractError(f"{label}.execution.entry_file must be experiment.py")
        _text_array(
            execution["dependencies"], f"{label}.execution.dependencies", 20, 100
        )
        _boolean(execution["deterministic"], f"{label}.execution.deterministic")
        _integer(execution["seed"], f"{label}.execution.seed", 0, 2**31 - 1)
        stage_expected = set(
            _text_array(
                execution["expected_artifacts"],
                f"{label}.execution.expected_artifacts",
                50,
                500,
            )
        )
        planned_paths = {artifact_by_id[item]["path"] for item in produces}
        if stage_expected != planned_paths:
            raise ContractError(
                f"{label}.execution.expected_artifacts must exactly match its produced artifacts"
            )
        expected_artifacts.update(stage_expected)

    if produced_ids != artifact_ids:
        missing = sorted(artifact_ids - produced_ids)
        raise ContractError(
            f"design.artifact_plan contains unproduced artifacts: {missing}"
        )
    if set(stage_ids[1:]) != targeted_stage_ids:
        missing = sorted(set(stage_ids[1:]) - targeted_stage_ids)
        raise ContractError(
            f"design.experiment_stages contains unreachable stages: {missing}"
        )
    missing_comparison_artifacts = sorted(comparison_artifacts - expected_artifacts)
    if missing_comparison_artifacts:
        raise ContractError(
            "design.paired_comparison_audits evidence artifacts must be expected: "
            f"{missing_comparison_artifacts}"
        )
    unplanned_comparison_measurements = sorted(
        comparison_measurements - planned_measurements
    )
    if unplanned_comparison_measurements:
        raise ContractError(
            "design.paired_comparison_audits measurements must be referenced by criteria: "
            f"{unplanned_comparison_measurements}",
            error_code="paired_measurement_not_criterion_bound",
            field_path="design.criteria[*].measurement_refs",
            suggestion=(
                "每个成对比较的 baseline_measurement、candidate_measurement 和"
                " delta_measurement 都必须在 measurement_plan 中声明，并至少由一个"
                " criterion 的 measurement_refs 引用。"
            ),
        )
    if measurement_plan_names:
        missing_parameter_differences = _missing_requested_parameter_differences(
            request["task"],
            measurement_plan_rows,
        )
        if missing_parameter_differences:
            raise ContractError(
                "the request asks for condition-specific calibration-parameter differences, "
                "but the plan omits: " + ", ".join(missing_parameter_differences),
                error_code="requested_parameter_difference_missing",
                field_path="design.measurement_plan",
                suggestion=(
                    "已分别计划两种条件的参数时，还要为用户明确要求的每个参数差异"
                    "声明同单位差值，并由科研判据引用；若用户未要求参数差异，则不要"
                    "额外添加。"
                ),
            )
        unplanned_criteria_measurements = sorted(
            planned_measurements - measurement_plan_names
        )
        if unplanned_criteria_measurements:
            raise ContractError(
                "design.criteria measurement_refs must be declared in measurement_plan: "
                f"{unplanned_criteria_measurements}",
                error_code="criterion_measurement_not_planned",
                field_path="design.measurement_plan",
                suggestion=(
                    "补齐这些 measurement_plan 项的 name、中文 display_name、role、"
                    "unit、scientific_meaning；随后同时检查每个成对比较三项测量均已"
                    "计划、每个计划测量均被判据引用，并把平均有符号误差命名为"
                    " signed_error 而不是 mse。"
                ),
            )
        unreferenced_measurements = sorted(
            measurement_plan_names - planned_measurements
        )
        if unreferenced_measurements:
            raise ContractError(
                "design.measurement_plan entries must be referenced by at least one criterion: "
                f"{unreferenced_measurements}",
                error_code="planned_measurement_not_criterion_bound",
                field_path="design.criteria[*].measurement_refs",
                suggestion=(
                    "若这些诊断量直接回答预设判据，将其加入相应 measurement_refs；"
                    "否则从 measurement_plan 和实验输出中删除。不要为保留无关诊断量"
                    "而新造判据。完成后检查 interpretation_policy 不含无来源数值阈值。"
                ),
            )
    paired_measurements = _paired_measurements_requiring_audit(
        planned_measurements,
        measurement_plan_by_name,
    )
    uncovered_paired_measurements = sorted(
        paired_measurements - comparison_measurements
    )
    if uncovered_paired_measurements:
        raise ContractError(
            "named raw/calibrated improvement triplets require a trusted paired comparison "
            f"audit: {uncovered_paired_measurements}"
        )
    design_metric_texts = [
        design["design_summary"],
        research_frame["primary_question"],
        *research_frame["supported_questions"],
        *[row["statement"] for row in criteria],
        *[row["basis_text"] for row in criteria],
    ]
    unsupported_metric = _unsupported_reader_metric(
        design_metric_texts,
        planned_measurements,
    )
    if unsupported_metric is not None:
        raise ContractError(
            "reader-facing design text names an unplanned metric: "
            f"{unsupported_metric}",
            error_code="unplanned_named_metric",
            field_path="design.measurement_plan",
            suggestion=(
                "为该指标增加名称与含义完全匹配的 measurement_plan 项并由判据引用，"
                "或从研究问题、设计摘要和判据中删除该指标名称。平均有符号误差应使用"
                " signed_error/mean_signed_error 命名，mse 只表示均方误差。"
            ),
        )
    policy = _object(design["interpretation_policy"], "design.interpretation_policy")
    _exact(
        policy,
        {"primary_estimand", "null_rule", "uncertainty_rule", "partial_rule"},
        "design.interpretation_policy",
    )
    for field in ("primary_estimand", "null_rule", "uncertainty_rule", "partial_rule"):
        _text(policy[field], f"design.interpretation_policy.{field}")
    for field in ("null_rule", "uncertainty_rule", "partial_rule"):
        if (
            HARD_NUMERIC_CUTOFF.search(policy[field]) is not None
            or RELATIVE_DECISION_CUTOFF.search(policy[field]) is not None
        ) and not _is_grounded_zero_direction_rule(policy[field], criteria):
            raise ContractError(
                "design.interpretation_policy contains an ungrounded numeric decision threshold",
                error_code="ungrounded_interpretation_threshold",
                field_path=f"design.interpretation_policy.{field}",
                suggestion=(
                    "解释规则会影响科学终态，不能自行发明样本数、百分比或参数变化阈值。"
                    "改用与预设估计目标和证据类型一致的定性规则；若确需阈值，应先把"
                    "来源建模为有依据的设计判据。"
                ),
            )
    if CJK_TEXT.search(request["task"]):
        reader_texts: list[tuple[str, str]] = [
            ("response.normalized_task", response["normalized_task"]),
            ("response.design_summary", response["design_summary"]),
            ("design.normalized_task", design["normalized_task"]),
            ("design.design_summary", design["design_summary"]),
            (
                "design.research_frame.primary_question",
                research_frame["primary_question"],
            ),
            ("design.research_frame.claim_scope", research_frame["claim_scope"]),
            (
                "design.research_frame.literature_basis",
                research_frame["literature_basis"],
            ),
        ]
        for index, row in enumerate(evidence_rows):
            reader_texts.extend(
                (
                    (
                        f"design.research_frame.input_evidence[{index}].{field}",
                        row[field],
                    )
                    for field in ("role", "intended_use", "limitations")
                )
            )
        for field in (
            "supported_questions",
            "deferred_questions",
            "assumptions",
            "threats_to_validity",
        ):
            reader_texts.extend(
                (
                    f"design.research_frame.{field}[{index}]",
                    text,
                )
                for index, text in enumerate(research_frame[field])
            )
        reader_texts.extend(
            (f"design.criteria[{index}].{field}", row[field])
            for index, row in enumerate(criteria)
            for field in ("statement", "basis_text")
        )
        for index, row in enumerate(measurement_plan_rows):
            reader_texts.extend(
                [
                    (
                        f"design.measurement_plan[{index}].display_name",
                        row["display_name"],
                    ),
                    (
                        f"design.measurement_plan[{index}].scientific_meaning",
                        row["scientific_meaning"],
                    ),
                ]
            )
        for index, row in enumerate(method_decision_rows):
            reader_texts.extend(
                (
                    f"design.method_decisions[{index}].{field}",
                    row[field],
                )
                for field in ("decision", "rationale", "claim_limit")
            )
        reader_texts.extend(
            (f"design.interpretation_policy.{field}", policy[field])
            for field in (
                "primary_estimand",
                "null_rule",
                "uncertainty_rule",
                "partial_rule",
            )
        )
        non_chinese = [
            label for label, text in reader_texts if CJK_TEXT.search(text) is None
        ]
        if non_chinese:
            raise ContractError(
                "reader-facing design fields must use the user's Chinese language: "
                + ", ".join(non_chinese)
            )
    for audit_row in design.get("paired_comparison_audits", []):
        if not isinstance(audit_row, dict):
            continue
        for fit_field in ("baseline_fit_condition", "candidate_fit_condition"):
            normalized = _normalize_fit_condition_text(audit_row.get(fit_field))
            if normalized is not None:
                audit_row[fit_field] = normalized
    return design


def experiment_stage(
    design: dict[str, Any],
    stage_id: str | None = None,
) -> dict[str, Any]:
    """Return one validated design stage without inventing a default route."""

    stages = design.get("experiment_stages")
    if not isinstance(stages, list) or not stages:
        raise ContractError(
            "design.experiment_stages is required; pre-refactor designs are not accepted"
        )
    selected = stage_id or stages[0].get("id")
    for stage in stages:
        if isinstance(stage, dict) and stage.get("id") == selected:
            return stage
    raise ContractError(f"unknown experiment stage: {selected}")


def stage_execution(
    design: dict[str, Any],
    stage_id: str | None = None,
) -> dict[str, Any]:
    return experiment_stage(design, stage_id)["execution"]


def validate_worker_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = clone(_object(payload, "worker_result"), "worker_result")
    fields = {
        "schema_version",
        "execution_completed",
        "measurements",
        "result_items",
        "artifacts",
        "warnings",
        "endpoint_results",
        "scientific_payload",
    }
    _exact(result, fields, "worker_result")
    if result["schema_version"] != WORKER_RESULT_VERSION:
        raise ContractError(
            f"worker_result.schema_version must be {WORKER_RESULT_VERSION}"
        )
    _boolean(result["execution_completed"], "worker_result.execution_completed")
    measurements = _array(result["measurements"], "worker_result.measurements", 0, 200)
    names: set[str] = set()
    measurement_sources: list[tuple[str, str]] = []
    for index, raw in enumerate(measurements):
        row = _object(raw, f"worker_result.measurements[{index}]")
        label = f"worker_result.measurements[{index}]"
        _exact(row, {"name", "value", "unit", "role", "source_artifact"}, label)
        name = _safe_ref(row["name"], f"{label}.name")
        if name in names:
            raise ContractError(f"worker_result.measurements duplicate name: {name}")
        names.add(name)
        _number(row["value"], f"{label}.value")
        _text(row["unit"], f"{label}.unit", minimum=0, maximum=100, preserve=True)
        _enum(row["role"], {"primary", "secondary", "diagnostic"}, f"{label}.role")
        source_artifact = _nullable_text(
            row["source_artifact"], f"{label}.source_artifact", maximum=500
        )
        if source_artifact is not None:
            measurement_sources.append((label, source_artifact))
    result_items = _array(result["result_items"], "worker_result.result_items", 0, 200)
    result_ids: set[str] = set()
    result_sources: list[tuple[str, str]] = []
    for index, raw in enumerate(result_items):
        row = _object(raw, f"worker_result.result_items[{index}]")
        label = f"worker_result.result_items[{index}]"
        _exact(
            row,
            {
                "id",
                "display_name",
                "value_kind",
                "value",
                "unit",
                "role",
                "source_artifact",
            },
            label,
        )
        result_id = _safe_ref(row["id"], f"{label}.id")
        if result_id in result_ids:
            raise ContractError(f"worker_result.result_items duplicate id: {result_id}")
        result_ids.add(result_id)
        _text(row["display_name"], f"{label}.display_name", maximum=200)
        value_kind = _enum(row["value_kind"], RESULT_VALUE_KINDS, f"{label}.value_kind")
        value = row["value"]
        if value_kind == "number":
            _number(value, f"{label}.value")
        elif value_kind == "count":
            _integer(value, f"{label}.value", 0, 2**63 - 1)
        elif value_kind == "boolean":
            _boolean(value, f"{label}.value")
        else:
            _text(value, f"{label}.value", maximum=2000)
        _text(row["unit"], f"{label}.unit", minimum=0, maximum=100, preserve=True)
        _enum(row["role"], {"primary", "secondary", "diagnostic"}, f"{label}.role")
        source_artifact = _nullable_text(
            row["source_artifact"], f"{label}.source_artifact", maximum=500
        )
        if source_artifact is not None:
            result_sources.append((label, source_artifact))
    artifacts = _array(result["artifacts"], "worker_result.artifacts", 0, 200)
    artifact_paths: set[str] = set()
    for index, raw in enumerate(artifacts):
        row = _object(raw, f"worker_result.artifacts[{index}]")
        label = f"worker_result.artifacts[{index}]"
        _exact(row, {"path", "kind", "description"}, label)
        path = _text(row["path"], f"{label}.path", maximum=500)
        if path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] == "result.json":
            raise ContractError(
                f"{label}.path cannot use reserved worker protocol result.json"
            )
        if path.startswith("/") or "\\" in path or ".." in path.split("/"):
            raise ContractError(f"{label}.path must be a safe relative POSIX path")
        if path in artifact_paths:
            raise ContractError(f"worker_result.artifacts duplicate path: {path}")
        artifact_paths.add(path)
        _enum(
            row["kind"],
            {
                "json",
                "csv",
                "text",
                "markdown",
                "image",
                "fits",
                "netcdf",
                "hdf5",
                "parquet",
                "other",
            },
            f"{label}.kind",
        )
        _text(row["description"], f"{label}.description", maximum=1000)
    for label, source_artifact in measurement_sources:
        if source_artifact not in artifact_paths:
            raise ContractError(
                f"{label}.source_artifact must reference a declared artifact"
            )
    for label, source_artifact in result_sources:
        if source_artifact not in artifact_paths:
            raise ContractError(
                f"{label}.source_artifact must reference a declared artifact"
            )
    _text_array(result["warnings"], "worker_result.warnings", 100)
    endpoints = _array(
        result["endpoint_results"], "worker_result.endpoint_results", 0, 100
    )
    endpoint_ids: set[str] = set()
    for index, raw in enumerate(endpoints):
        row = _object(raw, f"worker_result.endpoint_results[{index}]")
        label = f"worker_result.endpoint_results[{index}]"
        _exact(row, {"id", "status", "summary"}, label)
        endpoint_id = _safe_id(row["id"], f"{label}.id")
        if endpoint_id in endpoint_ids:
            raise ContractError(
                f"worker_result.endpoint_results duplicate id: {endpoint_id}"
            )
        endpoint_ids.add(endpoint_id)
        _enum(row["status"], ENDPOINT_STATUS, f"{label}.status")
        _text(row["summary"], f"{label}.summary")
    scientific = _object(
        result["scientific_payload"], "worker_result.scientific_payload"
    )
    _exact(
        scientific,
        {
            "primary_estimand",
            "estimate",
            "interval",
            "equivalence_bounds",
            "sensitivity",
            "uncertainty_reasons",
        },
        "worker_result.scientific_payload",
    )
    _text(
        scientific["primary_estimand"],
        "worker_result.scientific_payload.primary_estimand",
    )
    if scientific["estimate"] is not None:
        _number(scientific["estimate"], "worker_result.scientific_payload.estimate")
    for field in ("interval", "equivalence_bounds"):
        value = scientific[field]
        if value is not None:
            bounds = _array(value, f"worker_result.scientific_payload.{field}", 2, 2)
            low = _number(bounds[0], f"worker_result.scientific_payload.{field}[0]")
            high = _number(bounds[1], f"worker_result.scientific_payload.{field}[1]")
            if low > high:
                raise ContractError(
                    f"worker_result.scientific_payload.{field} must be ordered"
                )
    _nullable_text(
        scientific["sensitivity"], "worker_result.scientific_payload.sensitivity"
    )
    _text_array(
        scientific["uncertainty_reasons"],
        "worker_result.scientific_payload.uncertainty_reasons",
        30,
    )
    return result


def validate_scientific_assessment(
    payload: dict[str, Any],
    design_payload: dict[str, Any],
    worker_payload: dict[str, Any],
    task_text: str | None = None,
    stage_id: str | None = None,
    evidence_basis_texts: list[str] | None = None,
) -> dict[str, Any]:
    design = _object(design_payload, "design")
    worker = validate_worker_result(worker_payload)
    assessment = clone(
        _object(payload, "scientific_assessment"), "scientific_assessment"
    )
    if "stage_outcome" not in assessment:
        assessment["stage_outcome"] = (
            "completed"
            if assessment.get("proposed_outcome")
            in {"completed_interpretable", "scientific_null"}
            else "inconclusive"
        )
    fields = {
        "proposed_outcome",
        "stage_outcome",
        "rationale",
        "criterion_results",
        "uncertainty_reasons",
        "null_assessment",
        "report_narrative",
    }
    _exact(assessment, fields, "scientific_assessment")
    outcome = _enum(
        assessment["proposed_outcome"],
        SCIENTIFIC_OUTCOMES,
        "scientific_assessment.proposed_outcome",
    )
    stage_outcome = _enum(
        assessment["stage_outcome"],
        {
            "completed",
            "inconclusive",
            "input_missing",
            "evidence_conflict",
            "method_invalid",
        },
        "scientific_assessment.stage_outcome",
    )
    intermediate_completed_stage = False
    if stage_id is not None:
        stage_ids = {
            str(row.get("id"))
            for row in design.get("experiment_stages", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }
        current_stage = next(
            (
                row
                for row in design.get("experiment_stages", [])
                if isinstance(row, dict) and row.get("id") == stage_id
            ),
            None,
        )
        if current_stage is None:
            raise ContractError(
                f"unknown experiment stage: {stage_id}",
                field_path="scientific_assessment.stage_outcome",
            )
        completed_target = (
            current_stage.get("transitions", {}).get("completed")
            if isinstance(current_stage.get("transitions"), dict)
            else None
        )
        intermediate_completed_stage = completed_target in stage_ids
    if (
        stage_outcome == "completed"
        and intermediate_completed_stage
        and outcome != "partial_result"
    ):
        raise ContractError(
            "a completed intermediate stage requires partial_result until the next "
            "experiment stage is verified",
            field_path="scientific_assessment.proposed_outcome",
            suggestion="当前阶段完成但整项实验尚未结束时使用 partial_result；最终阶段完成后再给出整体终态。",
        )
    if (
        stage_outcome == "completed"
        and not intermediate_completed_stage
        and outcome not in {"completed_interpretable", "scientific_null"}
    ):
        raise ContractError(
            "scientific_assessment.stage_outcome completed requires a completed "
            "or scientific-null proposed outcome"
        )
    if stage_outcome != "completed" and outcome not in {
        "partial_result",
        "high_uncertainty",
    }:
        raise ContractError(
            "an incomplete stage outcome requires partial_result or high_uncertainty"
        )
    _text(assessment["rationale"], "scientific_assessment.rationale")
    criteria_by_id = {row["id"]: row for row in design["criteria"]}
    criterion_ids = set(criteria_by_id)
    measurement_by_name = {row["name"]: row for row in worker["measurements"]}
    result_by_id = {row["id"]: row for row in worker.get("result_items", [])}
    endpoint_by_id = {row["id"]: row for row in worker["endpoint_results"]}
    if (
        worker["scientific_payload"]["primary_estimand"]
        != design["interpretation_policy"]["primary_estimand"]
    ):
        raise ContractError(
            "worker_result.scientific_payload.primary_estimand must match the validated design"
        )
    seen: set[str] = set()
    rows = _array(
        assessment["criterion_results"],
        "scientific_assessment.criterion_results",
        0,
        30,
    )
    for index, raw in enumerate(rows):
        row = _object(raw, f"scientific_assessment.criterion_results[{index}]")
        label = f"scientific_assessment.criterion_results[{index}]"
        _exact(row, {"criterion_id", "status", "explanation"}, label)
        criterion_id = _safe_id(row["criterion_id"], f"{label}.criterion_id")
        if criterion_id not in criterion_ids:
            raise ContractError(
                f"{label}.criterion_id references an unknown design criterion"
            )
        if criterion_id in seen:
            raise ContractError(
                f"scientific_assessment has duplicate criterion result: {criterion_id}"
            )
        seen.add(criterion_id)
        status = _enum(row["status"], CRITERION_STATUS, f"{label}.status")
        _text(row["explanation"], f"{label}.explanation")
        criterion = criteria_by_id[criterion_id]
        missing_measurements = sorted(
            set(criterion["measurement_refs"]) - set(measurement_by_name)
        )
        missing_results = sorted(set(criterion["result_refs"]) - set(result_by_id))
        missing_endpoints = sorted(
            set(criterion["endpoint_refs"]) - set(endpoint_by_id)
        )
        if missing_measurements or missing_results or missing_endpoints:
            raise ContractError(
                f"{label} lacks declared evidence: "
                f"measurements={missing_measurements}, typed_results={missing_results}, "
                f"endpoints={missing_endpoints}"
            )
        if status == "met":
            incomplete_endpoints = [
                endpoint_id
                for endpoint_id in criterion["endpoint_refs"]
                if endpoint_by_id[endpoint_id]["status"] != "completed"
            ]
            if incomplete_endpoints:
                raise ContractError(
                    f"{label} cannot be met while referenced endpoints are incomplete: "
                    f"{incomplete_endpoints}"
                )
    if seen != criterion_ids:
        raise ContractError(
            "scientific_assessment.criterion_results must evaluate every design criterion"
        )
    uncertainty = _text_array(
        assessment["uncertainty_reasons"],
        "scientific_assessment.uncertainty_reasons",
        30,
    )
    narrative = _object(
        assessment["report_narrative"],
        "scientific_assessment.report_narrative",
    )
    _exact(
        narrative,
        {
            "title",
            "objective",
            "data_scope",
            "method",
            "interpretation",
            "evidence_strength",
            "claim_boundary",
            "limitations",
            "next_steps",
        },
        "scientific_assessment.report_narrative",
    )
    for field, maximum in (
        ("title", 120),
        ("objective", 1200),
        ("data_scope", 1200),
        ("method", 2000),
        ("interpretation", 2000),
        ("evidence_strength", 1600),
        ("claim_boundary", 1600),
    ):
        narrative[field] = _text(
            narrative[field],
            f"scientific_assessment.report_narrative.{field}",
            maximum=maximum,
        )
    limitation_rows = _array(
        narrative["limitations"],
        "scientific_assessment.report_narrative.limitations",
        0,
        8,
    )
    narrative["limitations"] = [
        _text(
            row,
            f"scientific_assessment.report_narrative.limitations[{index}]",
            maximum=1000,
        )
        for index, row in enumerate(limitation_rows)
    ]
    next_step_rows = _array(
        narrative["next_steps"],
        "scientific_assessment.report_narrative.next_steps",
        0,
        8,
    )
    narrative["next_steps"] = [
        _text(
            row,
            f"scientific_assessment.report_narrative.next_steps[{index}]",
            maximum=1000,
        )
        for index, row in enumerate(next_step_rows)
    ]
    narrative_entries = [
        (f"scientific_assessment.report_narrative.{field}", narrative[field])
        for field in (
            "title",
            "objective",
            "data_scope",
            "method",
            "interpretation",
            "evidence_strength",
            "claim_boundary",
        )
    ]
    narrative_entries.extend(
        (
            f"scientific_assessment.report_narrative.limitations[{index}]",
            text,
        )
        for index, text in enumerate(narrative["limitations"])
    )
    narrative_entries.extend(
        (
            f"scientific_assessment.report_narrative.next_steps[{index}]",
            text,
        )
        for index, text in enumerate(narrative["next_steps"])
    )
    narrative_texts = [text for _path, text in narrative_entries]
    reader_entries = [
        ("scientific_assessment.rationale", assessment["rationale"]),
        *[
            (
                f"scientific_assessment.criterion_results[{index}].explanation",
                row["explanation"],
            )
            for index, row in enumerate(rows)
        ],
        *[
            (f"scientific_assessment.uncertainty_reasons[{index}]", text)
            for index, text in enumerate(uncertainty)
        ],
        *narrative_entries,
    ]
    reader_texts = [text for _path, text in reader_entries]
    conflicting_outcomes = [
        claimed_outcome
        for claimed_outcome, marker in EXPLICIT_SCIENTIFIC_OUTCOME_CLAIMS.items()
        if claimed_outcome != outcome
        and any(marker.search(text) for text in reader_texts)
    ]
    if conflicting_outcomes:
        raise ContractError(
            "scientific_assessment reader text claims a scientific outcome that "
            "conflicts with proposed_outcome"
        )
    actual_measurement_names = {str(row["name"]) for row in worker["measurements"]}
    metric_support_texts = set(actual_measurement_names)
    for planned in design.get("measurement_plan", []):
        if planned.get("name") not in actual_measurement_names:
            continue
        metric_support_texts.update(
            {
                str(planned.get("display_name", "")),
                str(planned.get("scientific_meaning", "")),
            }
        )
    unsupported_metric = _unsupported_reader_metric(
        narrative_texts,
        metric_support_texts,
    )
    if unsupported_metric is not None:
        raise ContractError(
            "scientific_assessment reader text names an unverified metric: "
            f"{unsupported_metric}"
        )
    unsupported_values = _unsupported_quantitative_claims(
        [
            assessment["rationale"],
            *[row["explanation"] for row in rows],
            narrative["interpretation"],
        ],
        design,
        worker,
        task_text,
        evidence_basis_texts,
    )
    if unsupported_values:
        raise ContractError(
            "scientific_assessment quantitative claims are not present in the "
            f"verified result, immutable input, or predeclared basis: {unsupported_values}",
            error_code="unverified_quantitative_claim",
            field_path="scientific_assessment.report_narrative.interpretation",
            suggestion=(
                "结论、判据说明和摘要中的每个数字都必须来自已核验测量、"
                "有类型结果、不可变输入或设计中预先声明的依据；删除或改正"
                "无法追溯的数字。"
            ),
        )
    for text in narrative_texts:
        if READER_INTERNAL_TOKEN.search(text):
            raise ContractError(
                "scientific_assessment report narrative must not expose internal "
                "contracts, ids, or workflow fields"
            )
    analysis_mode = str(design.get("research_frame", {}).get("analysis_mode", ""))
    interval_basis_text = " ".join(
        [
            analysis_mode,
            *[
                str(row.get(field, ""))
                for row in design.get("method_decisions", [])
                for field in ("decision", "rationale", "claim_limit")
            ],
            *[
                str(row.get(field, ""))
                for row in design.get("criteria", [])
                for field in ("statement", "basis_text")
            ],
        ]
    )
    has_interval_basis = INTERVAL_BASIS_LANGUAGE.search(interval_basis_text) is not None
    if (
        worker["scientific_payload"].get("interval") is not None
        and not has_interval_basis
    ):
        raise ContractError(
            "worker_result.scientific_payload.interval requires a predeclared "
            "inferential or uncertainty-estimation method",
            error_code="unsupported_interval_basis",
            field_path="worker_result.scientific_payload.interval",
            suggestion=(
                "描述性或确定性分析应将 interval 设为 null。只有设计已明确声明置信区间、"
                "可信区间、重采样区间或其他可复算区间方法时，才能返回区间；不得围绕点"
                "估计自行加减常数。"
            ),
        )
    has_inferential_support = (
        has_interval_basis and worker["scientific_payload"].get("interval") is not None
    )
    if not has_inferential_support and any(
        _unsupported_reader_claim(
            text,
            UNSUPPORTED_SIGNIFICANCE_LANGUAGE,
            NONCLAIM_SIGNIFICANCE_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment reader text must not use 显著 without "
            "inferential evidence",
            error_code="unsupported_significance_language",
            field_path="scientific_assessment.report_narrative",
            suggestion="改写为当前评价行上观测到的数值变化，并明确未进行推断性检验。",
        )
    if analysis_mode != "predictive" and any(
        _unsupported_reader_claim(
            text,
            UNSUPPORTED_GENERALIZATION_LANGUAGE,
            NONCLAIM_GENERALIZATION_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment reader text must describe the current "
            "holdout segment instead of claiming 泛化能力",
            error_code="unsupported_generalization_language",
            field_path="scientific_assessment.report_narrative",
            suggestion="改写为当前留出段观测到的误差变化，不外推到其他样本或时期。",
        )
    if any(
        _unsupported_reader_claim(
            text,
            UNSUPPORTED_BIAS_ELIMINATION_LANGUAGE,
            NONCLAIM_BIAS_ELIMINATION_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment reader text must report observed error changes "
            "without claiming that systematic bias or overestimation was eliminated",
            error_code="unsupported_bias_elimination_language",
            field_path="scientific_assessment.report_narrative",
            suggestion="报告平均有符号误差的实际数值和方向，不声称系统偏差已被消除。",
        )
    has_equivalence_basis = worker["scientific_payload"].get(
        "equivalence_bounds"
    ) is not None or any(
        HARD_NUMERIC_CUTOFF.search(str(row.get("statement", ""))) is not None
        for row in design.get("criteria", [])
    )
    if not has_equivalence_basis and any(
        _unsupported_reader_claim(
            text,
            UNSUPPORTED_TRIVIAL_IMPACT_LANGUAGE,
            NONCLAIM_TRIVIAL_IMPACT_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment cannot call an effect negligible or non-substantive "
            "without an equivalence or grounded decision basis",
            error_code="unsupported_trivial_impact_language",
            field_path="scientific_assessment.report_narrative",
            suggestion=(
                "只报告两个条件的估计值、差值与方向；没有等效界限或有依据阈值时，"
                "明确说明当前设计不足以判断该差异是否具有实质影响。"
            ),
        )
    if any(
        _unsupported_reader_claim(
            text,
            UNSUPPORTED_SYSTEMIC_HOLDOUT_LANGUAGE,
            NONCLAIM_SYSTEMIC_HOLDOUT_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment reader text must describe the observed signed-error "
            "direction instead of labeling a small holdout as a systemic bias",
            error_code="unsupported_systemic_holdout_claim",
            field_path="scientific_assessment.report_narrative",
            suggestion="写明当前留出段的平均有符号误差为正或为负，并给出行数与数值。",
        )
    if any(
        _unsupported_reader_claim(
            text,
            UNSUPPORTED_CLOSENESS_LANGUAGE,
            NONCLAIM_CLOSENESS_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment reader text must not claim predictions are close "
            "without a predefined closeness threshold",
            error_code="unsupported_unbounded_closeness_claim",
            field_path="scientific_assessment.report_narrative",
            suggestion="改写为逐行绝对误差是否下降，或报告预先定义阈值下的通过行数。",
        )
    if any(UNSUPPORTED_NONIDENTITY_LANGUAGE.search(text) for text in reader_texts):
        raise ContractError(
            "scientific_assessment cannot infer a non-identity relationship from point estimates alone",
            error_code="unsupported_nonidentity_claim",
            field_path="scientific_assessment.report_narrative.interpretation",
            suggestion=(
                "只报告校准斜率和截距的已核验点估计。没有针对斜率等于 1、截距等于 0"
                "的不确定性分析时，不写“非恒等关系”。"
            ),
        )
    correlation_degree_basis = " ".join(
        str(row.get(field, ""))
        for row in design.get("criteria", [])
        for field in ("statement", "basis_text")
        if row.get("basis_kind") in {"user_request", "located_source"}
    )
    has_correlation_degree_basis = bool(
        CORRELATION_DEGREE_BASIS_LANGUAGE.search(correlation_degree_basis)
        and HARD_NUMERIC_CUTOFF.search(correlation_degree_basis)
    )
    if not has_correlation_degree_basis and any(
        _unsupported_reader_claim(
            text,
            UNSUPPORTED_CORRELATION_DEGREE_LANGUAGE,
            NONCLAIM_CORRELATION_DEGREE_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment must not assign a qualitative correlation "
            "strength without a user-supplied or located classification basis",
            error_code="unsupported_correlation_degree_language",
            field_path="scientific_assessment.report_narrative",
            suggestion=(
                "只报告 Pearson 相关系数的数值、符号和当前样本范围；"
                "没有预先给定的分级阈值时，不写极强、接近 1 或模式清晰。"
            ),
        )
    monotonic_plan_text = " ".join(
        [
            str(design.get("normalized_task", "")),
            *[
                str(row.get(field, ""))
                for collection, fields in (
                    (design.get("criteria", []), ("statement", "basis_text")),
                    (
                        design.get("measurement_plan", []),
                        ("display_name", "scientific_meaning"),
                    ),
                    (
                        design.get("result_plan", []),
                        ("display_name", "scientific_meaning"),
                    ),
                    (
                        design.get("method_decisions", []),
                        ("decision", "rationale"),
                    ),
                )
                for row in collection
                for field in fields
            ],
        ]
    )
    if MONOTONIC_LANGUAGE.search(monotonic_plan_text) is None and any(
        _unsupported_reader_claim(
            text,
            MONOTONIC_LANGUAGE,
            NONCLAIM_MONOTONIC_LANGUAGE,
        )
        for text in reader_texts
    ):
        raise ContractError(
            "scientific_assessment must not add an unplanned monotonicity claim "
            "to a correlation result",
            error_code="unverified_monotonicity_claim",
            field_path="scientific_assessment.report_narrative.interpretation",
            suggestion=(
                "Pearson 相关只报告系数与方向；除非设计已计划并核验单调性，"
                "不得写每个观测都单调递增或递减。"
            ),
        )
    for field_path, text in reader_entries:
        if (
            ROBUSTNESS_LANGUAGE.search(text)
            and not BOUNDED_ROBUSTNESS_LANGUAGE.search(text)
            and not NONCLAIM_ROBUSTNESS_LANGUAGE.search(text)
        ):
            raise ContractError(
                "scientific_assessment reader text must bound robustness language to "
                "the specific perturbation or flagged row that was checked; "
                f"offending text: {text[:180]}",
                error_code="unsupported_broad_robustness_claim",
                field_path=field_path,
                suggestion=(
                    "只修改该字段：改写为排除这一标记行后方向性结论一致；"
                    "若它是后续研究，改写为针对具体标记条件的敏感性检查。"
                ),
            )
    if isinstance(task_text, str) and CJK_TEXT.search(task_text):
        language_rows = [
            ("rationale", assessment["rationale"]),
            *[
                (f"criterion_results[{index}].explanation", row["explanation"])
                for index, row in enumerate(rows)
            ],
            *[
                (f"uncertainty_reasons[{index}]", row)
                for index, row in enumerate(uncertainty)
            ],
            *[
                (f"report_narrative[{index}]", row)
                for index, row in enumerate(narrative_texts)
            ],
        ]
        for label, text in language_rows:
            if CJK_TEXT.search(text) is None:
                raise ContractError(
                    f"scientific_assessment.{label} must use the user's Chinese language"
                )
    if (
        isinstance(task_text, str)
        and SYNTHETIC_TASK.search(task_text)
        and SYNTHETIC_DISCLOSURE.search(" ".join(narrative_texts)) is None
    ):
        raise ContractError(
            "scientific_assessment.report_narrative must disclose that the task uses "
            "a synthetic or simulated fixture"
        )
    null_assessment = assessment["null_assessment"]
    if null_assessment is not None:
        null_row = _object(null_assessment, "scientific_assessment.null_assessment")
        _exact(
            null_row,
            {"estimand", "interval", "equivalence_bounds", "power_or_sensitivity"},
            "scientific_assessment.null_assessment",
        )
        _text(null_row["estimand"], "scientific_assessment.null_assessment.estimand")
        for field in ("interval", "equivalence_bounds"):
            value = null_row[field]
            if value is not None:
                bounds = _array(
                    value, f"scientific_assessment.null_assessment.{field}", 2, 2
                )
                low = _number(
                    bounds[0], f"scientific_assessment.null_assessment.{field}[0]"
                )
                high = _number(
                    bounds[1], f"scientific_assessment.null_assessment.{field}[1]"
                )
                if low > high:
                    raise ContractError(
                        f"scientific_assessment.null_assessment.{field} must be ordered"
                    )
        _nullable_text(
            null_row["power_or_sensitivity"],
            "scientific_assessment.null_assessment.power_or_sensitivity",
        )
    endpoints = worker["endpoint_results"]
    completed = any(row["status"] == "completed" for row in endpoints)
    incomplete = any(row["status"] != "completed" for row in endpoints)
    if (
        outcome == "completed_interpretable"
        and not worker["measurements"]
        and not worker["result_items"]
    ):
        raise ContractError(
            "completed_interpretable requires at least one measured or typed result"
        )
    if (
        outcome == "partial_result"
        and not intermediate_completed_stage
        and not (completed and incomplete)
    ):
        raise ContractError(
            "partial_result requires both completed and incomplete endpoints"
        )
    if outcome == "high_uncertainty" and not uncertainty:
        raise ContractError("high_uncertainty requires explicit uncertainty reasons")
    if outcome == "scientific_null":
        if null_assessment is None:
            raise ContractError("scientific_null requires null_assessment")
        if null_assessment["interval"] is None:
            raise ContractError("scientific_null requires an interval")
        if (
            null_assessment["equivalence_bounds"] is None
            and null_assessment["power_or_sensitivity"] is None
        ):
            raise ContractError(
                "scientific_null requires equivalence bounds or a power/sensitivity justification"
            )
        if (
            null_assessment["estimand"]
            != worker["scientific_payload"]["primary_estimand"]
        ):
            raise ContractError("scientific_null estimand must match the worker result")
    return assessment
