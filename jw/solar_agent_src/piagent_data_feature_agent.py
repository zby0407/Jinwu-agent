from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bailian_data_feature_agent import BailianDataFeatureAgent
from chat_session import ChatSession
from intent_router import route_intent
from piagent_schemas import DEFAULT_DATA_SCOPE, PiAgentRequest
from piagent_tools import run_chat_request, run_piagent_request
import upload_column_splitter


def parse_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got {value!r}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PiAgent adapter for the Solar-Cycle data feature workflow.",
    )
    parser.add_argument(
        "--request-json", help="Optional path to a PiAgent request JSON file."
    )
    parser.add_argument(
        "--task",
        default="prepare_features",
        help="Task type for the data feature sub-agent.",
    )
    parser.add_argument(
        "--target",
        default="cycle_prediction",
        help="Downstream target, e.g. cycle_prediction.",
    )
    parser.add_argument("--question", help="Question for task=ask_agent.")
    parser.add_argument(
        "--session-id", help="Persistent Bailian agent session identifier."
    )
    parser.add_argument(
        "--approval-id", help="Approve one previously frozen mutating tool call."
    )
    parser.add_argument(
        "--upload-file", help="CSV path for task=inspect_upload or load_dataset."
    )
    parser.add_argument(
        "--interactive",
        type=parse_bool,
        default=False,
        help="Start an interactive chat loop.",
    )
    parser.add_argument(
        "--list-capabilities",
        action="store_true",
        help="List discoverable Skills and Tools without calling Bailian.",
    )
    parser.add_argument("--action", help="Action for dataset_stats task.")
    parser.add_argument(
        "--column", help="Column argument for dataset_stats / dataset_query tasks."
    )
    parser.add_argument("--query", help="Expression for dataset_query task.")
    parser.add_argument(
        "--rebuild",
        type=parse_bool,
        default=False,
        help="Whether to run the full data workflow.",
    )
    parser.add_argument(
        "--run-tests",
        type=parse_bool,
        default=True,
        help="Whether to run contract tests.",
    )
    parser.add_argument(
        "--data-scope",
        default=",".join(DEFAULT_DATA_SCOPE),
        help="Comma-separated data scope, e.g. sunspot,f107,wso,goes,hale.",
    )
    parser.add_argument(
        "--require-quality-report",
        type=parse_bool,
        default=True,
        help="Whether the quality report is required for a successful handoff.",
    )
    return parser.parse_args()


def request_from_args(args: argparse.Namespace) -> PiAgentRequest:
    if args.request_json:
        payload = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    else:
        payload: dict[str, Any] = {
            "task": args.task,
            "target": args.target,
            "rebuild": args.rebuild,
            "run_tests": args.run_tests,
            "data_scope": [
                item.strip() for item in args.data_scope.split(",") if item.strip()
            ],
            "require_quality_report": args.require_quality_report,
            "question": args.question,
            "upload_path": args.upload_file,
            "action": args.action,
            "column": args.column,
            "query": args.query,
            "session_id": args.session_id,
            "approval_id": args.approval_id,
        }
    return PiAgentRequest.from_dict(payload)


def format_chat_output(result: dict[str, Any]) -> str:
    """Render a structured chat result as readable text."""
    status = result.get("status")
    if status == "failed":
        error = result.get("error", "unknown error")
        if isinstance(error, dict):
            error = f"{error.get('type', 'Error')}: {error.get('message', '')}"
        return f"执行失败: {error}"
    if status == "approval_required":
        pending = result.get("pending_action") or {}
        return (
            "需要批准写入操作:\n"
            f"  工具: {pending.get('tool')}\n"
            f"  参数: {json.dumps(pending.get('arguments', {}), ensure_ascii=False)}\n"
            f"  预计写入: {pending.get('expected_writes', [])}\n"
            f"  approval_id: {pending.get('approval_id')}\n"
            f"  过期时间: {pending.get('expires_utc')}"
        )

    task = result.get("task")
    if task == "load_dataset":
        insp = result.get("inspection", {})
        td = insp.get("time_detection", {})
        lines = [
            f"已加载数据集: {result.get('dataset')}",
            f"  行数: {insp.get('rows_read')}",
            f"  列数: {insp.get('column_count')}",
            f"  主时间列: {td.get('primary_time_column') or td.get('primary_time_columns')}",
        ]
        if insp.get("warnings"):
            lines.append(f"  警告: {'; '.join(insp.get('warnings', []))}")
        if result.get("warning"):
            lines.append(f"  提示: {result.get('warning')}")
        if result.get("requires_split_confirmation"):
            lines.append("")
            lines.append("检测到单列多字段 CSV。")
            proposal = result.get("split_proposal", {})
            lines.append(
                f"  建议分隔符: {proposal.get('delimiter_label') or proposal.get('delimiter')}"
            )
            lines.append(f"  拆分后列数: {proposal.get('field_count')}")
            lines.append(
                f"  列名: {', '.join(str(c) for c in proposal.get('column_names', []))}"
            )
            confidence = proposal.get("confidence_score")
            if confidence is not None:
                lines.append(f"  置信度: {confidence:.2f}")
            if proposal.get("auto_decision"):
                lines.append("  自动决策: 置信度 >= 0.9，将自动执行拆分")
            notes = proposal.get("notes")
            if notes:
                lines.append(f"  备注: {notes}")
            sample = proposal.get("sample_splits") or []
            if sample:
                lines.append("  示例（拆分后）:")
                for row in sample[:3]:
                    lines.append(f"    {row}")
            lines.append("请确认是否按此方案拆分为长表。")
        return "\n".join(lines)

    if task == "apply_multifield_split":
        lines = ["已执行单列多字段拆分"]
        lines.append(f"  原文件: {result.get('original_path')}")
        lines.append(f"  长表: {result.get('long_table_path')}")
        lines.append(f"  行数: {result.get('rows')}")
        lines.append(f"  列名: {', '.join(str(c) for c in result.get('columns', []))}")
        feature_paths = result.get("feature_paths", {})
        if feature_paths:
            lines.append("  标准产物:")
            for key, path in feature_paths.items():
                lines.append(f"    {key}: {path}")
        warnings = (result.get("feature_result") or {}).get("warnings") or []
        if warnings:
            lines.append("  警告:")
            for warning in warnings:
                lines.append(f"    - {warning}")
        return "\n".join(lines)

    if task == "align_uploads":
        lines = [
            f"已对齐 {result.get('source_count')} 个上传数据源",
            f"对齐后行数: {result.get('aligned_rows')}",
            f"对齐表: {result.get('aligned_path')}",
            f"报告: {result.get('report_path')}",
        ]
        warnings = result.get("warnings") or []
        if warnings:
            lines.append(f"警告: {'; '.join(warnings)}")
        return "\n".join(lines)

    if task == "prepare_features_for_upload":
        lines = ["上传数据标准特征产物已生成"]
        paths = result.get("paths", {})
        for key, path in paths.items():
            lines.append(f"  {key}: {path}")
        warnings = result.get("warnings") or []
        if warnings:
            lines.append("警告:")
            for warning in warnings:
                lines.append(f"  - {warning}")
        return "\n".join(lines)

    if task in ("dataset_stats", "dataset_query"):
        # Pretty-print the structured result as JSON for clarity.
        return json.dumps(result, ensure_ascii=False, indent=2)

    if task == "analyze_quality":
        # Summarize the quality report in readable text.
        score = result.get("quality_score", 0)
        issues = result.get("issues", [])
        lines = [f"数据质量评分: {score}/100"]
        if issues:
            lines.append("发现的问题:")
            for issue in issues:
                lines.append(
                    f"  [{issue.get('severity', 'info')}] {issue.get('type')}: {issue.get('message')} "
                    f"(建议: {issue.get('suggested_action', '无')})"
                )
        else:
            lines.append("未发现明显数据质量问题。")
        coverage = result.get("coverage", {})
        lines.append(
            f"覆盖范围: {coverage.get('time_range', {}).get('start')} 至 "
            f"{coverage.get('time_range', {}).get('end')}，"
            f"共 {coverage.get('rows')} 行 x {coverage.get('columns')} 列"
        )
        report_path = result.get("report_path")
        if report_path:
            lines.append(f"完整报告: {report_path}")
        return "\n".join(lines)

    if task in ("propose_cleaning", "apply_cleaning"):
        return _format_cleaning_report(result, task)

    if task == "generate_features":
        return _format_features_report(result)

    if task == "experiment_handoff":
        return _format_handoff_report(result)

    if task == "strategy_recommendation":
        return _format_strategy_report(result)

    if task == "ask_agent":
        return result.get("answer") or "(LLM 未返回回答)"

    if task == "chat":
        return "会话已清除" if result.get("action") == "clear" else ""

    return json.dumps(result, ensure_ascii=False, indent=2)


def _format_cleaning_report(result: dict[str, Any], task: str) -> str:
    """Render the conservative cleaning report in readable text."""
    lines = []
    if task == "apply_cleaning":
        lines.append("已执行安全清洗")
        applied = result.get("applied_actions", [])
        if applied:
            lines.append(f"执行动作: {', '.join(applied)}")
        cleaned_path = result.get("cleaned_file_path")
        if cleaned_path:
            lines.append(f"清洗后文件: {cleaned_path}")
            lines.append("已自动设为当前数据集。")
    else:
        lines.append("保守清洗报告（只标记，不篡改物理值）")

    safe = result.get("safe_actions_available", 0)
    warnings = result.get("domain_warnings", 0)
    lines.append(f"安全动作可执行: {safe} 个，领域警告: {warnings} 个")

    findings = result.get("findings", [])
    if findings:
        lines.append("发现的问题:")
        for finding in findings:
            lines.append(
                f"  [{finding.get('severity', 'info')}] {finding.get('type')}: {finding.get('message')}"
            )
    else:
        lines.append("未发现需要处理的问题。")

    report_path = result.get("quality_report_path")
    if report_path:
        lines.append(f"完整报告: {report_path}")
    return "\n".join(lines)


def print_current_dataset(session: ChatSession) -> None:
    path = session.get_current_dataset_path()
    if path:
        print(f"当前数据集: {path}")
    else:
        print("当前数据集: 无")


def _resolve_clarification(session: ChatSession, user_input: str) -> Any | None:
    """If there's a pending clarification, try to combine it with the new input."""
    pending = session.get_pending_clarification()
    if not pending:
        return None

    # New slash command cancels pending clarification.
    if user_input.startswith("/"):
        session.clear_pending_clarification()
        return None

    inspection = session.get_inspection_summary()
    df_columns = set(inspection.get("columns", [])) if inspection else set()

    # Try to extract additional columns from the user's response.
    from intent_router import QUOTED_COLUMN_PATTERN, IDENTIFIER_PATTERN

    additional_cols = []
    for m in QUOTED_COLUMN_PATTERN.finditer(user_input):
        additional_cols.append(m.group(1))
    for token in IDENTIFIER_PATTERN.findall(user_input):
        if token in df_columns and token not in additional_cols:
            additional_cols.append(token)

    intent = pending["intent"]
    existing_cols = pending.get("columns", [])
    combined_cols = existing_cols + [
        c for c in additional_cols if c not in existing_cols
    ]

    from intent_router import _build_piagent_request

    request = _build_piagent_request(intent, combined_cols)
    session.clear_pending_clarification()
    return request


_HELP_TEXT = """可用命令:
  /load <csv路径>          加载并检查 CSV；若检测到单列多字段，会提示拆分方案，确认后自动拆分为长表并执行质量分析与特征产物生成
  /align                   将已加载的多个 CSV 按月初时间对齐合并
  /prepare_upload_features [csv路径]  对上传数据生成标准特征产物（清洗、周期特征、漂移、质量、注册表）
  /describe                数据集描述
  /head [n]                前 n 行
  /tail [n]                后 n 行
  /stats [col]             列统计 / 全表统计
  /corr <col1> <col2>      两列相关性
  /value_counts <col>      频次统计
  /groupby <col> <agg>     分组统计
  /drift <col1> <col2> [group] 跨组关系漂移
  /query <expr>            预定义函数查询（例如 mean(col)）
  /quality                 数据质量分析
  /clean                   生成保守清洗报告（只标记、不篡改）
  /apply_cleaning          执行安全清洗并生成 cleaned_v1.csv
  /cleaning_report         查看当前清洗报告
  /features                生成太阳物理特征工程表
  /feature_registry        查看特征注册表
  /validate_features       验证特征无泄漏
  /handoff                 生成 experiment_handoff.json
  /recommend               获取实验设计策略推荐
  /domain_rules            查看太阳物理规则常量
  /set column <语义=列名>   覆盖列语义推断
  /set coverage <key=value> 覆盖物理规则常量
  /ask <问题>              强制走 LLM 回答
  /clear                   清空当前数据集和历史
  /exit                    退出
  /help                    显示此帮助
"""


def _format_features_report(result: dict[str, Any]) -> str:
    """Render the feature engineering summary."""
    lines = ["特征工程已生成"]
    lines.append(f"原始列数: {result.get('original_columns')}")
    lines.append(f"工程后列数: {result.get('engineered_columns')}")
    lines.append(f"可用输入特征数: {result.get('input_feature_count')}")
    engineered_path = result.get("engineered_file_path")
    registry_path = result.get("registry_path")
    if engineered_path:
        lines.append(f"特征表: {engineered_path}")
    if registry_path:
        lines.append(f"注册表: {registry_path}")
    issues = result.get("validation_issues", [])
    if issues:
        lines.append("验证警告:")
        for issue in issues:
            lines.append(f"  [{issue.get('severity')}] {issue.get('message')}")
    else:
        lines.append("特征验证通过，无泄漏风险。")
    return "\n".join(lines)


def _format_handoff_report(result: dict[str, Any]) -> str:
    """Render the experiment handoff summary."""
    lines = ["实验交接已生成"]
    handoff_path = result.get("handoff_path")
    if handoff_path:
        lines.append(f"文件: {handoff_path}")
    splits = result.get("handoff_to_experiment_agent", {}).get("recommended_splits", [])
    if splits:
        lines.append(f"推荐切分: {len(splits)} 个")
        for split in splits[:5]:
            lines.append(
                f"  - {split.get('id')}: {split.get('rows')} 行 ({split.get('start')} ~ {split.get('end')})"
            )
    risk_flags = result.get("risk_flags", [])
    if risk_flags:
        lines.append(f"风险标记: {', '.join(risk_flags)}")
    forbidden = result.get("handoff_to_experiment_agent", {}).get(
        "forbidden_inputs", []
    )
    if forbidden:
        lines.append(f"禁止输入: {', '.join(forbidden)}")
    return "\n".join(lines)


def _format_strategy_report(result: dict[str, Any]) -> str:
    """Render the strategy recommendation summary."""
    lines = ["实验设计策略推荐"]
    lines.append(f"LLM 可用: {result.get('llm_available', False)}")
    paths = result.get("paths", {})
    if paths.get("markdown"):
        lines.append(f"完整报告: {paths['markdown']}")
    rb = result.get("rule_based", {})
    top = rb.get("top_features", [])
    if top:
        lines.append(f"推荐特征: {', '.join(top[:5])}")
    models = rb.get("models", [])
    if models:
        lines.append(f"推荐模型: {models[0]}")
    risks = rb.get("risks", [])
    if risks:
        lines.append("主要风险:")
        for risk in risks[:3]:
            lines.append(f"  - {risk}")
    return "\n".join(lines)


def _handle_chat_action(session: ChatSession, action: dict[str, Any]) -> str:
    """Handle session-state-only commands such as /set, /domain_rules, /cleaning_report."""
    import data_cleaning_engine

    name = action.get("action")
    if name == "domain_rules":
        rules = data_cleaning_engine.get_coverage_rules(
            session.get_cleaning_coverage_overrides()
        )
        lines = ["当前太阳物理覆盖规则常量:"]
        for key, values in rules.items():
            lines.append(f"  {key}: {values}")
        return "\n".join(lines)

    if name == "feature_registry":
        upload_dir = session.get_upload_registry_path()
        if upload_dir:
            registry_path = upload_dir.parent / "feature_registry.json"
            if registry_path.exists():
                try:
                    registry = json.loads(registry_path.read_text(encoding="utf-8"))
                    fields = registry.get("fields", [])
                    input_features = [
                        f["field"]
                        for f in fields
                        if f.get("role") == "input_feature"
                        and f.get("allowed_as_model_input")
                    ]
                    identifiers = [
                        f["field"] for f in fields if f.get("role") == "identifier"
                    ]
                    labels = [f["field"] for f in fields if f.get("role") == "label"]
                    lines = [
                        f"特征注册表: {registry_path}",
                        f"  input_features: {len(input_features)}",
                        f"  identifiers: {len(identifiers)}",
                        f"  labels: {len(labels)}",
                    ]
                    if input_features:
                        lines.append(
                            f"  输入特征示例: {', '.join(input_features[:10])}"
                        )
                    return "\n".join(lines)
                except (json.JSONDecodeError, OSError):
                    pass
        return "暂无特征注册表。请先运行 /features。"

    if name == "validate_features":
        upload_dir = session.get_upload_registry_path()
        if upload_dir:
            registry_path = upload_dir.parent / "feature_registry.json"
            if registry_path.exists():
                try:
                    registry = json.loads(registry_path.read_text(encoding="utf-8"))
                    issues = registry.get("validation_issues", [])
                    if issues:
                        lines = ["特征验证发现问题:"]
                        for issue in issues:
                            lines.append(
                                f"  [{issue.get('severity')}] {issue.get('message')}"
                            )
                        return "\n".join(lines)
                    return "特征验证通过，无泄漏风险。"
                except (json.JSONDecodeError, OSError) as exc:
                    return f"特征验证失败: {type(exc).__name__}: {exc}"
        return "暂无特征注册表。请先运行 /features。"

    if name == "cleaning_report":
        upload_dir = session.get_upload_registry_path()
        if upload_dir:
            report_path = upload_dir.parent / "quality_report.json"
            if report_path.exists():
                try:
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    cleaning = report.get("cleaning")
                    if cleaning:
                        return _format_cleaning_report(cleaning, "propose_cleaning")
                except (json.JSONDecodeError, OSError):
                    pass
        return "暂无清洗报告。请先运行 /clean 或 /quality。"

    if name == "set_column":
        mapping = action.get("mapping", {})
        for semantic, column in mapping.items():
            session.set_cleaning_column_override(semantic, column)
        return f"已设置列语义覆盖: {mapping}"

    if name == "set_coverage":
        mapping = action.get("mapping", {})
        # Parse flattened keys like "f107_start" into nested dicts.
        coverage_rules: dict[str, dict[str, str]] = {}
        for key, value in mapping.items():
            parts = key.rsplit("_", 1)
            if len(parts) == 2:
                category, attr = parts
                coverage_rules.setdefault(category, {})[attr] = value
            else:
                coverage_rules.setdefault("custom", {})[key] = value
        for category, values in coverage_rules.items():
            session.set_cleaning_coverage_override(category, values)
        return f"已设置物理规则覆盖: {coverage_rules}"

    return f"未知 chat action: {name}"


def chat_mode(args: argparse.Namespace) -> None:
    session = ChatSession()
    agent = BailianDataFeatureAgent(session=session)
    print("Solar-Cycle Data Feature Agent Chat")
    print("输入 /exit 退出，/load <csv> 加载数据集，/help 查看命令")
    print_current_dataset(session)

    while True:
        try:
            user_input = input("\nchat> ").strip()
        except EOFError:
            break
        if not user_input:
            continue
        if user_input.lower() in {"/exit", "/quit", "exit", "quit"}:
            break
        if user_input.lower() == "/help":
            print(_HELP_TEXT)
            continue

        if not user_input.startswith("/"):
            response = agent.run(user_input, session_id=session.session_id)
            while response.status == "approval_required" and response.pending_action:
                print(format_chat_output({"task": "ask_agent", **response.to_dict()}))
                decision = input("批准此操作？[y/N] ").strip().lower()
                approval_id = response.pending_action["approval_id"]
                if decision in {"y", "yes", "是"}:
                    response = agent.run(
                        "", session_id=session.session_id, approval_id=approval_id
                    )
                else:
                    response = agent.reject(session.session_id, approval_id)
            output = format_chat_output({"task": "ask_agent", **response.to_dict()})
            if output:
                print(output)
            session.append_history("user", user_input)
            session.append_history("assistant", output or "")
            continue

        session.append_history("user", user_input)

        # Try to resolve any pending clarification first.
        resolved_request = _resolve_clarification(session, user_input)
        if resolved_request is not None:
            request = resolved_request
        else:
            try:
                intent = route_intent(user_input, session)
            except ValueError as exc:
                output = f"命令解析错误: {exc}"
                print(output)
                session.append_history("assistant", output)
                continue

            if intent.clarification_question:
                print(intent.clarification_question)
                session.append_history("assistant", intent.clarification_question)
                session.set_pending_clarification(intent.to_dict())
                continue

            # Handle chat actions that modify session state directly.
            if intent.chat_action:
                output = _handle_chat_action(session, intent.chat_action)
                print(output)
                session.append_history("assistant", output)
                continue

            request = intent.piagent_request
            if request is None:
                output = "无法识别你的意图，请重试或输入 /help 查看命令。"
                print(output)
                session.append_history("assistant", output)
                continue

        try:
            if request.task == "ask_agent":
                response = agent.run(
                    request.question or "", session_id=session.session_id
                )
                while (
                    response.status == "approval_required" and response.pending_action
                ):
                    print(
                        format_chat_output({"task": "ask_agent", **response.to_dict()})
                    )
                    decision = input("批准此操作？[y/N] ").strip().lower()
                    approval_id = response.pending_action["approval_id"]
                    if decision in {"y", "yes", "是"}:
                        response = agent.run(
                            "", session_id=session.session_id, approval_id=approval_id
                        )
                    else:
                        response = agent.reject(session.session_id, approval_id)
                result = {"task": "ask_agent", **response.to_dict()}
            else:
                result = run_chat_request(request, session)
        except Exception as exc:
            result = {
                "agent": "data_feature_agent",
                "status": "failed",
                "task": request.task if request else None,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

        output = format_chat_output(result)
        if output:
            print(output)
        session.append_history("assistant", output or "")
        session.append_tool_trace(
            {
                "task": request.task,
                "status": result.get("status"),
                "error_type": result.get("error_type"),
                "error": result.get("error"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        # Interactive confirmation for single-column multi-field CSVs.
        if request.task == "load_dataset" and result.get("requires_split_confirmation"):
            proposal = result.get("split_proposal", {})
            confidence = proposal.get("confidence_score", 0.0)
            auto_decision = proposal.get("auto_decision", False)
            notes = proposal.get("notes")

            if auto_decision:
                print(f"\n置信度 {confidence:.2f} >= 0.9，将自动执行拆分。")
                if notes:
                    print(f"  备注: {notes}")
                decision = "y"
            else:
                print("\n检测到单列多字段 CSV。")
                print(
                    f"  分隔符: {proposal.get('delimiter_label') or proposal.get('delimiter')}"
                )
                print(f"  拆分后列数: {proposal.get('field_count')}")
                print(
                    f"  列名: {', '.join(str(c) for c in proposal.get('column_names', []))}"
                )
                print(f"  置信度: {confidence:.2f}")
                if notes:
                    print(f"  备注: {notes}")
                decision = input("确认按此方案拆分为长表？[y/N] ").strip().lower()

            if decision in {"y", "yes", "是"}:
                session.set_agent_state("pending_split_proposal", proposal)
                try:
                    split_result = upload_column_splitter.apply_split(
                        session,
                        proposal,
                        run_quality=False,
                        run_features=True,
                    )
                    split_output = format_chat_output(split_result)
                    if split_output:
                        print("\n" + split_output)
                    session.append_history("assistant", split_output or "")
                    session.append_tool_trace(
                        {
                            "task": "apply_multifield_split",
                            "status": split_result.get("status"),
                            "error_type": None,
                            "error": None,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                except Exception as exc:
                    split_error = f"拆分失败: {type(exc).__name__}: {exc}"
                    print(split_error)
                    session.append_history("assistant", split_error)
                    session.append_tool_trace(
                        {
                            "task": "apply_multifield_split",
                            "status": "failed",
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                    )
            else:
                skip_msg = "已取消拆分。当前数据集仍保持原始单列表。"
                print(skip_msg)
                session.append_history("assistant", skip_msg)
            continue

        # Auto-run the standard feature pipeline after a successful dataset load.
        if request.task == "load_dataset" and result.get("status") == "ok":
            try:
                feature_result = run_chat_request(
                    PiAgentRequest(task="prepare_features_for_upload"), session
                )
                feature_output = format_chat_output(feature_result)
                if feature_output:
                    print("\n" + feature_output)
                session.append_history("assistant", feature_output or "")
                session.append_tool_trace(
                    {
                        "task": "prepare_features_for_upload",
                        "status": feature_result.get("status"),
                        "error_type": None,
                        "error": None,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )
            except Exception as exc:
                feature_error = f"自动特征管线失败: {type(exc).__name__}: {exc}"
                print(feature_error)
                session.append_history("assistant", feature_error)
                session.append_tool_trace(
                    {
                        "task": "prepare_features_for_upload",
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

        if request.task == "chat" and getattr(request, "action", None) == "clear":
            print_current_dataset(session)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.list_capabilities:
        print(
            json.dumps(
                BailianDataFeatureAgent().capabilities(), ensure_ascii=False, indent=2
            )
        )
        return
    if args.interactive:
        chat_mode(args)
        return

    try:
        request = request_from_args(args)
        if request.task == "ask_agent":
            response = BailianDataFeatureAgent().run(
                request.question or "",
                session_id=request.session_id,
                approval_id=request.approval_id,
            )
            result = {
                "agent": "data_feature_agent",
                "task": "ask_agent",
                **response.to_dict(),
            }
        elif request.task in {
            "chat",
            "load_dataset",
            "align_uploads",
            "prepare_features_for_upload",
            "dataset_stats",
            "dataset_query",
            "analyze_quality",
            "propose_cleaning",
            "apply_cleaning",
            "generate_features",
            "experiment_handoff",
            "strategy_recommendation",
        }:
            session = ChatSession()
            result = run_chat_request(request, session)
        else:
            result = run_piagent_request(request)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        error = {
            "agent": "data_feature_agent",
            "platform_target": "bailian_function_calling",
            "status": "failed",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        print(json.dumps(error, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
