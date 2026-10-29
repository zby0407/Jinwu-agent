"""The human report: every claim in it is generated from a number in the result.

The report is written in the same language as the agent contract (Chinese) but
keeps identifiers, metric names and file names in English so that a reader can
grep the artefacts and find the same string in the CSV.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["build_report", "format_interval"]

SECTION_TITLES = (
    "1. 运行概要",
    "2. 数据来源与可得性",
    "3. 预测结果（区间，而不是一个分数）",
    "4. 解释：因素、对照与敏感性",
    "5. 偏差审计",
    "6. 可复现性",
    "7. 局限与无法评估的内容",
    "8. 复现命令",
)


def format_interval(low: float, median: float, high: float) -> str:
    """A citation interval with its width, because the width is the message."""
    if any(
        value is None or (isinstance(value, float) and math.isnan(value))
        for value in (low, median, high)
    ):
        return "不可用（模型未训练）"
    return f"{median:.0f}（80% 区间 {low:.0f}-{high:.0f}，宽度 {high - low:.0f}）"


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = [str(cell).replace("|", "/").replace("\n", " ") for cell in row]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def build_report(
    *,
    run_id: str,
    reference_year: int,
    horizon: int,
    cohort_summary: Mapping[str, Any],
    metrics: Mapping[str, Any],
    model_card: Mapping[str, Any] | None,
    importances: Sequence[Any],
    explanations: Sequence[Any],
    bias_report: Any,
    provenance_summary: Mapping[str, Any],
    notes: Sequence[str] = (),
    files: Mapping[str, str] | None = None,
    started_at: str = "",
    finished_at: str = "",
    wall_seconds: float | None = None,
    fingerprint: str = "",
) -> str:
    """Render the whole report from already-computed pieces."""
    lines: list[str] = ["# 科研影响力分析与偏差解释报告", ""]
    lines.append(f"- run_id: `{run_id}`")
    lines.append("- schema: `jw.research-influence/v1`")
    lines.append(f"- 参考年: {reference_year}；观测窗口: 发表后 {horizon} 年")
    if fingerprint:
        lines.append(f"- 运行指纹: `{fingerprint}`")
    if started_at:
        lines.append(f"- 开始: {started_at}；完成: {finished_at}")
    if wall_seconds is not None:
        lines.append(f"- 耗时: {wall_seconds:.2f} 秒")
    lines.append("")

    lines.extend(_section_overview(cohort_summary, metrics, model_card))
    lines.extend(_section_sources(cohort_summary, provenance_summary))
    lines.extend(_section_predictions(explanations, model_card, horizon, metrics))
    lines.extend(_section_explanations(explanations, importances))
    lines.extend(_section_bias(bias_report))
    lines.extend(_section_reproducibility(model_card, metrics, fingerprint, files))
    lines.extend(_section_limits(cohort_summary, notes))
    lines.extend(_section_commands(run_id))
    return "\n".join(lines) + "\n"


def _section_overview(
    cohort: Mapping[str, Any],
    metrics: Mapping[str, Any],
    card: Mapping[str, Any] | None,
) -> list[str]:
    lines = [f"## {SECTION_TITLES[0]}", ""]
    rows = [
        ["论文数", cohort.get("papers", 0)],
        ["可用于训练（窗口已闭合）", cohort.get("labelled", 0)],
        ["右删失（窗口未闭合，不入训练）", cohort.get("censored", 0)],
        ["有全文", cohort.get("with_full_text", 0)],
        ["语言数", len(cohort.get("languages", {}) or {})],
    ]
    lines.extend(_table(["指标", "值"], rows))
    lines.append("")
    if card:
        lines.append(
            "模型以分位回归给出 80% 区间：不是把被引次数压成一个分数，而是给出"
            "“三年内被引多少”的区间，并把区间宽度作为输出的一部分。"
        )
        lines.append("")
        lines.append(
            f"- 训练行数 {card.get('training_rows', 0)}，留出行数 "
            f"{card.get('holdout_rows', 0)}，特征数 {len(card.get('feature_names', []))}"
        )
        for key in (
            "spearman",
            "top_decile_lift",
            "interval_coverage",
            "median_absolute_error_log",
        ):
            if key in metrics:
                lines.append(f"- {key}: {_number(metrics[key])}")
        coverage = metrics.get("interval_coverage")
        nominal = metrics.get("interval_nominal", 0.8)
        if isinstance(coverage, float) and isinstance(nominal, float):
            records = int(metrics.get("records", 0) or 0)
            error = math.sqrt(nominal * (1 - nominal) / records) if records else 0.0
            low = nominal - 2 * error
            high = nominal + 2 * error
            verdict = "落在噪声范围内" if low <= coverage <= high else "偏离名义覆盖"
            lines.append(
                f"- 区间校准：名义 {nominal:.0%}、实测 {coverage:.0%}，"
                f"{records} 条记录的抽样误差约 ±{error:.1%}，因此该值{verdict}"
                + (
                    "；区间偏窄，实际风险比 80% 乐观。"
                    if coverage < low
                    else "；区间偏宽，属于保守估计。"
                    if coverage > high
                    else "。"
                )
            )
        lines.append("")
    else:
        lines.append(
            "本次运行没有足够带标签的论文，未训练模型：只给出解析结果与可得性，"
            "不给出任何预测数字。"
        )
        lines.append("")
    return lines


def _section_sources(
    cohort: Mapping[str, Any], provenance: Mapping[str, Any]
) -> list[str]:
    lines = [f"## {SECTION_TITLES[1]}", ""]
    rows = [
        ["有标题", provenance.get("with_title", 0)],
        ["有摘要", provenance.get("with_abstract", 0)],
        ["有年份", provenance.get("with_year", 0)],
        ["有作者", provenance.get("with_authors", 0)],
        ["有期刊信息", provenance.get("with_venue", 0)],
        ["有参考文献列表", provenance.get("with_references", 0)],
        ["有开放数据声明", provenance.get("with_open_data", 0)],
        ["有全文（章节级）", provenance.get("with_full_text", 0)],
    ]
    lines.extend(_table(["字段", "论文数"], rows))
    lines.append("")
    routes = provenance.get("routes") or {}
    if routes:
        lines.append("获取路线分布（七级阶梯的落点）：")
        lines.append("")
        lines.extend(
            _table(
                ["路线", "论文数"],
                [[name, count] for name, count in sorted(routes.items())],
            )
        )
        lines.append("")
    coverage = provenance.get("field_coverage") or {}
    if coverage and not any(value for value in coverage.values()):
        lines.append(
            "本次队列没有任何字段级 provenance：合成队列不是从真实来源抽取的，"
            "因此这里不给出“溯源覆盖率 100%”这种假数字。真实抓取快照会有逐字段来源行。"
        )
        lines.append("")
    elif coverage:
        lines.append("逐字段溯源覆盖率（有 provenance 记录的字段占比）：")
        lines.append("")
        lines.extend(
            _table(
                ["字段", "覆盖率"],
                [
                    [name, f"{value:.0%}" if isinstance(value, float) else value]
                    for name, value in sorted(coverage.items())
                ],
            )
        )
        lines.append("")
    lines.append(
        "拿不到全文时，元数据级特征仍然保留，只有全文级特征缺失，并记为 "
        "`missing_reason=full_text_unavailable`；缺失不会被插补成 0。"
    )
    lines.append("")
    return lines


def _section_predictions(
    explanations: Sequence[Any],
    card: Mapping[str, Any] | None,
    horizon: int,
    metrics: Mapping[str, Any] | None = None,
) -> list[str]:
    lines = [f"## {SECTION_TITLES[2]}", ""]
    if explanations and any(
        explanation.paper_id in set((metrics or {}).get("censored_paper_ids") or ())
        for explanation in explanations
    ):
        lines.append(
            "标注“窗口未闭合”的论文，其观测被引还在增长，不能与已闭合窗口的论文直接比较；"
            "这类论文不参与训练，也可能被模型低估，因为模型只学过“三年窗口内”的被引。"
        )
        lines.append("")
    if not explanations:
        lines.append("没有可解释的论文（缺少模型或缺少目标论文）。")
        lines.append("")
        return lines
    censored = set((metrics or {}).get("censored_paper_ids") or ())
    rows = []
    for explanation in explanations:
        observed = str(explanation.observed_citations)
        if explanation.paper_id in censored:
            observed = f"{observed}（窗口未闭合）"
        rows.append(
            [
                explanation.paper_id,
                (explanation.title or "")[:48],
                explanation.year or "未知",
                observed,
                format_interval(explanation.low, explanation.median, explanation.high),
                explanation.extras.get("log_median", ""),
            ]
        )
    lines.extend(
        _table(
            ["论文", "标题", "年份", "窗口内已观测", "预测（三年）", "log 中位数"],
            rows,
        )
    )
    lines.append("")
    if card:
        lines.append(
            "区间来自分位模型（0.1 / 0.5 / 0.9），并做了分位交叉修复：若低分位高于中位数，"
            "会被上抬到中位数并计数上报，不会出现“下界比中位数还高”的假区间。"
        )
        lines.append("")
    calibration = (metrics or {}).get("decile_calibration") or []
    if calibration:
        lines.append("### 分箱校准：模型在哪一段失准")
        lines.append("")
        lines.extend(
            _table(
                ["观测分箱", "篇数", "观测中位数", "预测中位数", "平均残差（log）"],
                [
                    [
                        item.get("bin", ""),
                        item.get("records", ""),
                        item.get("observed_median", ""),
                        item.get("predicted_median", ""),
                        f"{item.get('mean_residual_log', 0):+0.3f}",
                    ]
                    for item in calibration
                ],
            )
        )
        lines.append("")
        tail = calibration[-1]
        residual = float(tail.get("mean_residual_log", 0.0))
        if residual > 0.15:
            lines.append(
                f"**最高被引的一组被系统性低估**：平均残差 {residual:+.3f}（log 尺度），"
                f"该组观测中位数 {tail.get('observed_median')}、预测中位数 "
                f"{tail.get('predicted_median')}。原因是树模型不能外推到训练集取值范围之外，"
                "极端高被引论文会被拉向主体分布；这不是可以靠调参消除的噪声，"
                "而是读这份报告时必须知道的上界限制。"
            )
        elif residual < -0.15:
            lines.append(
                f"**最高被引的一组被系统性高估**：平均残差 {residual:+.3f}（log 尺度）。"
                "同样来自模型对极端值的压缩，只是方向相反。"
            )
        else:
            lines.append(
                f"各箱残差都在 ±0.15 以内（最高箱 {residual:+.3f}），"
                "说明区间在整段观测范围内基本一致，没有明显的高端压缩。"
            )
        lines.append("")
    return lines


def _section_explanations(
    explanations: Sequence[Any], importances: Sequence[Any]
) -> list[str]:
    lines = [f"## {SECTION_TITLES[3]}", ""]
    if explanations:
        first = explanations[0]
        lines.append(f"### {first.paper_id} 的因素拆解")
        lines.append("")
        lines.extend(
            _table(
                ["因素", "观测值", "队列参考", "Δ（被引）", "95% CI", "方向", "占比"],
                [
                    [
                        effect.label,
                        "缺失" if effect.observed is None else f"{effect.observed:.3g}",
                        f"{effect.reference:.3g}",
                        f"{effect.delta_counts:+.1f}",
                        f"{effect.ci_low:+.1f} ~ {effect.ci_high:+.1f}",
                        effect.direction,
                        f"{effect.share:.0%}",
                    ]
                    for effect in first.factors
                ],
            )
        )
        lines.append("")
        lines.append(
            "**怎么读**：把该论文的一个特征设成队列参考值，再看预测怎么变；"
            "Δ 是点数估计，95% CI 来自对“参考值本身”的 bootstrap 重采样。"
            "如果 CI 跨 0，这条因素就不能当作解释。"
        )
        lines.append("")
        if first.comparators:
            lines.append("### 相似论文对照（特征空间最近邻）")
            lines.append("")
            lines.extend(
                _table(
                    ["论文", "年份", "期刊", "已观测被引", "预测中位数", "距离"],
                    [
                        [
                            (item.title or item.paper_id)[:44],
                            item.year or "未知",
                            item.venue or "未知",
                            item.observed_citations,
                            f"{item.predicted_median:.0f}",
                            f"{item.distance:.3f}",
                        ]
                        for item in first.comparators
                    ],
                )
            )
            lines.append("")
    if importances:
        lines.append("### 模型整体依赖：置换重要性")
        lines.append("")
        lines.extend(
            _table(
                ["#", "特征", "排序损失", "95% CI", "结论"],
                [
                    [
                        item.rank,
                        item.label,
                        f"{item.mean_drop:+.3f}",
                        f"{item.ci_low:+.3f} ~ {item.ci_high:+.3f}",
                        "有信号" if item.supported else "与噪声不可区分",
                    ]
                    for item in importances[:10]
                ],
            )
        )
        lines.append("")
        lines.append(
            "方法是打乱某一列后重算 Spearman 排序相关，跨多个随机种子取均值与区间；"
            "没有使用 SHAP（环境没有该依赖），所以这里的归因是可复算的置换量，"
            "不是博弈论意义上的 Shapley 值。"
        )
        lines.append("")
    return lines


def _section_bias(bias_report: Any) -> list[str]:
    lines = [f"## {SECTION_TITLES[4]}", ""]
    if bias_report is None:
        lines.append("本次运行未执行偏差审计。")
        lines.append("")
        return lines
    counts = bias_report.counts()
    lines.append(
        f"{counts['audits']} 类审计：{counts['detected']} 类检出系统性差异，"
        f"{counts['cannot_assess']} 类明确无法评估。"
    )
    lines.append("")
    lines.extend(
        _table(
            ["审计", "结论状态", "严重度", "结论"],
            [
                [
                    finding.label,
                    finding.status,
                    finding.severity,
                    finding.conclusion,
                ]
                for finding in bias_report.findings
            ],
        )
    )
    lines.append("")
    for finding in bias_report.findings:
        lines.append(f"### {finding.label}")
        lines.append("")
        lines.append(f"- 状态：{finding.status}（严重度 {finding.severity}）")
        lines.append(f"- 方法：{finding.method}")
        lines.append(f"- 证据：`{_compact(finding.evidence)}`")
        lines.append(f"- 结论：{finding.conclusion}")
        if finding.recommendation:
            lines.append(f"- 建议：{finding.recommendation}")
        lines.append("")
    for note in bias_report.notes:
        lines.append(f"- {note}")
    lines.append("")
    return lines


def _section_reproducibility(
    card: Mapping[str, Any] | None,
    metrics: Mapping[str, Any],
    fingerprint: str,
    files: Mapping[str, str] | None,
) -> list[str]:
    lines = [f"## {SECTION_TITLES[5]}", ""]
    rows: list[list[Any]] = []
    if card:
        rows.append(["模型指纹", card.get("fingerprint", "")])
        rows.append(["队列指纹", card.get("cohort_fingerprint", "")])
        rows.append(["随机种子", card.get("seed", "")])
        rows.append(["超参数", _compact(card.get("hyperparameters", {}))])
        rows.append(["分位交叉修复次数", card.get("quantile_crossings", "")])
    if fingerprint:
        rows.append(["运行指纹", fingerprint])
    for key in ("determinism", "wall_seconds", "cohort_fingerprint"):
        if key in metrics:
            rows.append([key, metrics[key]])
    lines.extend(_table(["项", "值"], rows))
    lines.append("")
    lines.append(
        "同一冻结快照 + 同一随机种子 => 同一条模型指纹、同一份指标；"
        "manifest.json 里记录了每个产物的 sha256，改动任何一个文件都会被发现。"
    )
    lines.append("")
    if files:
        lines.append("产物清单：")
        lines.append("")
        lines.extend(
            _table(
                ["文件", "路径"], [[role, path] for role, path in sorted(files.items())]
            )
        )
        lines.append("")
    return lines


def _section_limits(cohort: Mapping[str, Any], notes: Sequence[str]) -> list[str]:
    lines = [f"## {SECTION_TITLES[6]}", ""]
    lines.append(
        "- **作者性别/族裔不做推断**：姓名分类器的错误率远大于要审计的效应，"
        "把它写成数字等于把不可靠估计洗成证据；如需人口学分析应由自报或权威字段提供。"
    )
    lines.append(
        "- **自引通胀当前无法评估**：快照只有论文级被引总数，缺少“每条参考文献属于谁”的"
        "作者级信息，因此无法把自引从外部引用中分离。"
    )
    lines.append(
        "- **右删失**：窗口尚未闭合的论文不参与训练，也不会被当成低影响力；"
        "其数量在概要与审计中单独计数。"
    )
    lines.append(
        "- **被引不是影响力**：被引次数受领域规模、期刊可见度与语言影响，"
        "本报告的偏差审计正是为了把这些因素显式化，而不是消除它们。"
    )
    labelled = cohort.get("labelled", 0)
    if labelled and labelled < 100:
        lines.append(
            f"- **样本量**：仅 {labelled} 篇论文窗口已闭合，"
            "区间外推能力有限；跨队列比较需要更大的冻结快照。"
        )
    lines.append(
        "- **本环境无网络**：真实 OpenAlex/Crossref/Unpaywall 抓取未在本机联网执行，"
        "随附的抓取脚本用于在有网络的机器上重建快照；离线基准使用合成队列，"
        "其指标反映的是生成器的已知结构，不能当作天体物理领域的真实预测能力。"
    )
    for note in notes:
        lines.append(f"- {note}")
    lines.append("")
    return lines


def _section_commands(run_id: str) -> list[str]:
    lines = [f"## {SECTION_TITLES[7]}", ""]
    lines.append("```bash")
    lines.append("# 用同一冻结快照重跑（离线、零网络）")
    lines.append(
        'jw  # 在会话中调用 infl_run(snapshot_path="<snapshot.json>", '
        "reference_year=<year>)"
    )
    lines.append("")
    lines.append("# 直接用 Python 复现指标")
    lines.append("python -m pytest tests/research_influence -q")
    lines.append("```")
    lines.append("")
    lines.append(f"本次运行的 run_id 为 `{run_id}`。")
    lines.append("")
    return lines


def _number(value: Any) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "不可用"
        return f"{value:.4f}"
    return str(value)


def _compact(payload: Any) -> str:
    import json

    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)[
            :1200
        ]
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(payload)[:1200]
