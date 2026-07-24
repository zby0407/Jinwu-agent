from __future__ import annotations

from typing import Any


def _fmt(value: Any) -> str:
    """Format a value for a Chinese natural-language report."""
    if value is None:
        return "无"
    if isinstance(value, float):
        if value != value:  # NaN
            return "无"
        return f"{value:.4f}"
    return str(value)


def _indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    lines = []
    for line in text.splitlines():
        if line.strip():
            lines.append(prefix + line)
        else:
            lines.append(line)
    return "\n".join(lines)


def _render_issue(issue: dict[str, Any]) -> str:
    lines = []
    severity = issue.get("severity", "info")
    severity_cn = {"critical": "严重", "warning": "警告", "info": "提示"}.get(
        severity, severity
    )
    lines.append(f"[{severity_cn}] {issue.get('type', 'unknown')}")
    if issue.get("message"):
        lines.append(f"  描述：{issue['message']}")
    if issue.get("count") is not None:
        lines.append(f"  数量：{issue['count']}")
    if issue.get("columns"):
        lines.append(f"  涉及列：{', '.join(str(c) for c in issue['columns'])}")
    if issue.get("suggested_action"):
        lines.append(f"  建议操作：{issue['suggested_action']}")
    if issue.get("sample") is not None:
        lines.append(f"  示例：{_fmt(issue['sample'])}")
    return "\n".join(lines)


def _render_coverage(coverage: dict[str, Any]) -> str:
    lines = []
    lines.append(f"行数：{coverage.get('rows', '无')}")
    lines.append(f"列数：{coverage.get('columns', '无')}")
    if "column_names" in coverage:
        lines.append(f"列名：{', '.join(str(c) for c in coverage['column_names'])}")
    time_range = coverage.get("time_range")
    if isinstance(time_range, dict):
        lines.append(
            f"时间范围：{time_range.get('start', '无')} 至 {time_range.get('end', '无')}"
        )
    if coverage.get("time_column"):
        lines.append(f"时间列：{coverage['time_column']}")
    if coverage.get("time_granularity"):
        lines.append(f"时间粒度：{coverage['time_granularity']}")
    if coverage.get("memory_mb") is not None:
        lines.append(f"内存估算：{coverage['memory_mb']} MB")
    return "\n".join(lines)


def _render_missing_per_column(missing: dict[str, Any]) -> str:
    if not missing:
        return "未发现缺失值。"
    lines = []
    for col, stats in missing.items():
        null_count = stats.get("null_count", 0)
        null_ratio = stats.get("null_ratio", 0.0)
        line = f"{col}: 缺失 {null_count} 个 ({null_ratio:.2%})"
        if "max_consecutive_missing" in stats:
            line += f"，最大连续缺失 {stats['max_consecutive_missing']} 个"
        lines.append(line)
    return "\n".join(lines)


def _render_cleaning(cleaning: dict[str, Any]) -> str:
    if not cleaning:
        return "无清洗报告。"

    do_not_alter_reason_cn: dict[str, str] = {
        "missing_proxy_values": "缺失值通常代表仪器前时代或覆盖范围缺口；插值会构造虚假观测。",
        "statistical_outliers": "太阳极端值往往是真实物理事件；应目视检查而非自动截断。",
        "coverage_outliers": "仪器覆盖范围外的数据应标记而非删除，因为缺口本身具有物理意义。",
    }
    safe_action_desc_cn: dict[str, str] = {
        "remove_exact_duplicates": "删除完全重复的行",
        "replace_inf_with_nan": "将无穷值替换为 NaN",
        "review_constant_columns": "检查常量列是否可作为元数据使用",
    }

    lines = []
    if cleaning.get("generated_at"):
        lines.append(f"生成时间（UTC）：{cleaning['generated_at']}")
    safe = cleaning.get("safe_actions_available", 0)
    warnings = cleaning.get("domain_warnings", 0)
    lines.append(f"安全可执行动作：{safe} 个")
    lines.append(f"领域警告：{warnings} 个")

    column_semantics = cleaning.get("column_semantics", {})
    if column_semantics:
        lines.append("字段物理语义识别：")
        for semantic, cols in column_semantics.items():
            lines.append(f"  {semantic}：{', '.join(str(c) for c in cols)}")

    domain_constants = cleaning.get("domain_constants", {})
    if domain_constants:
        lines.append("物理覆盖规则常量：")
        for signal, rules in domain_constants.items():
            if isinstance(rules, dict):
                parts = [f"{k}={v}" for k, v in rules.items()]
                lines.append(f"  {signal}：{', '.join(parts)}")
            else:
                lines.append(f"  {signal}：{rules}")

    do_not_alter = cleaning.get("do_not_alter", [])
    if do_not_alter:
        lines.append("禁止自动篡改的物理原则：")
        for rule in do_not_alter:
            if isinstance(rule, dict):
                rule_type = rule.get("type", "")
                reason = rule.get("reason", "")
                reason_cn = do_not_alter_reason_cn.get(rule_type, reason)
                lines.append(f"  - {rule_type}：{reason_cn}")
            else:
                lines.append(f"  - {rule}")

    findings = cleaning.get("findings", [])
    if findings:
        lines.append("发现的问题：")
        for finding in findings:
            lines.append(_indent(_render_issue(finding), 2))
    else:
        lines.append("未发现需要处理的问题。")

    safe_actions = cleaning.get("safe_actions", [])
    if safe_actions:
        lines.append("建议的安全动作：")
        for action in safe_actions:
            if isinstance(action, dict):
                action_name = action.get("action", "")
                applies = action.get("applies", False)
                desc = action.get("description", "")
                desc_cn = safe_action_desc_cn.get(action_name, desc)
                lines.append(
                    f"  - {action_name}（{'可执行' if applies else '暂不可执行'}）：{desc_cn}"
                )
            else:
                lines.append(f"  - {action}")

    return "\n".join(lines)


def _render_cleaned_actions(report: dict[str, Any]) -> str:
    lines = []
    if report.get("cleaned_file_path"):
        lines.append(f"清洗后文件：{report['cleaned_file_path']}")
    actions = report.get("applied_cleaning_actions", [])
    if actions:
        lines.append("已执行动作：")
        for action in actions:
            lines.append(f"  - {action}")
    else:
        lines.append("无需要执行的安全清洗动作。")
    return "\n".join(lines)


def _render_cycle_context_summary(summary: dict[str, Any]) -> str:
    lines = []
    lines.append("【周期关联物理特征摘要】")

    time_range = summary.get("upload_time_range")
    if time_range:
        lines.append(
            f"上传数据时间范围：{time_range.get('start', '无')} 至 {time_range.get('end', '无')}"
        )

    signals = summary.get("present_signals", [])
    if signals:
        lines.append(f"识别到的物理信号：{', '.join(signals)}")

    meanings = summary.get("upload_column_meanings", {})
    if meanings:
        lines.append("上传列物理含义：")
        for col, meaning in meanings.items():
            lines.append(f"  {col}：{meaning.get('physical_meaning', '无')}")
        lines.append("")

    cycles = summary.get("overlapping_cycles", [])
    if cycles:
        lines.append("重叠的太阳活动周期：")
        for c in cycles:
            lines.append(
                f"  第 {c['cycle_no']} 周：{c['start_date']} ~ {c['end_date']}，"
                f"极大 {c['peak_date']}，完整：{'是' if c['is_complete'] else '否'}"
            )
            upload_features = c.get("upload_features", {})
            if upload_features:
                lines.append("    由上传数据计算的周期特征：")
                for k, v in upload_features.items():
                    lines.append(f"      {k}：{v}")
            global_features = c.get("global_features", {})
            if global_features:
                lines.append("    全局标准周期特征（对照，节选）：")
                for k, v in global_features.items():
                    lines.append(f"      {k}：{v}")
                if c.get("global_feature_count", 0) > len(global_features):
                    lines.append(f"      ... 共 {c['global_feature_count']} 个全局字段")
    else:
        lines.append("未找到重叠的太阳活动周期。")

    ml_features = summary.get("ml_ready_features", [])
    if ml_features:
        lines.append("ML 可用输入特征（与上传信号相关）：")
        for f in ml_features:
            lines.append(f"  - {f['field']}（来源：{f.get('source_table', '无')}）")
            lines.append(f"      物理含义：{f.get('physical_meaning') or '无'}")
            lines.append(f"      证据等级：{f.get('evidence_tier') or '无'}")
            mechanism = f.get("mechanism_link") or []
            if mechanism:
                lines.append(f"      机制关联：{', '.join(str(m) for m in mechanism)}")
    else:
        lines.append("未找到与上传信号相关的 ML 可用输入特征。")

    labels = summary.get("label_fields", [])
    if labels:
        lines.append("标签字段（只能作为预测目标）：")
        for f in labels:
            lines.append(f"  - {f['field']}：{f.get('physical_meaning') or '无'}")
            lines.append(f"      {f.get('note', '')}")

    return "\n".join(lines)


def _render_key_value_pairs(obj: Any, indent: int = 2) -> str:
    """Generic recursive renderer for nested dicts and lists."""
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{' ' * indent}{k}：")
                lines.append(_render_key_value_pairs(v, indent + 2))
            else:
                lines.append(f"{' ' * indent}{k}：{_fmt(v)}")
        return "\n".join(lines)
    if isinstance(obj, list):
        if not obj:
            return ""
        lines = []
        for item in obj:
            if isinstance(item, (dict, list)):
                lines.append(_render_key_value_pairs(item, indent))
            else:
                lines.append(f"{' ' * indent}- {_fmt(item)}")
        return "\n".join(lines)
    return f"{' ' * indent}{_fmt(obj)}"


def _render_upload_evidence_tiers(evidence_tiers: dict[str, Any]) -> str:
    if not evidence_tiers:
        return "未提供证据层级。"
    groups: dict[str, list[str]] = {}
    for col, info in evidence_tiers.items():
        if isinstance(info, dict):
            tier = info.get("tier", "unverified")
            note = info.get("note", "")
            groups.setdefault(tier, []).append(f"{col}（{note}）" if note else col)
        else:
            groups.setdefault(str(info), []).append(col)
    lines = []
    for tier, cols in groups.items():
        lines.append(f"{tier}：{', '.join(cols)}")
    return "\n".join(lines)


def _render_recommendations(items: list[Any]) -> str:
    if not items:
        return "无。"
    lines = []
    for item in items:
        if isinstance(item, dict):
            parts = [f"{k}={_fmt(v)}" for k, v in item.items()]
            lines.append(f"- {'; '.join(parts)}")
        else:
            lines.append(f"- {_fmt(item)}")
    return "\n".join(lines)


def _render_upload_report(report: dict[str, Any]) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("数据质量分析报告（上传数据集）")
    lines.append("=" * 60)
    lines.append("")

    if report.get("generated_utc"):
        lines.append(f"生成时间（UTC）：{report['generated_utc']}")
    if report.get("generated_at"):
        lines.append(f"生成时间（UTC）：{report['generated_at']}")
    if report.get("status"):
        lines.append(f"报告状态：{report['status']}")
    if report.get("llm_status"):
        lines.append(f"LLM 状态：{report['llm_status']}")
    if report.get("path"):
        lines.append(f"JSON 报告：{report['path']}")
    if report.get("text_path"):
        lines.append(f"文本报告：{report['text_path']}")
    if report.get("split_provenance"):
        prov = report["split_provenance"]
        lines.append("拆分来源：")
        lines.append(f"  原始文件：{prov.get('original_path', '无')}")
        lines.append(
            f"  拆分分隔符：{prov.get('delimiter_label') or prov.get('delimiter', '无')}"
        )
        lines.append(
            f"  首行是否为表头：{'是' if prov.get('first_row_is_header') else '否'}"
        )
        lines.append(
            f"  拆分前列名：{', '.join(str(c) for c in prov.get('column_names_before_split', []))}"
        )
        lines.append(
            f"  拆分后列名：{', '.join(str(c) for c in prov.get('column_names_after_split', []))}"
        )
    lines.append("")

    # Dataset overview
    lines.append("【数据集概览】")
    coverage = report.get("coverage", {})
    rows = report.get("rows") or coverage.get("rows")
    lines.append(f"行数：{_fmt(rows)}")
    columns = report.get("columns") or coverage.get("column_names")
    if columns is not None:
        lines.append(f"列数：{len(columns)}")
        lines.append(f"列名：{', '.join(str(c) for c in columns)}")
    date_range = report.get("date_range") or coverage.get("time_range")
    if isinstance(date_range, dict):
        lines.append(
            f"时间范围：{date_range.get('start', '无')} 至 {date_range.get('end', '无')}"
        )
    lines.append("")

    # Quality score
    lines.append("【质量评分】")
    lines.append(f"综合评分：{report.get('quality_score', '无')}/100")
    severity_counts = report.get("severity_counts", {})
    lines.append(f"严重问题：{severity_counts.get('critical', 0)} 个")
    lines.append(f"警告问题：{severity_counts.get('warning', 0)} 个")
    lines.append(f"提示信息：{severity_counts.get('info', 0)} 个")
    lines.append("")

    # Issues
    lines.append("【质量问题详情】")
    issues = report.get("quality_issues") or report.get("issues") or []
    if issues:
        for issue in issues:
            lines.append(_render_issue(issue))
    else:
        lines.append("未发现明显数据质量问题。")
    lines.append("")

    # Coverage
    coverage = report.get("coverage", {})
    if coverage:
        lines.append("【覆盖范围】")
        lines.append(_render_coverage(coverage))
        lines.append("")

    # Missing values
    missing = report.get("missing_per_column", {})
    if missing:
        lines.append("【缺失值情况】")
        lines.append(_render_missing_per_column(missing))
        lines.append("")

    # Cleaning
    cleaning = report.get("cleaning_report") or report.get("cleaning")
    if cleaning:
        lines.append("【保守清洗建议】")
        lines.append(_render_cleaning(cleaning))
        lines.append("")

    # Applied auto-cleaning
    if report.get("cleaned_file_path"):
        lines.append("【已执行清洗】")
        lines.append(_render_cleaned_actions(report))
        lines.append("")

    # Semantic mapping
    semantic_map = report.get("semantic_mapping", {})
    if semantic_map:
        lines.append("【语义映射】")
        for col, semantic in semantic_map.items():
            lines.append(f"  {col}：{semantic}")
        lines.append("")

    # Evidence tiers
    evidence_tiers = report.get("evidence_tiers", {})
    if evidence_tiers:
        lines.append("【证据层级】")
        lines.append(_render_upload_evidence_tiers(evidence_tiers))
        lines.append("")

    # Feature recommendations
    feature_recs = report.get("feature_recommendations", [])
    if feature_recs:
        lines.append("【特征工程建议】")
        lines.append(_render_recommendations(feature_recs))
        lines.append("")

    # Proxy suggestions
    proxy_suggestions = report.get("missing_data_proxy_suggestions", [])
    if proxy_suggestions:
        lines.append("【缺失数据代理建议】")
        lines.append(_render_recommendations(proxy_suggestions))
        lines.append("")

    # Physical plausibility
    plausibility = report.get("physical_plausibility", {})
    if plausibility:
        lines.append("【物理合理性检查】")
        consistent = plausibility.get("consistent")
        if consistent is not None:
            lines.append(f"整体一致：{'是' if consistent else '否'}")
        notes = plausibility.get("notes") or []
        if notes:
            lines.append("检查笔记：")
            for note in notes:
                lines.append(f"  - {note}")
        lines.append("")

    # Physical meaning verification
    verification = report.get("physical_meaning_verification", {})
    if verification:
        lines.append("【物理含义验证】")
        all_consistent = verification.get("all_consistent")
        if all_consistent is not None:
            lines.append(f"全部一致：{'是' if all_consistent else '否'}")
        verified = verification.get("verified", [])
        if verified:
            lines.append("字段验证：")
            for item in verified:
                field = item.get("field", "unknown")
                consistent = item.get("consistent")
                flag = "一致" if consistent else "不一致"
                lines.append(f"  - {field}: {flag}")
                item_issues = item.get("issues", [])
                if item_issues:
                    for issue in item_issues:
                        lines.append(f"      {issue}")
        lines.append("")

    # Wording risk
    wording = report.get("wording_risk_check") or report.get("wording_risk")
    if wording:
        lines.append("【措辞风险检查】")
        has_risk = wording.get("has_risk")
        lines.append(f"是否存在风险：{'是' if has_risk else '否'}")
        risks = wording.get("risks", [])
        if risks:
            lines.append("风险点：")
            for risk in risks:
                if isinstance(risk, dict):
                    parts = [f"{k}={_fmt(v)}" for k, v in risk.items()]
                    lines.append(f"  - {'; '.join(parts)}")
                else:
                    lines.append(f"  - {risk}")
        if wording.get("safer_text"):
            lines.append("建议表述：")
            lines.append(_indent(wording["safer_text"], 2))
        if wording.get("note"):
            lines.append(f"备注：{wording['note']}")
        lines.append("")

    # Narrative
    narrative = report.get("narrative")
    if narrative:
        lines.append("【自动摘要】")
        lines.append(str(narrative))
        lines.append("")

    # Cycle context physical feature summary
    if report.get("cycle_context_summary"):
        lines.append(_render_cycle_context_summary(report["cycle_context_summary"]))
        lines.append("")

    lines.append("=" * 60)
    lines.append("报告结束。")
    lines.append("=" * 60)
    return "\n".join(lines)


def _render_processed_report(report: dict[str, Any]) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("数据质量分析报告（项目处理数据）")
    lines.append("=" * 60)
    lines.append("")

    metadata = report.get("report_metadata", {})
    if metadata.get("generated_on"):
        lines.append(f"生成日期：{metadata['generated_on']}")
    if metadata.get("report_path"):
        lines.append(f"JSON 报告：{metadata['report_path']}")
    if metadata.get("purpose"):
        lines.append(f"报告目的：{metadata['purpose']}")
    lines.append("")

    # Source profiles
    source_profiles = report.get("source_profiles", {})
    if source_profiles:
        lines.append("【数据源概览】")
        for name, profile in source_profiles.items():
            lines.append(f"{name}：")
            lines.append(_render_key_value_pairs(profile, 2))
        lines.append("")

    # Master table quality
    master_quality = report.get("master_table_quality", {})
    if master_quality:
        lines.append("【主表质量】")
        lines.append(f"日期标准：{master_quality.get('date_month_standard', '无')}")
        lines.append(
            f"所有日期均为月初：{'是' if master_quality.get('all_dates_are_month_start') else '否'}"
        )
        flag_counts = master_quality.get("data_coverage_flag_counts", {})
        if flag_counts:
            lines.append("数据覆盖标志计数：")
            for flag, count in flag_counts.items():
                lines.append(f"  {flag}：{count}")
        overlap = master_quality.get("all_sources_overlap", {})
        if overlap:
            lines.append(
                f"全源重叠区间：{overlap.get('start', '无')} 至 {overlap.get('end', '无')}，"
                f"共 {overlap.get('months', '无')} 个月"
            )
        validity = master_quality.get("validity_by_signal", {})
        if validity:
            lines.append("各信号有效月份：")
            for signal, summary in validity.items():
                lines.append(f"  {signal}：")
                lines.append(_render_key_value_pairs(summary, 4))
        lines.append("")

    # Cycle table quality
    cycle_quality = report.get("cycle_table_quality", {})
    if cycle_quality:
        lines.append("【周期表质量】")
        lines.append(f"总周期数：{cycle_quality.get('total_cycles', '无')}")
        lines.append(f"完整周期数：{cycle_quality.get('complete_cycle_count', '无')}")
        incomplete = cycle_quality.get("incomplete_cycles", [])
        lines.append(
            f"不完整周期：{', '.join(str(c) for c in incomplete) if incomplete else '无'}"
        )
        feature_avail = cycle_quality.get("feature_availability_by_cycle_signal", {})
        if feature_avail:
            lines.append("特征可用性：")
            for signal, summary in feature_avail.items():
                lines.append(f"  {signal}：")
                lines.append(_render_key_value_pairs(summary, 4))
        target_notes = cycle_quality.get("target_field_notes", {})
        if target_notes:
            lines.append("目标字段说明：")
            for field, note in target_notes.items():
                lines.append(f"  {field}：{note}")
        lines.append("")

    # Evidence tiers
    evidence = report.get("evidence_tiers", {})
    if evidence:
        lines.append("【证据层级】")
        primary = evidence.get("primary_evidence", [])
        if primary:
            lines.append("主要证据：")
            for item in primary:
                if isinstance(item, dict):
                    signal = item.get("signal", "unknown")
                    fields = item.get("fields", [])
                    lines.append(
                        f"  - {signal}（字段：{', '.join(str(f) for f in fields)}）"
                    )
                    usable = item.get("usable_for", [])
                    if usable:
                        lines.append("    可用于：")
                        for u in usable:
                            lines.append(f"      · {u}")
                    limitations = item.get("limitations", [])
                    if limitations:
                        lines.append("    局限性：")
                        for lim in limitations:
                            lines.append(f"      · {lim}")
                else:
                    lines.append(f"  - {_fmt(item)}")
        auxiliary = evidence.get("mechanism_or_auxiliary_evidence", [])
        if auxiliary:
            lines.append("辅助/机制证据：")
            for item in auxiliary:
                if isinstance(item, dict):
                    signal = item.get("signal", "unknown")
                    fields = item.get("fields", [])
                    lines.append(
                        f"  - {signal}（字段：{', '.join(str(f) for f in fields)}）"
                    )
                    usable = item.get("usable_for", [])
                    if usable:
                        lines.append("    可用于：")
                        for u in usable:
                            lines.append(f"      · {u}")
                    limitations = item.get("limitations", [])
                    if limitations:
                        lines.append("    局限性：")
                        for lim in limitations:
                            lines.append(f"      · {lim}")
                else:
                    lines.append(f"  - {_fmt(item)}")
        lines.append("")

    # Missingness and proxy warnings
    missingness = report.get("missingness_and_proxy_warnings", {})
    if missingness:
        known = missingness.get("known_missing_or_limited_areas", [])
        if known:
            lines.append("【已知缺失或受限区域】")
            for item in known:
                area = item.get("area", "unknown")
                lines.append(f"{area}：")
                lines.append(f"  影响：{item.get('impact', '无')}")
                lines.append(f"  要求行为：{item.get('required_agent_behavior', '无')}")
            lines.append("")
        proxies = missingness.get("proxy_markers", {})
        if proxies:
            lines.append("【代理标记】")
            for k, v in proxies.items():
                lines.append(f"{k}：{v}")
            lines.append("")

    # Claims policy
    claims = report.get("claims_policy_for_downstream_agents", {})
    if claims:
        lines.append("【下游声明策略】")
        strong = claims.get("allowed_strong_claims", [])
        if strong:
            lines.append("允许强声明：")
            for item in strong:
                lines.append(f"  - {item}")
        moderate = claims.get("allowed_moderate_claims", [])
        if moderate:
            lines.append("允许中等声明：")
            for item in moderate:
                lines.append(f"  - {item}")
        disallowed = claims.get("disallowed_or_caution_claims", [])
        if disallowed:
            lines.append("禁止或需谨慎声明：")
            for item in disallowed:
                lines.append(f"  - {item}")
        wording = claims.get("required_wording_style", [])
        if wording:
            lines.append("推荐措辞风格：")
            for item in wording:
                lines.append(f"  - {item}")
        lines.append("")

    # Recommended usage
    usage = report.get("recommended_agent_usage", {})
    if usage:
        lines.append("【推荐用法】")
        for agent, recs in usage.items():
            lines.append(f"{agent}：")
            for rec in recs:
                lines.append(f"  - {rec}")
        lines.append("")

    # Thresholds
    thresholds = report.get("machine_readable_thresholds", {})
    if thresholds:
        lines.append("【机器可读阈值】")
        lines.append(_render_key_value_pairs(thresholds, 2))
        lines.append("")

    lines.append("=" * 60)
    lines.append("报告结束。")
    lines.append("=" * 60)
    return "\n".join(lines)


def render_data_quality_report_text(report: dict[str, Any]) -> str:
    """Convert a data quality report JSON dict to a Chinese natural-language text report.

    Automatically detects whether the report is the processed/canonical report
    (contains ``report_metadata``) or an upload-style report.
    """
    if not report:
        return "报告为空。"
    if "report_metadata" in report:
        return _render_processed_report(report)
    return _render_upload_report(report)
