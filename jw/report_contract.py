"""Shared field contract for scientific hypothesis, plan and experiment reports."""

from __future__ import annotations

import re

# The document's H1 is Paper Title; the other fields have explicit headings.
REPORT_FIELDS = (
    (
        "paper_title",
        "标题",
        "Paper Title",
        "符合学术出版规范、准确限定研究范围的具体标题。",
    ),
    (
        "paper_abstract",
        "摘要",
        "Paper Abstract",
        "包含背景、方法和预期结果；另行区分实际结果与尚未验证的预期。",
    ),
    (
        "problem_statement",
        "待研究问题",
        "Problem Statement",
        "指出当前领域的具体局限及本次要回答的问题。",
    ),
    (
        "rationale",
        "解决思路",
        "Rationale",
        "说明创新思路及可审查的证据、假设和可检验推论之间的逻辑，不输出内部思维链。",
    ),
    (
        "technical_details",
        "必要的技术手段",
        "Technical Details",
        "具体统计、机器学习或深度学习方法、模型、技术栈及关键设置；未确定项如实说明。",
    ),
    (
        "datasets",
        "数据集",
        "Datasets",
        "说明真实来源、版本、覆盖范围、使用权限与资格限制，不将未核验数据标为合格。",
    ),
    ("source", "Source", "Source", "假设推演依据的历史数据、来源定位及适用范围。"),
    (
        "target",
        "Target",
        "Target",
        "验证所需的拟采集数据特征、变量、时空覆盖和采样要求；仅用既有数据时明确说明。",
    ),
    (
        "methods",
        "方法论",
        "Methods",
        "具体实施步骤、模型架构或实验流程，并区分计划与已执行步骤。",
    ),
    (
        "experiments",
        "实验设计",
        "Experiments",
        "检验方案、划分、对照、资源与停止条件。",
    ),
    (
        "baselines",
        "基线对比",
        "Baselines",
        "基线和选择依据；不适用时说明理由，不虚构已完成的比较。",
    ),
    (
        "metrics",
        "评估指标",
        "Metrics",
        "指标、估计对象、单位、判定依据及不确定性评价。",
    ),
    (
        "results",
        "实验结果",
        "Results",
        "只报告真实公式推导或实际执行所得的可追溯结果及可行性边界；未完成时说明缺口。",
    ),
    (
        "references",
        "参考论文",
        "References",
        "列出实际引用且可核验的真实文献及定位，严禁编造作者、年份、标题或 DOI；没有合格论文时明示。",
    ),
)
REPORT_SUBFIELDS = {
    "source": "datasets",
    "target": "datasets",
    "baselines": "experiments",
    "metrics": "experiments",
}

REPORT_TEMPLATE = (
    "# Experiment Report Template (Required)\n\n"
    + (
        "最终《科学假设与研究计划》及实验报告必须覆盖下列全部字段。"
        "使用具体学术标题作为唯一一级标题，其他字段使用下列中英文字段名作为二级标题；"
        "Source/Target 放在 Datasets 下，Baselines/Metrics 放在 Experiments 下，使用三级标题。\n\n"
    )
    + "\n".join(
        f"- **{chinese if chinese == english else f'{chinese}（{english}）'}**：{description}"
        for _, chinese, english, description in REPORT_FIELDS
    )
    + (
        "\n\n字段不得省略、留空或只写 TBD/N/A/待补充。缺失、不适用、未执行时，"
        "写明具体原因、结论限制和所需后续证据；不得为了填满字段制造数据、实验或参考论文。"
        "字段齐全只代表结构完整，科学内容仍须由原生生产与独立审查确认。"
        "可另附 summary、experiment plan、setup、limitations 和 next steps，不能替代必需字段。\n"
    )
)


def _normal_heading(text: str) -> str:
    text = re.sub(r"^[\d一二三四五六七八九十]+[.、．)）\s]+", "", text)
    return re.sub(r"[\W_]+", "", text).casefold()


def report_field_key(heading: str) -> str | None:
    name = _normal_heading(heading)
    for key, chinese, english, _ in REPORT_FIELDS:
        aliases = {
            "methods": ("方法", "数据与方法"),
            "results": ("结果",),
        }.get(key, ())
        names = (
            chinese,
            english,
            chinese + english,
            *aliases,
            *(alias + english for alias in aliases),
        )
        if name in {_normal_heading(t) for t in names}:
            return key
    return None


def report_sections(markdown: str, *, include_code: bool = False) -> dict[str, str]:
    """Read real Markdown sections, excluding code fences and HTML comments."""
    clean = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    lines = clean.splitlines()
    visible: list[str] = []
    fence: str | None = None
    for line in lines:
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            visible.append("")
        else:
            visible.append(line if fence is None else "")
    headings = []
    for index, line in enumerate(visible):
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((index, len(match[1]), match[2]))
    sections: dict[str, str] = {}
    for position, (start, level, heading) in enumerate(headings):
        key = report_field_key(heading)
        if level == 1 and key is None:
            sections.setdefault("paper_title", heading.strip())
            continue
        if key is None:
            continue
        if key in REPORT_SUBFIELDS:
            ancestors = [h for h in headings[:position] if h[1] < level]
            if (
                not ancestors
                or report_field_key(ancestors[-1][2]) != REPORT_SUBFIELDS[key]
            ):
                continue
        end = next(
            (
                i
                for i, depth, _ in headings[position + 1 :]
                if depth <= level or key == "paper_title"
            ),
            len(visible),
        )
        body = "\n".join((lines if include_code else visible)[start + 1 : end]).strip()
        sections.setdefault(key, body)
    return sections


def validate_report_fields(markdown: str) -> None:
    """Reject missing/empty fields; factual adequacy belongs to Evidence review."""
    sections = report_sections(markdown)
    missing = []
    for key, _, english, _ in REPORT_FIELDS:
        body = re.sub(r"(?m)^\s*#{1,6}\s+.*$", "", sections.get(key, "")).strip()
        if not body or re.fullmatch(
            r"[\s*_.—-]*(?:TBD|TODO|N/?A|待补充|暂无|未提供|不适用)?[\s*_.—-]*",
            body,
            re.I,
        ):
            missing.append(english)
    if missing:
        raise ValueError(
            "Scientific report has missing or empty required fields: "
            + ", ".join(missing)
            + ". Use explicit report headings and explain unavailable/not-applicable fields; never invent evidence."
        )
