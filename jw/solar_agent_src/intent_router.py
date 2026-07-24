from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from chat_session import ChatSession
from piagent_schemas import PiAgentRequest


STAT_KEYWORDS: dict[str, set[str]] = {
    "describe": {"描述", "describe", "概况", "summary", "概览", "info", "什么样", "如何"},
    "head": {"head", "前", "开头", "preview", "前几行"},
    "tail": {"tail", "后", "结尾", "最后", "后几行"},
    "column_stats": {
        "均值", "平均", "mean", "中位数", "median", "标准差", "std", "统计", "stats",
        "describe column", "分布", "最大值", "最小值", "极值",
    },
    "corr": {"相关", "correlation", "corr", "关系", "关联", "相关系数"},
    "drift": {"漂移", "drift", "稳定性", "跨周期", "跨时间", "随时间变化"},
    "value_counts": {"频次", "value_counts", "计数", "count", "分布"},
    "groupby": {"分组", "groupby", "group by", "aggregate", "按...统计"},
    "query": {"query", "查询", "计算", "算一下"},
    "process": {"process", "清洗", "处理", "生成特征", "特征工程"},
    "quality": {"质量", "quality", "检查", "问题", "缺失", "异常", "重复"},
    "clean": {"clean", "清洗", "清洗建议", "cleaning", "数据清洗"},
    "apply_cleaning": {"apply_cleaning", "执行清洗", "确认清洗"},
    "generate_features": {"features", "generate_features", "特征", "生成特征", "特征工程"},
    "experiment_handoff": {"handoff", "experiment_handoff", "交接", "实验交接"},
    "strategy_recommendation": {"recommend", "strategy", "推荐", "策略", "实验设计"},
    "ask": {"为什么", "解释", "说明", "分析", "如何看待", "告诉我", "?", "？"},
}



INTENT_REQUIRES_COLUMNS: dict[str, tuple[int, str]] = {
    "describe": (0, "describe"),
    "head": (0, "head"),
    "tail": (0, "tail"),
    "column_stats": (1, "column_stats"),
    "corr": (2, "corr"),
    "drift": (2, "drift"),
    "value_counts": (1, "value_counts"),
    "groupby": (2, "groupby"),
    "query": (1, "dataset_query"),
    "process": (0, "process"),
    "quality": (0, "analyze_quality"),
    "clean": (0, "propose_cleaning"),
    "apply_cleaning": (0, "apply_cleaning"),
    "generate_features": (0, "generate_features"),
    "experiment_handoff": (0, "experiment_handoff"),
    "strategy_recommendation": (0, "strategy_recommendation"),
    "ask": (0, "ask_agent"),
}



@dataclass
class Intent:
    """Structured intent classification result."""

    intent: str
    columns: list[str] = field(default_factory=list)
    confidence: float = 0.0
    requires_confirmation: bool = False
    missing_param: str | None = None
    clarification_question: str | None = None
    piagent_request: PiAgentRequest | None = None
    chat_action: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "columns": self.columns,
            "confidence": round(self.confidence, 4),
            "requires_confirmation": self.requires_confirmation,
            "missing_param": self.missing_param,
            "clarification_question": self.clarification_question,
            "chat_action": self.chat_action,
        }


# Quoted strings capture spaces and Chinese characters.
QUOTED_COLUMN_PATTERN = re.compile(r"[\"']([^\"']+?)[\"']")
# Simple identifiers for unquoted columns.
IDENTIFIER_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def _extract_quoted_columns(text: str) -> list[str]:
    return [m.group(1) for m in QUOTED_COLUMN_PATTERN.finditer(text)]


def _extract_identifiers(text: str) -> list[str]:
    return IDENTIFIER_PATTERN.findall(text)


def _extract_columns(text: str, df_columns: set[str]) -> list[str]:
    """Extract dataset columns from user input.

    Priority:
    1. Quoted strings (supports spaces and Chinese names).
    2. Exact matches against df_columns for unquoted tokens.
    """
    matched: list[str] = []
    seen = set()

    # 1. Quoted columns are taken as-is.
    for col in _extract_quoted_columns(text):
        if col not in seen:
            matched.append(col)
            seen.add(col)

    # 2. Unquoted identifiers that exactly match dataset columns.
    for token in _extract_identifiers(text):
        if token in df_columns and token not in seen:
            matched.append(token)
            seen.add(token)

    return matched


def _contains_any(text: str, keywords: set[str]) -> bool:
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in keywords)


def _score_intent(user_input: str, df_columns: set[str]) -> tuple[str, list[str], float, str | None]:
    """Return (intent, matched_columns, confidence, missing_param)."""
    columns = _extract_columns(user_input, df_columns)
    matched_cols = list(columns)

    # Determine primary intent from keyword matches.
    intent_scores: dict[str, float] = {}
    for intent, keywords in STAT_KEYWORDS.items():
        if _contains_any(user_input, keywords):
            # Base keyword score.
            score = 0.7
            # Boost if exact Chinese keyword appears (basic heuristic).
            for kw in keywords:
                if kw in user_input:
                    score = max(score, 0.85)
                    break
            intent_scores[intent] = score

    if not intent_scores:
        # Fallback to ask_agent when no keyword matches.
        return "ask", matched_cols, 0.0, None

    # Pick the intent with the highest score.
    best_intent = max(intent_scores, key=intent_scores.get)
    base_score = intent_scores[best_intent]

    required_cols, _ = INTENT_REQUIRES_COLUMNS[best_intent]
    if required_cols == 0:
        return best_intent, matched_cols, min(0.99, base_score + 0.05 * len(matched_cols)), None

    if len(matched_cols) >= required_cols:
        confidence = min(0.99, base_score + 0.1 * required_cols)
        return best_intent, matched_cols[:required_cols], confidence, None

    # Missing required columns.
    missing_count = required_cols - len(matched_cols)
    confidence = max(0.3, base_score - 0.15 * missing_count)
    return best_intent, matched_cols, confidence, "missing_columns"


def _build_clarification_question(intent: str, columns: list[str], df_columns: list[str]) -> str:
    if intent == "corr":
        if len(columns) == 0:
            return "你想计算哪两个字段的相关性？"
        return f"你想计算 {columns[0]} 与哪一个字段的相关性？"
    if intent == "drift":
        if len(columns) == 0:
            return "你想分析哪两个字段的跨时间关系？"
        return f"你想分析 {columns[0]} 与哪一个字段的跨时间关系？"
    if intent == "column_stats":
        return f"你想查看哪个字段的统计信息？可用字段：{', '.join(df_columns)}"
    if intent == "value_counts":
        return f"你想查看哪个字段的频次分布？可用字段：{', '.join(df_columns)}"
    if intent == "groupby":
        return f"你想按哪个字段分组统计？可用字段：{', '.join(df_columns)}"
    if intent == "query":
        return "你想计算哪个字段？可用字段：{0}".format(", ".join(df_columns))
    return "请补充更多信息。"


def _build_piagent_request(intent: str, columns: list[str]) -> PiAgentRequest:
    if intent == "describe":
        return PiAgentRequest(task="dataset_stats", action="describe")
    if intent == "head":
        return PiAgentRequest(task="dataset_stats", action="head")
    if intent == "tail":
        return PiAgentRequest(task="dataset_stats", action="tail")
    if intent == "column_stats":
        return PiAgentRequest(task="dataset_stats", action="column_stats", column=columns[0] if columns else None)
    if intent in {"corr", "drift"}:
        col_arg = " ".join(columns[:2]) if columns else ""
        return PiAgentRequest(task="dataset_stats", action=intent, column=col_arg)
    if intent == "value_counts":
        return PiAgentRequest(task="dataset_stats", action="value_counts", column=columns[0] if columns else None)
    if intent == "groupby":
        col_arg = f"{columns[0]} mean" if columns else ""
        return PiAgentRequest(task="dataset_stats", action="groupby", column=col_arg)
    if intent == "query":
        # Convert natural-language query intent to a predefined function call if possible.
        return PiAgentRequest(task="dataset_query", query=columns[0] if columns else "")
    if intent == "process":
        return PiAgentRequest(task="process_dataset")
    if intent == "quality":
        return PiAgentRequest(task="analyze_quality")
    if intent == "clean":
        return PiAgentRequest(task="propose_cleaning")
    if intent == "apply_cleaning":
        return PiAgentRequest(task="apply_cleaning")
    if intent == "generate_features":
        return PiAgentRequest(task="generate_features")
    if intent == "experiment_handoff":
        return PiAgentRequest(task="experiment_handoff")
    if intent == "strategy_recommendation":
        return PiAgentRequest(task="strategy_recommendation")
    return PiAgentRequest(task="ask_agent", question="")


def _parse_slash_command(user_input: str) -> Intent:
    """Parse slash-style commands into an Intent with full confidence."""
    parts = user_input.strip().split()
    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    if command == "/help":
        return Intent(intent="help", confidence=1.0, piagent_request=PiAgentRequest(task="chat", action="help"))

    if command == "/load":
        if not args:
            return Intent(
                intent="load_dataset",
                confidence=0.5,
                missing_param="upload_path",
                clarification_question="请提供 CSV 文件路径：/load <csv_path>",
            )
        return Intent(
            intent="load_dataset",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="load_dataset", upload_path=" ".join(args)),
        )

    if command in {"/align", "/merge"}:
        return Intent(
            intent="align_uploads",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="align_uploads"),
        )

    if command in {"/prepare_upload_features", "/upload_features"}:
        upload_path = " ".join(args) if args else None
        return Intent(
            intent="prepare_features_for_upload",
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="prepare_features_for_upload", upload_path=upload_path
            ),
        )

    if command == "/describe":
        return Intent(intent="describe", confidence=1.0, piagent_request=PiAgentRequest(task="dataset_stats", action="describe"))

    if command == "/head":
        return Intent(
            intent="head",
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="dataset_stats", action="head", column=args[0] if args else "5"
            ),
        )

    if command == "/tail":
        return Intent(
            intent="tail",
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="dataset_stats", action="tail", column=args[0] if args else "5"
            ),
        )

    if command == "/stats":
        return Intent(
            intent="column_stats",
            columns=[args[0]] if args else [],
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="dataset_stats", action="column_stats", column=args[0] if args else None
            ),
        )

    if command == "/corr":
        if len(args) < 2:
            return Intent(
                intent="corr",
                columns=args[:1],
                confidence=1.0,
                missing_param="second_column",
                clarification_question="请提供两个字段：/corr <col1> <col2>",
            )
        return Intent(
            intent="corr",
            columns=[args[0], args[1]],
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="dataset_stats", action="corr", column=f"{args[0]} {args[1]}"
            ),
        )

    if command == "/value_counts":
        if not args:
            return Intent(
                intent="value_counts",
                confidence=1.0,
                missing_param="column",
                clarification_question="请提供字段名：/value_counts <col>",
            )
        return Intent(
            intent="value_counts",
            columns=[args[0]],
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="dataset_stats", action="value_counts", column=args[0]
            ),
        )

    if command == "/groupby":
        if len(args) < 2:
            return Intent(
                intent="groupby",
                columns=[args[0]] if args else [],
                confidence=1.0,
                missing_param="aggregation_or_group_column",
                clarification_question="请提供分组字段和聚合方式：/groupby <col> <agg>",
            )
        return Intent(
            intent="groupby",
            columns=[args[0], args[1]],
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="dataset_stats", action="groupby", column=f"{args[0]} {args[1]}"
            ),
        )

    if command == "/drift":
        if len(args) < 2:
            return Intent(
                intent="drift",
                columns=[args[0]] if args else [],
                confidence=1.0,
                missing_param="second_column",
                clarification_question="请提供两个字段：/drift <col1> <col2> [group]",
            )
        col_part = " ".join(args[:3]) if len(args) >= 3 else " ".join(args[:2])
        return Intent(
            intent="drift",
            columns=[args[0], args[1]],
            confidence=1.0,
            piagent_request=PiAgentRequest(
                task="dataset_stats", action="drift", column=col_part
            ),
        )

    if command == "/query":
        if not args:
            return Intent(
                intent="query",
                confidence=1.0,
                missing_param="expression",
                clarification_question="请提供查询表达式：/query <function>(<column>, ...)",
            )
        return Intent(
            intent="query",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="dataset_query", query=" ".join(args)),
        )

    if command == "/quality":
        return Intent(
            intent="quality",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="analyze_quality"),
        )

    if command == "/clean" or command == "/propose_cleaning":
        return Intent(
            intent="clean",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="propose_cleaning"),
        )

    if command == "/apply_cleaning":
        return Intent(
            intent="apply_cleaning",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="apply_cleaning"),
        )

    if command == "/features" or command == "/generate_features":
        return Intent(
            intent="generate_features",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="generate_features"),
        )

    if command == "/handoff":
        return Intent(
            intent="experiment_handoff",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="experiment_handoff"),
        )

    if command == "/recommend":
        return Intent(
            intent="strategy_recommendation",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="strategy_recommendation"),
        )

    if command == "/feature_registry":
        return Intent(
            intent="feature_registry",
            confidence=1.0,
            chat_action={"action": "feature_registry"},
        )

    if command == "/validate_features":
        return Intent(
            intent="validate_features",
            confidence=1.0,
            chat_action={"action": "validate_features"},
        )

    if command == "/domain_rules":
        return Intent(
            intent="domain_rules",
            confidence=1.0,
            chat_action={"action": "domain_rules"},
        )

    if command == "/cleaning_report":
        return Intent(
            intent="cleaning_report",
            confidence=1.0,
            chat_action={"action": "cleaning_report"},
        )

    if command == "/set":
        if len(args) < 2:
            return Intent(
                intent="set",
                confidence=1.0,
                missing_param="set_expression",
                clarification_question="用法：/set column <semantic>=<col> 或 /set coverage <key>=<value>",
            )
        subcommand = args[0].lower()
        rest = args[1:]
        if subcommand == "column":
            if not rest or "=" not in rest[0]:
                return Intent(
                    intent="set",
                    confidence=1.0,
                    missing_param="column_map",
                    clarification_question="用法：/set column f107=my_f107_col",
                )
            mapping = {}
            for item in rest:
                if "=" in item:
                    semantic, col = item.split("=", 1)
                    mapping[semantic.strip()] = col.strip()
            return Intent(
                intent="set",
                confidence=1.0,
                chat_action={"action": "set_column", "mapping": mapping},
            )
        if subcommand == "coverage":
            if not rest or "=" not in rest[0]:
                return Intent(
                    intent="set",
                    confidence=1.0,
                    missing_param="coverage_rule",
                    clarification_question="用法：/set coverage f107_start=1947-02-01",
                )
            mapping = {}
            for item in rest:
                if "=" in item:
                    key, value = item.split("=", 1)
                    mapping[key.strip()] = value.strip()
            return Intent(
                intent="set",
                confidence=1.0,
                chat_action={"action": "set_coverage", "mapping": mapping},
            )
        return Intent(
            intent="unknown",
            confidence=0.0,
            clarification_question=f"未知 /set 子命令：{subcommand}。支持 column / coverage。",
        )

    if command == "/ask":
        if not args:
            return Intent(
                intent="ask",
                confidence=1.0,
                missing_param="question",
                clarification_question="请提供你的问题：/ask <question>",
            )
        return Intent(
            intent="ask",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="ask_agent", question=" ".join(args)),
        )

    if command in {"/clear", "/reset"}:
        return Intent(
            intent="clear",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="chat", action="clear"),
        )

    if command in {"/exit", "/quit"}:
        return Intent(
            intent="exit",
            confidence=1.0,
            piagent_request=PiAgentRequest(task="chat", action="exit"),
        )

    return Intent(
        intent="unknown",
        confidence=0.0,
        clarification_question=f"未知命令：{command}。输入 /help 查看可用命令。",
    )


def route_intent(user_input: str, session: ChatSession | None = None) -> Intent:
    """Convert user input into a structured Intent.

    Slash commands are parsed explicitly with full confidence. Natural language
    is classified against keyword groups and column availability. If required
    columns are missing, the returned Intent contains a clarification question
    instead of a PiAgentRequest.
    """
    if session is None:
        session = ChatSession()

    if user_input.startswith("/"):
        return _parse_slash_command(user_input)

    inspection = session.get_inspection_summary()
    df_columns: set[str] = set()
    df_column_list: list[str] = []
    if inspection and inspection.get("columns"):
        df_column_list = list(inspection["columns"])
        df_columns = set(df_column_list)

    intent, columns, confidence, missing_param = _score_intent(user_input, df_columns)
    requires_confirmation = confidence < 0.6 and confidence > 0.3

    if missing_param:
        return Intent(
            intent=intent,
            columns=columns,
            confidence=confidence,
            missing_param=missing_param,
            clarification_question=_build_clarification_question(intent, columns, df_column_list),
        )

    if intent == "ask":
        # Explicit ask intent (e.g., "为什么...")
        return Intent(
            intent=intent,
            columns=columns,
            confidence=confidence,
            piagent_request=PiAgentRequest(task="ask_agent", question=user_input),
        )

    return Intent(
        intent=intent,
        columns=columns,
        confidence=confidence,
        requires_confirmation=requires_confirmation,
        piagent_request=_build_piagent_request(intent, columns),
    )
