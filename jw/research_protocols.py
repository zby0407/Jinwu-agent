"""Deterministic contracts for narrow, high-risk scientific analyses."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

F107_DISCONTINUITY_PROTOCOL = "f107_discontinuity_v1"
GENERIC_DATA_PRODUCT = "generic_data_artifact"
B1_FLARE_CASE_PROTOCOL = "b1-flare-case-v1"
B1_FLARE_CASE_DATA_PRODUCT = "b1_flare_case_v1"
B1_FLARE_CASE_DATASET_IDS: tuple[str, ...] = (
    "b1-sharp-11158-v1",
    "b1-goes-events-2011-v1",
    "b1-goes15-xrs-201102-v1",
    "b1-case-parameters-v1",
    "b1-case-sources-v1",
)
SILSO_CYCLE_REPRODUCTION_PROTOCOL = "silso_cycle_reproduction_v1"
SILSO_CYCLE_EXTREMA_DATA_PRODUCT = "silso_cycle_extrema_v1"
SILSO_CYCLE_MORPHOLOGY_PROTOCOL = "silso_cycle_morphology_v1"
SILSO_CYCLE_MORPHOLOGY_DATA_PRODUCT = "silso_cycle_morphology_v1"
SILSO_CYCLE_DESCRIPTIVE_PROTOCOL = "silso_cycle_descriptive_v1"
SILSO_CYCLE_DESCRIPTIVE_DATA_PRODUCT = "silso_cycle_descriptive_v1"
SILSO_CYCLE_PHASE_PROTOCOL = "silso_cycle_phase_v1"
SILSO_GNBB2_ANNUAL_PROTOCOL = "silso_gnbb2_annual_v1"
SILSO_CYCLE_SENSITIVITY_PROTOCOL = "silso_cycle_sensitivity_v1"
SILSO_CYCLE_ASSOCIATIONS_PROTOCOL = "silso_cycle_associations_v1"
SOLAR_CYCLE_26_READINESS_PROTOCOL = "solar_cycle_26_readiness_v1"
SOLAR_CYCLE_26_READINESS_DATA_PRODUCT = "solar_cycle_26_readiness_inventory_v1"
SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL = "solar_cycle_26_forecast_backtest_v1"
SOLAR_CYCLE_26_FORECAST_BACKTEST_DATA_PRODUCT = "solar_cycle_26_forecast_backtest_v1"
SOLAR_CYCLE_26_READINESS_DATASET_IDS: tuple[str, ...] = (
    "silso-monthly-total-v2",
    "silso-monthly-smoothed-v2",
    "silso-cycle-extrema-v2",
    "noaa-swpc-monthly-f107-v1",
    "mwo-wso-polar-field-v2",
    "wso-current-polar-field-v1",
)
SOLAR_POLAR_PRECURSOR_PROTOCOL = "solar_polar_precursor_v1"
SOLAR_POLAR_PRECURSOR_DATA_PRODUCT = "solar_polar_precursor_table_v1"
SOLAR_POLAR_PRECURSOR_DATASET_IDS: tuple[str, ...] = (
    "silso-monthly-total-v2",
    "mwo-wso-polar-field-v2",
)
SILSO_CYCLE_REPRODUCTION_DATASET_IDS: tuple[str, ...] = (
    "silso-monthly-total-v2",
    "silso-monthly-smoothed-v2",
    "silso-cycle-extrema-v2",
)
F107_DISCONTINUITY_REQUIRED_MEASUREMENTS: tuple[str, ...] = (
    "f107_full_period_relation",
    "f107_pre_1980_relation",
    "f107_post_1980_relation",
    "f107_fixed_1980_chow_f",
    "f107_scan_best_break_year",
    "f107_relative_scale_jump",
    "f107_pre_model_predicts_post_mean_residual",
    "f107_pre_model_predicts_post_positive_fraction",
    "f107_post_model_predicts_pre_mean_residual",
    "f107_post_model_predicts_pre_positive_fraction",
    "f107_low_activity_sensitivity",
    "f107_month_coverage_sensitivity",
)

_F107_PATTERN = re.compile(
    r"(?:f\s*10[.]?7|10[.]7\s*cm|太阳射电流量)",
    re.IGNORECASE,
)
_F107_DISCONTINUITY_PATTERN = re.compile(
    r"(?:1980|1981|漂移|不连续|断点|变点|跨时段|跨周期稳定|"
    r"discontinuity|breakpoint|change[\s-]?point|drift|cross[\s-]?period)",
    re.IGNORECASE,
)
_SILSO_PATTERN = re.compile(r"(?:WDC[- ]?SILSO|SILSO|太阳黑子)", re.IGNORECASE)
_SILSO_CYCLE_REPRODUCTION_PATTERN = re.compile(
    r"(?:太阳活动周|solar\s+cycle|周期).*(?:极小|极大|极值|上升时间|"
    r"minimum|maximum|extrema|rise\s+time)|"
    r"(?:极小|极大|极值|上升时间|minimum|maximum|extrema|rise\s+time).*"
    r"(?:太阳活动周|solar\s+cycle|周期)",
    re.IGNORECASE | re.DOTALL,
)
_SILSO_CYCLE_ENGLISH_PATTERN = re.compile(
    r"\b(?:solar\s+)?cycles?\b.*\b(?:minimum|maximum|extrema|rise\s+time)\b|"
    r"\b(?:minimum|maximum|extrema|rise\s+time)\b.*\b(?:solar\s+)?cycles?\b",
    re.IGNORECASE | re.DOTALL,
)
_SILSO_CYCLE_MORPHOLOGY_PATTERN = re.compile(
    r"(?:周期形态|形态统计|cycle\s+morphology|Waldmeier|"
    r"周期长度.{0,20}(?:上升|下降)|"
    r"rise\s+time.{0,30}decline\s+time)",
    re.IGNORECASE | re.DOTALL,
)
_CYCLE_DESCRIPTIVE_PATTERN = re.compile(
    r"(?:周期|cycles?).{0,80}(?:累计|累积|均值|平均|integrated|cumulative|mean)|"
    r"(?:低活动|low.activity).{0,60}(?:时长|持续|月份|duration|fraction|months)",
    re.IGNORECASE,
)
_POLAR_FIELD_PATTERN = re.compile(
    r"(?:polar[\s-]?field|polar precursor|MWO|WSO|极区磁场|极地磁场|极区场强|极区场)",
    re.IGNORECASE,
)
_POLAR_PRECURSOR_INTENT_PATTERN = re.compile(
    r"(?:precursor|predictor|following cycle|next cycle|near minimum|"
    r"前兆|预测因子|下一周期|下一(?:太阳)?活动周|极小附近|预测关系)",
    re.IGNORECASE,
)
_POLAR_EXCLUSION_PATTERN = re.compile(
    r"(?:do not\s+(?:use|include|analyze)|without\s+(?:using|including)|"
    r"exclude\s+(?:the\s+)?|不(?:要)?(?:使用|加入|分析)|不加入|"
    r"排除(?=(?:极区|极地|MWO|WSO|polar))"
    r"[^\n。！？!?；;]{0,30}"
    r"(?:polar[\s-]?field|polar precursor|MWO|WSO|极区磁场|极地磁场|极区场))",
    re.IGNORECASE,
)


def _explicit_polar_data_exclusion(text: str) -> bool:
    """Match data exclusions, not instructions to avoid rerunning a protocol."""

    for sentence in re.split(r"[\n。！？!?；;]", text):
        if re.search(r"(?:调用|重新|重跑|call|invoke|rerun|re-run)", sentence, re.I):
            continue
        if _POLAR_EXCLUSION_PATTERN.search(sentence):
            return True
    return False


_LITERATURE_ONLY_INTENT_PATTERN = re.compile(
    r"(?:纯文献|文献(?:与[^\n。！？!?；;，,]{0,20})?(?:审查|综述|核验)|"
    r"(?:全文|文献|既有资料|已有证据)(?:证据)?(?:综述|审查|核验)|"
    r"(?:综述|审查|核验)(?:既有|已有)?文献|"
    r"literature\s+(?:review|only|synthesis)|literature[- ]only|"
    r"(?:full[- ]text(?:\s+evidence)?|evidence)\s+(?:review|synthesis)|"
    r"review\s+(?:the\s+)?(?:literature|full\s+texts?))",
    re.IGNORECASE,
)
_LITERATURE_ONLY_BOUNDARY_PATTERN = re.compile(
    r"(?:(?:不得|不要|禁止|无需|不必|不需要|不允许|不)\s*"
    r"(?:(?:再|重新|进行|开展|执行|调用|进入|运行|做|启动|任何|额外|新的?|数值|统计)\s*)*"
    r"(?:data\b|solar[- ]?data\b|experiment\b|数据分析|实验|回测|预测|计算|回归|模拟|检验|bootstrap)"
    r"|\b(?:do\s+not|don't|must\s+not|without|no|avoid)\s+"
    r"(?:(?:perform(?:ing)?|run(?:ning)?|do(?:ing)?|conduct(?:ing)?|new|any|additional|numerical|statistical)\s+)*"
    r"(?:data\s+analysis|experiments?|comput(?:ation|e|ing)|calculat(?:ion|e|ing)|"
    r"regression|bootstrap|simulation|backtest(?:ing)?|forecast(?:ing)?|prediction)\b)",
    re.IGNORECASE,
)
_ONLY_LITERATURE_SCOPE = re.compile(
    r"(?:(?:只(?:允许|做|使用|采用)|仅(?:限于?|做|使用|允许|基于)|纯)\s*"
    r"(?:进行|开展)?\s*(?:文献|全文证据|既有资料|已有证据|已发表证据)|"
    r"\b(?:only\s+(?:use\s+)?(?:literature|published\s+evidence)|"
    r"(?:literature|evidence|full[- ]text)\s+(?:review\s+)?only|literature[- ]only)\b)",
    re.IGNORECASE,
)

_FILE_REFERENCE = re.compile(
    r"(?<![\w])(?:[^\s`<>\"'|，。；！？!?()（）\[\]]*[\\/])?"
    r"[^\s`<>\"'|，。；！？!?()（）\[\]\\/]+[.]"
    r"(?:txt|csv|tsv|jsonl?|ya?ml|md|html?|pdf|parquet|fits?|xlsx?|docx?|py|png|jpe?g)\b",
    re.IGNORECASE,
)
_SOURCE_HEADING = re.compile(
    r"(?:来源|参考文献|附件|输入清单|哈希|\bsources?\b|\breferences?\b|"
    r"\battachments?\b|\binput\s+(?:files?|manifest)\b|\bhash(?:es)?\b)",
    re.IGNORECASE,
)
_FILE_COMPUTATION_REQUEST = re.compile(
    r"(?:下游统计|统计分析|相关分析|计算|重算|复现|拟合|回归|"
    r"分析\s*(?:已验证的|上传的|这份)?\s*\[file\]|"
    r"\b(?:compute|recompute|reproduce|calculate|regression|backtest|bootstrap)\b|"
    r"\banaly[sz]e\s+(?:the\s+)?\[file\])",
    re.IGNORECASE,
)
_NEGATED_ACTION = re.compile(
    r"(?:不要|不得|禁止|无需|不必|不用|不需要|不允许|不可|不能|避免|排除|"
    r"不(?=(?:做|作|进行|开展|执行|调用|进入|运行|启动|计算|分析|使用|加入|复现|预测|检验)))"
    r"|\b(?:do\s+not|don't|must\s+not|should\s+not|will\s+not|"
    r"no\s+need\s+to|without|avoid|exclude)\b"
    r"|\bno\s+(?:new\s+)?(?:computation|calculation|experiment|analysis|regression|simulation)",
    re.IGNORECASE,
)
_POSTFIX_NEGATED_ACTION = re.compile(
    r"(?:(?:均|都|也)?(?:不做|不算|不运行|不开展|不进行|不需要|不必|"
    r"不要(?:做|运行|计算|执行|使用))(?:了)?|"
    r"\bnot\s+(?:requested|required|needed|allowed))\s*$",
    re.IGNORECASE,
)
_AFFIRMATIVE_RESTART = re.compile(
    r"^\s*(?:(?:但是|但|而是|改为|转而|同时|请|然后)|"
    r"(?:but|instead|then|please)\s+)*\s*"
    r"(?:分析|复现|重算|计算|比较|检验|回测|拟合|使用|研究|判断|"
    r"analy[sz]e\b|reproduce\b|compute\b|calculate\b|compare\b|run\b|use\b)",
    re.IGNORECASE,
)
_EXPLICIT_COMPUTE_REQUEST = re.compile(
    r"(?:^|[。！？!?；;，,\n])\s*"
    r"(?:(?:请|然后|同时|并|另|还要|再|只|仅|也|先)|"
    r"(?:please|then|also|and|only|next)\s+)*\s*"
    r"(?:复现|重算|计算|拟合|回归|模拟|"
    r"(?:运行|执行)\s*(?:(?:新的?|数值|统计)\s*)?(?:实验|回测|计算|分析|代码|脚本|模型)|"
    r"compute\b|recompute\b|calculate\b|reproduce\b|run\b|simulate\b|fit\b)",
    re.IGNORECASE,
)


def _affirmative_clauses(text: str) -> str:
    """Keep positive commands, without reinterpreting prohibited work as intent."""

    output: list[str] = []
    negated_list = False
    text = re.sub(
        r"(?<!不)(?:但是|但|而是|改为|转而)|\b(?:but|instead|however)\b",
        "; ",
        text,
        flags=re.I,
    )
    for clause in re.split(r"([，,](?!\d)|[。！？!?；;\n]|[.](?!\d))", text):
        if re.fullmatch(r"[，,。！？!?；;\n.]", clause):
            if clause not in {",", "，"}:
                negated_list = False
            output.append(clause)
            continue
        if _POSTFIX_NEGATED_ACTION.search(clause):
            negated_list = True
            continue
        negative = _NEGATED_ACTION.search(clause)
        if negative:
            output.append(clause[: negative.start()])
            negated_list = True
        elif not negated_list or _AFFIRMATIVE_RESTART.search(clause):
            output.append(clause)
            negated_list = False
    return "".join(output)


def routing_request_text(text: str, *, affirmative_only: bool = False) -> str:
    """Return instruction prose for routing, not source inventories or file names.

    This is a routing view only: keep the original message for the research
    agent. A file in an explicit read request becomes ``[file]``. Only an
    affirmative computation request retains its file's basename semantics;
    this preserves requests to analyze a named, verified feature table without
    letting a bare input inventory select a protocol. Negations remain by default.
    """

    lines: list[str] = []
    fenced = False
    source_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^(?:```|~~~)", stripped):
            fenced = not fenced
            continue
        if fenced or stripped.startswith((">", "|")):
            continue
        heading = re.match(r"^#{1,6}\s+(.+)", stripped)
        if heading:
            source_section = bool(_SOURCE_HEADING.search(heading[1]))
            if source_section:
                continue
        if source_section and re.match(r"^(?:[-*+]\s|\d+[.)、]\s*)", stripped):
            continue
        if re.match(
            r"^(?:来源|参考文献|附件|sources?|references?|sha-?256)\s*[:：]",
            stripped,
            re.I,
        ):
            continue
        # A URL or machine-readable identity is evidence metadata, not a verb.
        line = re.sub(r"https?://[^\s<>`]+", "[source]", line, flags=re.I)
        masked = _FILE_REFERENCE.sub("[file]", line)
        # Check the request after masking paths so verbs in a filename cannot
        # authorize computation on their own.
        if _FILE_COMPUTATION_REQUEST.search(_affirmative_clauses(masked)):
            line = _FILE_REFERENCE.sub(
                lambda match: (
                    "[file: "
                    + re.sub(
                        r"[_-]+", " ", match[0].replace("\\", "/").rsplit("/", 1)[-1]
                    )
                    + "]"
                ),
                line,
            )
        else:
            line = masked
        line = re.sub(r"\b[a-f0-9]{32,128}\b", "[hash]", line, flags=re.I)
        line = re.sub(
            r"\b(?:[a-z][a-z0-9]*[-_])+(?:v\d+(?:[.]\d+)*)\b",
            # Keep this explicit protocol after the source/quotation/file
            # filters. Middleware also consumes this routing view, so preserving
            # it only inside detect_analysis_protocol would still lose intent.
            lambda match: (
                B1_FLARE_CASE_PROTOCOL
                if match[0].casefold() == B1_FLARE_CASE_PROTOCOL and not source_section
                else "[source]"
            ),
            line,
            flags=re.I,
        )
        line = re.sub(r"`([^`\n]+)`", lambda m: m[1], line)
        # A bare file/hash list carries no affirmative instruction at all.
        remainder = re.sub(
            r"\[(?:file|source|hash)\]|[\s\d.*+\-:：()（）,，;；|]+", "", line
        )
        if not remainder:
            continue
        lines.append(line)
    prose = "\n".join(lines)
    return _affirmative_clauses(prose) if affirmative_only else prose


_CYCLE_26_PATTERN = re.compile(
    r"(?:第\s*26\s*(?:太阳活动)?周|太阳活动周\s*26|solar\s+cycle\s*26|cycle\s*26)",
    re.IGNORECASE,
)
_CYCLE_26_READINESS_PATTERN = re.compile(
    r"(?:是否.{0,16}(?:启动|发布)|可以启动|暂不启动|正式分类|峰值区间|"
    r"launch|readiness|ready\s+to|formal\s+(?:forecast|classification)|"
    r"prediction.{0,16}(?:start|launch))",
    re.IGNORECASE | re.DOTALL,
)
_CYCLE_26_READINESS_CUTOFF_PATTERN = re.compile(
    r"(?:2026\s*年\s*6\s*月\s*30\s*日|2026[-/.]0?6[-/.]30)",
    re.IGNORECASE,
)
_CYCLE_26_OPERATIONAL_FORECAST_PATTERN = re.compile(
    r"(?:初步.{0,12}(?:概率)?预测|点预测|(?:80|95)\s*%.{0,12}预测区间|"
    r"preliminary.{0,20}(?:probabilistic|probability|operational).{0,12}forecast|"
    r"point\s+forecast|prediction\s+interval)",
    re.IGNORECASE | re.DOTALL,
)
_CYCLE_26_BACKTEST_FORECAST_PATTERN = re.compile(
    r"(?:历史回测|时间顺序回测|historical\s+backtest|chronological\s+backtest)"
    r"[\s\S]{0,500}(?:第\s*26\s*(?:太阳活动)?周|solar\s+cycle\s*26|cycle\s*26)"
    r"|(?:第\s*26\s*(?:太阳活动)?周|solar\s+cycle\s*26|cycle\s*26)"
    r"[\s\S]{0,500}(?:历史回测|时间顺序回测|historical\s+backtest|chronological\s+backtest)",
    re.IGNORECASE,
)


def is_literature_only_request(text: str) -> bool:
    """Return whether a request explicitly scopes itself to literature evidence."""

    text = routing_request_text(text)
    positive = _affirmative_clauses(text)
    # Restricting evidence sources does not prohibit calculations performed on
    # those sources. An affirmative computation command disambiguates "only
    # use literature" from a genuinely literature-only task.
    if _EXPLICIT_COMPUTE_REQUEST.search(positive):
        return False
    if _ONLY_LITERATURE_SCOPE.search(positive):
        return True
    if not _LITERATURE_ONLY_INTENT_PATTERN.search(positive):
        return False
    return bool(_LITERATURE_ONLY_BOUNDARY_PATTERN.search(text))


def detect_analysis_protocol(text: str) -> str:
    """Return the required deterministic analysis protocol for one request."""

    if is_literature_only_request(text):
        return "none"

    # Preserve explicit C commands through dataset-ID masking, while retaining
    # source-section, quotation, negation and affirmative-computation checks.
    text = text.replace("[C_PROTOCOL=silso_gnbb2_annual_v1]", "[CAnnualBounded]")
    text = text.replace(
        "[C_PROTOCOL=silso_cycle_sensitivity_v1]", "[CSensitivityBounded]"
    )
    text = text.replace(
        "[D_PROTOCOL=silso_cycle_associations_v1]", "[DAssociationsBounded]"
    )
    text = routing_request_text(text, affirmative_only=True)

    # This one-case contract is opt-in. Match only the filtered affirmative
    # request, never a source inventory, quoted example, URL or prohibition.
    if re.search(r"(?<![\w-])b1-flare-case-v1(?![\w-])", text, re.IGNORECASE):
        return B1_FLARE_CASE_PROTOCOL

    # Explicit bounded C markers precede the more general A/B vocabulary.
    c_command = re.sub(
        r"\[(?:CAnnualBounded|CSensitivityBounded|C2_BATCH=C2[abc]|C2_BATCH=C1|DAssociationsBounded|D_BATCH=D[12])\]",
        "",
        text,
    )
    c_requested = bool(
        _EXPLICIT_COMPUTE_REQUEST.search(c_command)
        or re.search(r"(?:使用|基于)[^。；;\n]{0,120}(?:计算|重算)", c_command)
    )
    if "[DAssociationsBounded]" in text and c_requested:
        return SILSO_CYCLE_ASSOCIATIONS_PROTOCOL
    if "[CAnnualBounded]" in text and c_requested:
        return SILSO_GNBB2_ANNUAL_PROTOCOL
    if "[CSensitivityBounded]" in text and c_requested:
        return SILSO_CYCLE_SENSITIVITY_PROTOCOL
    if _F107_PATTERN.search(text) and _F107_DISCONTINUITY_PATTERN.search(text):
        return F107_DISCONTINUITY_PROTOCOL
    if _CYCLE_26_BACKTEST_FORECAST_PATTERN.search(text):
        return SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL
    if _CYCLE_26_PATTERN.search(text) and _CYCLE_26_OPERATIONAL_FORECAST_PATTERN.search(
        text
    ):
        return SOLAR_CYCLE_26_READINESS_PROTOCOL
    if (
        _CYCLE_26_PATTERN.search(text)
        and _CYCLE_26_READINESS_PATTERN.search(text)
        and _CYCLE_26_READINESS_CUTOFF_PATTERN.search(text)
    ):
        return SOLAR_CYCLE_26_READINESS_PROTOCOL
    if (
        _POLAR_FIELD_PATTERN.search(text)
        and _POLAR_PRECURSOR_INTENT_PATTERN.search(text)
        and not _explicit_polar_data_exclusion(text)
    ):
        return SOLAR_POLAR_PRECURSOR_PROTOCOL
    if _SILSO_PATTERN.search(text) and re.search(
        r"相位(?:与幅度)?归一化|相位对齐|phase\s+(?:alignment|normali[sz]ation)",
        text,
        re.I,
    ):
        return SILSO_CYCLE_PHASE_PROTOCOL
    if _SILSO_PATTERN.search(text) and _CYCLE_DESCRIPTIVE_PATTERN.search(text):
        return SILSO_CYCLE_DESCRIPTIVE_PROTOCOL
    if _SILSO_PATTERN.search(text) and _SILSO_CYCLE_MORPHOLOGY_PATTERN.search(text):
        return SILSO_CYCLE_MORPHOLOGY_PROTOCOL
    if _SILSO_PATTERN.search(text) and (
        _SILSO_CYCLE_REPRODUCTION_PATTERN.search(text)
        or _SILSO_CYCLE_ENGLISH_PATTERN.search(text)
    ):
        return SILSO_CYCLE_REPRODUCTION_PROTOCOL
    return "none"


def required_data_product_for_protocol(protocol: str) -> str:
    """Return the sole specialized Data product required by one protocol."""

    return {
        B1_FLARE_CASE_PROTOCOL: B1_FLARE_CASE_DATA_PRODUCT,
        SILSO_CYCLE_REPRODUCTION_PROTOCOL: SILSO_CYCLE_EXTREMA_DATA_PRODUCT,
        SILSO_CYCLE_MORPHOLOGY_PROTOCOL: SILSO_CYCLE_MORPHOLOGY_DATA_PRODUCT,
        SILSO_CYCLE_DESCRIPTIVE_PROTOCOL: SILSO_CYCLE_DESCRIPTIVE_DATA_PRODUCT,
        SILSO_CYCLE_PHASE_PROTOCOL: SILSO_CYCLE_PHASE_PROTOCOL,
        SILSO_GNBB2_ANNUAL_PROTOCOL: SILSO_GNBB2_ANNUAL_PROTOCOL,
        SILSO_CYCLE_SENSITIVITY_PROTOCOL: SILSO_CYCLE_SENSITIVITY_PROTOCOL,
        SILSO_CYCLE_ASSOCIATIONS_PROTOCOL: SILSO_CYCLE_ASSOCIATIONS_PROTOCOL,
        SOLAR_CYCLE_26_READINESS_PROTOCOL: SOLAR_CYCLE_26_READINESS_DATA_PRODUCT,
        SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL: SOLAR_CYCLE_26_FORECAST_BACKTEST_DATA_PRODUCT,
        SOLAR_POLAR_PRECURSOR_PROTOCOL: SOLAR_POLAR_PRECURSOR_DATA_PRODUCT,
    }.get(protocol, GENERIC_DATA_PRODUCT)


def required_dataset_ids_for_protocol(protocol: str) -> tuple[str, ...]:
    """Return the registered dataset IDs required by one stable protocol."""

    return {
        B1_FLARE_CASE_PROTOCOL: B1_FLARE_CASE_DATASET_IDS,
        SILSO_CYCLE_REPRODUCTION_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SILSO_CYCLE_MORPHOLOGY_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SILSO_CYCLE_DESCRIPTIVE_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SILSO_CYCLE_PHASE_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SILSO_GNBB2_ANNUAL_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SILSO_CYCLE_SENSITIVITY_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SILSO_CYCLE_ASSOCIATIONS_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SOLAR_CYCLE_26_READINESS_PROTOCOL: SOLAR_CYCLE_26_READINESS_DATASET_IDS,
        SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL: SILSO_CYCLE_REPRODUCTION_DATASET_IDS,
        SOLAR_POLAR_PRECURSOR_PROTOCOL: SOLAR_POLAR_PRECURSOR_DATASET_IDS,
    }.get(protocol, ())


def selected_dataset_ids_from_plan(plan: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the accepted plan's non-empty dataset selections in plan order."""

    return tuple(
        dict.fromkeys(
            str(item["selected_source_id"]).strip()
            for item in plan.get("required_datasets", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("selected_source_id"), str)
            and str(item["selected_source_id"]).strip()
        )
    )


def resolve_required_dataset_ids(
    plan: Mapping[str, Any], protocol: str
) -> tuple[str, ...]:
    """Resolve plan-first dataset authority and reject protocol conflicts."""

    selected = selected_dataset_ids_from_plan(plan)
    protocol_ids = required_dataset_ids_for_protocol(protocol)
    if selected and protocol_ids and set(selected) != set(protocol_ids):
        raise ValueError(
            "Data semantics conflict: accepted plan dataset selections do not "
            f"match the {protocol} protocol mapping"
        )
    return selected or protocol_ids


def plan_dataset_selection_conflicts_protocol(
    plan: Mapping[str, Any], protocol: str
) -> bool:
    """Return whether explicit plan selections conflict with protocol authority."""

    selected = selected_dataset_ids_from_plan(plan)
    protocol_ids = required_dataset_ids_for_protocol(protocol)
    return bool(selected and protocol_ids and set(selected) != set(protocol_ids))


def render_silso_cycle_reproduction_markdown(payload: Mapping[str, Any]) -> str:
    """Render the accepted SILSO comparison without another model generation."""

    if not (
        payload.get("schema_version") == "silso-cycle-reproduction-v1"
        and payload.get("analysis_protocol") == SILSO_CYCLE_REPRODUCTION_PROTOCOL
        and payload.get("cycles") == [21, 22, 23, 24]
    ):
        raise ValueError("invalid SILSO cycle reproduction payload")
    rows = payload.get("comparison")
    if not isinstance(rows, list) or len(rows) != 4:
        raise ValueError("SILSO comparison must contain cycles 21 through 24")

    def extremum(row: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = row.get(key)
        if not isinstance(value, Mapping):
            raise ValueError(f"invalid {key}")
        return value

    table_rows: list[str] = []
    differences: list[str] = []
    strengths: list[tuple[int, float]] = []
    rise_times: list[tuple[int, int]] = []
    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            raise ValueError("invalid SILSO comparison row")
        cycle = int(raw_row["cycle"])
        official_minimum = extremum(raw_row, "official_minimum")
        official_maximum = extremum(raw_row, "official_maximum")
        rise_months = int(raw_row["official_rise_months"])
        minimum_value = float(official_minimum["sunspot_number"])
        maximum_value = float(official_maximum["sunspot_number"])
        table_rows.append(
            f"| {cycle} | {official_minimum['year_month']} | {minimum_value:.1f} | "
            f"{official_maximum['year_month']} | {maximum_value:.1f} | "
            f"{rise_months} 个月 |"
        )
        strengths.append((cycle, maximum_value))
        rise_times.append((cycle, rise_months))
        if not (
            raw_row.get("minimum_matches_official") is True
            and raw_row.get("maximum_matches_official") is True
        ):
            recomputed_minimum = extremum(raw_row, "recomputed_minimum")
            recomputed_maximum = extremum(raw_row, "recomputed_maximum")
            if (
                raw_row.get("minimum_matches_official") is False
                and float(recomputed_minimum["sunspot_number"]) == minimum_value
            ):
                difference_note = (
                    "重算序列在相同最小平滑值的平台期选择了不同月份；"
                    "两者均保留，主表采用官方年月。"
                )
            elif raw_row.get("maximum_matches_official") is False:
                difference_note = (
                    "官方极大与序列重算极大不一致；两者均保留，主表采用官方值。"
                )
            else:
                difference_note = "官方值与序列重算值不一致；两者均保留。"
            differences.append(
                f"| {cycle} | {official_minimum['year_month']} / "
                f"{minimum_value:.1f} | {recomputed_minimum['year_month']} / "
                f"{float(recomputed_minimum['sunspot_number']):.1f} | "
                f"{official_maximum['year_month']} / {maximum_value:.1f} | "
                f"{recomputed_maximum['year_month']} / "
                f"{float(recomputed_maximum['sunspot_number']):.1f} | "
                f"{rise_months} / {int(raw_row['recomputed_rise_months'])} 个月 | "
                f"{difference_note} |"
            )

    strength_order = " > ".join(
        f"周期 {cycle}"
        for cycle, _value in sorted(strengths, key=lambda item: item[1], reverse=True)
    )
    weakest = min(strengths, key=lambda item: item[1])
    fastest = min(rise_times, key=lambda item: item[1])
    slowest = max(rise_times, key=lambda item: item[1])
    sections = [
        "## 第 21–24 太阳活动周历史复现",
        "",
        "数据源：WDC-SILSO Sunspot Number Version 2.0 的官方 13 个月平滑月均总太阳黑子数和官方周期极值表。",
        "",
        "计算方法：以官方极小和极大年月为周期标记，并按日历月份差计算从极小到极大的上升时间。主表采用官方极值；序列重算结果仅用于一致性检查。",
        "",
        "| 周期 | 官方极小年月 | 极小值 | 官方极大年月 | 峰值 | 上升时间 |",
        "| --- | --- | ---: | --- | ---: | ---: |",
        *table_rows,
    ]
    if differences:
        sections.extend(
            [
                "",
                "### 官方值与序列重算的差异",
                "",
                "| 周期 | 官方极小 | 重算极小 | 官方极大 | 重算极大 | 官方/重算上升时间 | 说明 |",
                "| --- | --- | --- | --- | --- | --- | --- |",
                *differences,
            ]
        )
    sections.extend(
        [
            "",
            "### 直接比较",
            "",
            f"- 强度排序：{strength_order}。",
            f"- 周期 {weakest[0]} 是四个周期中最弱的。",
            f"- 周期 {fastest[0]} 上升最快（{fastest[1]} 个月）。",
            f"- 周期 {slowest[0]} 上升最慢（{slowest[1]} 个月）。",
            "- 以上仅为历史描述，不涉及周期 26 的预测。",
        ]
    )
    return "\n".join(sections)


def f107_discontinuity_directive() -> str:
    """Return the experiment-facing contract for the F10.7 discontinuity task."""

    measurement_ids = ", ".join(F107_DISCONTINUITY_REQUIRED_MEASUREMENTS)
    return (
        "Implement f107_discontinuity_v1 from the verified dataset manifest. "
        "Model F10.7 as the response over the common pre/post sunspot-number "
        "support; never invert an SN-on-F10.7 OLS slope. Compute the relative "
        "F10.7 scale jump at a fixed reference such as SN=100 and store it as "
        "f107_relative_scale_jump, then compare that computed value with the "
        "published approximately 10.5% discontinuity without forcing agreement. "
        "Keep the fixed 1980-1981 comparison confirmatory. For exploratory "
        "breakpoint scans, use an upper-tail survival probability and choose the "
        "maximum F statistic or minimum unrestricted SSR, not the first p-value "
        "that underflows to zero. Define cross-period residuals as "
        "actual-minus-predicted. Include observed/URSI product, low-activity, and "
        "20/25-observed-day monthly-coverage sensitivities. Estimate long-term "
        "trend and instantaneous step in a joint model before comparing them. "
        f"Emit every required measurement id: {measurement_ids}."
    )


def solar_polar_precursor_directive() -> str:
    """Return the experiment contract for cycle-level polar precursor tests."""

    return (
        "Use only the verified solar_precursor_cycle_features.csv table and its "
        "SolarPrecursorFeatureRecordV1 lineage. Treat one adjacent solar-cycle "
        "pair as the independent sample unit; monthly rows must never inflate "
        "the sample count. Predict target cycle N+1 only from information available "
        "around the ending minimum of cycle N. Run the fixed rolling-origin "
        "tournament with five "
        "initial training cycles, then predict each later cycle in chronological "
        "order. Refit all preprocessing and models inside each training fold. "
        "Compare the low-dimensional polar precursor model with both a "
        "training-mean baseline and a persistence baseline, and report every fold, "
        "MAE and RMSE. Use cycle-level paired bootstrap with seed 20260828 and "
        "10000 repetitions, plus leave-one-cycle results. Report MWO proxy and "
        "WSO magnetograph measurement-regime sensitivities separately. Test "
        "interaction and moderation terms with a two-sided, sign-neutral rule; do "
        "not preselect a positive, negative, or otherwise preferred interaction "
        "direction or scientific outcome. Classify "
        "skill_supported only when MAE improvement over the training-mean baseline "
        "is positive, its 95% interval has a positive lower bound, and the "
        "pre-registered regime check does not reverse direction. Compare an "
        "axial-dipole predictor only when a registered axial-dipole product or a "
        "fixed harmonic from registered synoptic maps is present; otherwise return "
        "blocked_by_data for H3 without relabeling the polar aperture field."
    )


def silso_cycle_morphology_directive() -> str:
    """Return the fixed contract for the independent cycles 1--24 experiment."""

    return (
        "Run silso_cycle_morphology_v1 only from the three bound SILSO v2.0 "
        "inputs. Use official minima, maxima, and next-minimum boundaries for "
        "completed cycles 1-24; C25 is a boundary for C24 only and is not a "
        "sample. Write outputs/cycle_morphology_strength_report.md, "
        "outputs/cycle_morphology_table.csv, and "
        "outputs/cycle_morphology_relationships.png. The CSV must have exactly "
        "24 cycle rows and the declared fields. Compute calendar-month differences "
        "divided by 12, peak smoothed number from the official maximum, Pearson "
        "and Spearman two-sided p-values, cycle-level bootstrap intervals with "
        "seed 20260826 and 10000 requested repetitions, leave-one-cycle-out "
        "results, and fixed early (1-12) versus modern (13-24) comparisons. "
        "In experiment_design, bind and inspect the staged registered sources plus "
        "the accepted morphology table, then call "
        "automatic_experiment_create_silso_morphology_design exactly once; do not "
        "author a generic compact or expanded design. In experiment_result, inspect "
        "the accepted run, call automatic_experiment_prepare_silso_morphology_attempt "
        "exactly once, execute that returned attempt, obtain the verification preview, "
        "submit the evidence-bounded scientific assessment, and finalize. "
        "Planned output paths are not evidence_refs until the files actually exist "
        "and are inspected; keep planning-stage evidence_refs limited to registered "
        "inputs and existing receipts. Do not search for local peaks, use polar-field/F10.7 data, browse, or "
        "make causal dynamo claims. Use claim-specific confidence in the reader-facing "
        "synthesis: directly verified source reconstruction, official date bindings, "
        "the 24-row table, and deterministic cross-checks may reach high confidence. "
        "The bounded historical rise-time association may be reported as medium-high "
        "when Pearson and Spearman agree, both bootstrap intervals exclude zero, all "
        "leave-one-cycle-out directions agree, and both fixed subgroup directions agree. "
        "Keep cycle-length and decline-time claims lower when intervals cross zero or "
        "periods disagree. This calibration must never upgrade a causal mechanism. "
        "During the post-result hypothesis update, bind the verified experiment evidence, "
        "update and read the persistent draft, run the tail review, record the analysis "
        "claim, and checkpoint the draft; do not call scientific_hypothesis_validate_response, "
        "which is only a legacy one-shot compatibility path. "
        "Do not return success until all three files "
        "exist, the CSV has 24 rows, and the PNG opens."
    )


def solar_cycle_26_readiness_directive() -> str:
    """Return the evidence boundary for the SC26 forecast launch gate."""

    return (
        "Apply the 2026-06-30 evidence cutoff. Treat SILSO sunspot number and "
        "monthly F10.7 as cycle-25 state indicators, not cycle-26 precursors. "
        "Require an established cycle-25/26 minimum and same-definition polar-"
        "field observations near that minimum before releasing a formal cycle-26 "
        "strength class or testable peak interval. Preserve explicit WSO missing "
        "rows as an observed evidence gap. Historical MWO/WSO cycle pairs provide "
        "calibration context only and do not substitute for the current precursor. "
        "Bind each raw parser to the actual declared product rather than guessing "
        "from its extension: the SILSO smoothed product is semicolon-delimited; "
        "the monthly-total and cycle-extrema products use fixed whitespace columns, "
        "and the cycle-extrema rows do not repeat the words Minimum or Maximum. "
        "The MWO/WSO CSV is a 12-column annual calibration history with separate "
        "decimal-year date, field, and uncertainty columns for north and south; it "
        "is not the current 10-day WSO feed. Polar.html contains the 10-day current "
        "WSO observations and explicit XXX missing rows. Use the accepted data "
        "inventory only as parser-validation anchors: the raw smoothed series must "
        "recover the non-missing 2026-01 value 104.2, while the raw Polar.html must "
        "recover the last valid observation 2026-01-09 and 17 explicit XXX rows "
        "through the cutoff. If a non-empty declared source parses to zero relevant "
        "rows or disagrees with those anchors, raise a technical failure before "
        "forming any scientific result. The anchors are checks only: derive the "
        "reported values from the raw bytes, never hard-code them as results. "
        "When these conditions are unmet, carry the verified data inventory through "
        "competition, experiment, and opposing-evidence review and return an honest "
        "not-ready decision with concrete re-evaluation triggers for a final narrow "
        "precursor forecast. If the user instead requests a preliminary operational "
        "probability forecast, the workflow must not return only a not-ready decision. "
        "Use a single target throughout: the peak 13-month smoothed SILSO v2 sunspot "
        "number. Compare target-compatible published scenarios with a reproducible "
        "historical baseline, keep shared-data model families non-independent, and "
        "quantify model discrepancy. The reviewed release must report a point forecast, "
        "80% and 95% prediction intervals, peak-time distribution, comparison with "
        "cycles 24 and 25, and observation-triggered update rules. Use claim-specific "
        "confidence: official target definitions, source identity, and deterministic "
        "reproduction may reach high confidence when directly verified; the future "
        "amplitude, timing, narrow interval, or causal mechanism must not be upgraded "
        "merely to satisfy a requested confidence label."
    )


def solar_cycle_26_forecast_backtest_directive() -> str:
    """Return the bounded contract for historical SC26 forecast validation."""

    return (
        "Run the dedicated solar_cycle_26_forecast_backtest_v1 product from the three "
        "bound SILSO v2.0 inputs only. First complete the chronological historical "
        "backtest for target cycles 1-24 without future leakage, then fit the formal "
        "Cycle 26 forecast using the observed Cycle 25 state. Persist the script's "
        "CSV, JSON, Markdown, PNG, and a verified receipt under the task workspace. "
        "Use cycle-level rows as independent units, fixed seed 20260827 and 10000 "
        "bootstrap repetitions, report MAE/RMSE against the training-mean baseline, "
        "and retain negative or low-skill results. Cycle 25 is a predictor only, not "
        "a completed backtest target; no causal dynamo claim is allowed. Do not use "
        "F10.7, polar-field data, unregistered files, or guessed paths. Do not return "
        "success until all canonical output files exist and the receipt is hash-bound. "
        "In experiment_design, bind and inspect the staged forecast feature, prediction, "
        "formal-forecast, summary, and manifest files, then call "
        "automatic_experiment_create_sc26_forecast_design exactly once; do not author "
        "a generic compact or expanded design. In experiment_result, resume the accepted "
        "run, call automatic_experiment_prepare_sc26_forecast_attempt exactly once, "
        "execute that returned attempt, obtain the verification preview, submit an "
        "evidence-bounded scientific assessment, and finalize. Directly verified source "
        "identity and deterministic reproduction of the negative backtest may be high "
        "confidence; the future Cycle 26 amplitude remains low confidence when the "
        "candidate does not outperform baseline or its improvement interval crosses zero."
    )


def sha256_file(path: Path) -> str:
    """Hash one immutable artifact without loading it fully into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetSemanticManifest:
    """Hash-bound semantic description of one canonicalized dataset."""

    manifest_id: str
    input_path: str
    input_sha256: str
    adapter_id: str
    adapter_version: str
    product_id: str
    product_version: str
    column_bindings: Mapping[str, str]
    unit: str
    observation_grain: str
    time_column: str
    primary_key: tuple[str, ...]
    duplicate_policy: str
    missing_policy: str
    quality_policy: str
    aggregation_plan: tuple[str, ...]
    coverage_start: str
    coverage_end: str
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    excluded_inputs: tuple[Mapping[str, str], ...] = ()
    limitations: tuple[str, ...] = ()
    analysis_requirements: tuple[str, ...] = ()
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["column_bindings"] = dict(self.column_bindings)
        value["primary_key"] = list(self.primary_key)
        value["aggregation_plan"] = list(self.aggregation_plan)
        value["diagnostics"] = dict(self.diagnostics)
        value["excluded_inputs"] = [dict(row) for row in self.excluded_inputs]
        value["limitations"] = list(self.limitations)
        value["analysis_requirements"] = list(self.analysis_requirements)
        return value


__all__ = [
    "SILSO_CYCLE_PHASE_PROTOCOL",
    "SILSO_GNBB2_ANNUAL_PROTOCOL",
    "SILSO_CYCLE_SENSITIVITY_PROTOCOL",
    "SILSO_CYCLE_ASSOCIATIONS_PROTOCOL",
    "SILSO_CYCLE_DESCRIPTIVE_PROTOCOL",
    "SILSO_CYCLE_DESCRIPTIVE_DATA_PRODUCT",
    "B1_FLARE_CASE_PROTOCOL",
    "B1_FLARE_CASE_DATA_PRODUCT",
    "B1_FLARE_CASE_DATASET_IDS",
    "F107_DISCONTINUITY_PROTOCOL",
    "F107_DISCONTINUITY_REQUIRED_MEASUREMENTS",
    "GENERIC_DATA_PRODUCT",
    "SILSO_CYCLE_EXTREMA_DATA_PRODUCT",
    "SILSO_CYCLE_MORPHOLOGY_DATA_PRODUCT",
    "SILSO_CYCLE_MORPHOLOGY_PROTOCOL",
    "SILSO_CYCLE_REPRODUCTION_DATASET_IDS",
    "SILSO_CYCLE_REPRODUCTION_PROTOCOL",
    "SOLAR_CYCLE_26_READINESS_DATASET_IDS",
    "SOLAR_CYCLE_26_READINESS_DATA_PRODUCT",
    "SOLAR_CYCLE_26_READINESS_PROTOCOL",
    "SOLAR_CYCLE_26_FORECAST_BACKTEST_PROTOCOL",
    "SOLAR_CYCLE_26_FORECAST_BACKTEST_DATA_PRODUCT",
    "SOLAR_POLAR_PRECURSOR_DATASET_IDS",
    "SOLAR_POLAR_PRECURSOR_DATA_PRODUCT",
    "SOLAR_POLAR_PRECURSOR_PROTOCOL",
    "DatasetSemanticManifest",
    "detect_analysis_protocol",
    "is_literature_only_request",
    "routing_request_text",
    "f107_discontinuity_directive",
    "plan_dataset_selection_conflicts_protocol",
    "render_silso_cycle_reproduction_markdown",
    "required_data_product_for_protocol",
    "required_dataset_ids_for_protocol",
    "resolve_required_dataset_ids",
    "selected_dataset_ids_from_plan",
    "sha256_file",
    "solar_cycle_26_readiness_directive",
    "solar_cycle_26_forecast_backtest_directive",
    "solar_polar_precursor_directive",
    "silso_cycle_morphology_directive",
]
