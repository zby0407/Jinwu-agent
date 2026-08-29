#!/usr/bin/env python3
"""Advisory audit for production-context residue in reader-visible prose."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple


SUPPORTED_SUFFIXES = {".md", ".markdown", ".tex", ".txt", ".rst"}
CATEGORIES = {
    "dialogue_trace",
    "assistant_meta",
    "production_motive",
    "revision_trace",
    "vague_attribution",
    "audience_meta",
    "workflow_narration",
}


class Finding(NamedTuple):
    category: str
    severity: str
    line: int
    column: int
    match: str
    message: str


class Rule(NamedTuple):
    category: str
    severity: str
    pattern: re.Pattern[str]
    message: str
    final_only: bool = False
    needs_missing_citation: bool = False


RULES = (
    Rule(
        "dialogue_trace",
        "blocker",
        re.compile(
            r"按(?:照)?(?:你|您|用户)(?:的)?要求|"
            r"(?:我们|你我)(?:刚才|之前|此前).{0,16}(?:讨论|决定|约定|确认)|"
            r"用户与助手.{0,16}(?:讨论|决定|约定|确认)|"
            r"希望这对(?:你|您)有帮助|"
            r"(?:如果|若)(?:你|您)想让我|请告诉我(?:是否|要不要)?|"
            r"as (?:you|the user) requested|"
            r"(?:we|you and I) (?:just|previously) (?:discussed|decided|agreed)|"
            r"I hope this helps|let me know if you(?: would|'d)? like",
            re.IGNORECASE,
        ),
        "疑似用户—助手协作话语；成品应直接陈述对读者成立的内容。",
    ),
    Rule(
        "assistant_meta",
        "blocker",
        re.compile(
            r"作为(?:一个|一名)?\s*(?:AI|人工智能|语言模型)|"
            r"(?:我|本助手)(?:无法|不能).{0,20}(?:访问|核验|浏览|提供)|"
            r"as an AI(?: language model)?|"
            r"I (?:cannot|can't).{0,30}as (?:an )?(?:AI|language model)",
            re.IGNORECASE,
        ),
        "疑似助手身份或能力声明；这类说明不应进入读者成品。",
    ),
    Rule(
        "production_motive",
        "review",
        re.compile(
            r"(?:同学|学生|读者|评委|领导).{0,24}"
            r"(?:没有|缺少|都是|是).{0,28}"
            r"(?:因此|所以|故).{0,28}"
            r"(?:本|这份)(?:讲义|报告|文稿|材料|PPT|演示|说明书)|"
            r"because (?:the )?(?:students?|readers?|judges?|audience).{0,50}"
            r"(?:this|the) (?:report|manual|handout|document|presentation)",
            re.IGNORECASE,
        ),
        "疑似把私人委托背景或写作动机写入正文；应转化为成品属性。",
        final_only=True,
    ),
    Rule(
        "revision_trace",
        "review",
        re.compile(
            r"(?:第[一二三四五六七八九十\d]+版|本版|本次修订).{0,24}"
            r"(?:已|新增|补充|修改|修正|删除|调整|遗漏|缺少)|"
            r"(?:此前|之前|上一版).{0,20}(?:遗漏|缺少|未|没有)|"
            r"(?:version\s*\d+|third version|this revision).{0,30}"
            r"(?:fixed|added|removed|corrected|omitted|missing)",
            re.IGNORECASE,
        ),
        "疑似修订或差异叙事；仅在变更日志、时间线或迁移说明中保留。",
        final_only=True,
    ),
    Rule(
        "audience_meta",
        "review",
        re.compile(
            r"评委(?:可|需要|应当|可以|关注|核对|检查|判断|打分)|"
            r"(?:供|面向)评委|"
            r"评委可核对|"
            r"(?:交付|展示|验收)标准|"
            r"本(?:页|稿|展示稿|材料)(?:展示|说明|呈现)"
        ),
        "疑似直接对评委或交付过程说话；将评审需求转化为结果、依据和适用范围，不在成品中呼叫受众。",
        final_only=True,
    ),
    Rule(
        "workflow_narration",
        "review",
        re.compile(
            r"(?:证据链|证据闭环)(?:构建|形成|完整)|"
            r"正式运行(?:结果)?|结果页|"
            r"(?:已修复|早期整理|上一版|本次修订)|"
            r"(?:系统|平台)(?:从|先|随后|最终|没有把|会制造|展示了)"
        ),
        "疑似把系统流程、修订史或内部验收叙事写入成品；正文应直接陈述研究方法、结果和边界。",
        final_only=True,
    ),
    Rule(
        "vague_attribution",
        "review",
        re.compile(
            r"(?:专家|业内人士|观察者)(?:普遍)?(?:认为|指出|表示)|"
            r"(?:研究|行业报告|多项研究)(?:普遍)?(?:表明|显示|指出)|"
            r"(?:experts?|observers?|industry reports?|studies)"
            r"(?: generally)? (?:argue|believe|show|suggest|indicate)",
            re.IGNORECASE,
        ),
        "存在模糊归因且同一行未见引用；请核对来源或降低主张强度。",
        needs_missing_citation=True,
    ),
)


CITATION_RE = re.compile(
    r"\[[0-9,\-–— ]+\]|"
    r"[（(][^()（）\n]{1,80}(?:19|20)\d{2}[a-z]?[^()（）\n]{0,30}[）)]|"
    r"\bdoi\s*:|\bhttps?://",
    re.IGNORECASE,
)


def _strip_markdown_fences(text: str) -> str:
    output: list[str] = []
    in_fence = False
    for line in text.splitlines(keepends=True):
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            output.append("\n" if line.endswith("\n") else "")
        elif in_fence:
            output.append("\n" if line.endswith("\n") else "")
        else:
            output.append(line)
    return "".join(output)


def _strip_latex_comments(text: str) -> str:
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        match = re.search(r"(?<!\\)%", line)
        if match:
            ending = "\n" if line.endswith("\n") else ""
            line = line[: match.start()] + ending
        output.append(line)
    return "".join(output)


def visible_text(text: str, suffix: str = ".txt") -> str:
    """Remove common non-visible regions while preserving line numbering."""
    normalized_suffix = suffix.lower()
    if normalized_suffix in {".md", ".markdown"}:
        text = re.sub(
            r"<!--.*?-->",
            lambda match: "\n" * match.group(0).count("\n"),
            text,
            flags=re.DOTALL,
        )
        return _strip_markdown_fences(text)
    if normalized_suffix == ".tex":
        return _strip_latex_comments(text)
    return text


def audit_text(text: str, mode: str = "final") -> list[Finding]:
    """Return advisory findings for visible text."""
    if mode not in {"final", "process-record"}:
        raise ValueError(f"unsupported mode: {mode}")

    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for rule in RULES:
            if rule.final_only and mode != "final":
                continue
            if rule.needs_missing_citation and CITATION_RE.search(line):
                continue
            for match in rule.pattern.finditer(line):
                findings.append(
                    Finding(
                        category=rule.category,
                        severity=rule.severity,
                        line=line_number,
                        column=match.start() + 1,
                        match=match.group(0),
                        message=rule.message,
                    )
                )
    return findings


def _read_source(source: str, stdin_name: str) -> tuple[str, str, str]:
    if source == "-":
        return stdin_name, sys.stdin.read(), Path(stdin_name).suffix or ".txt"

    path = Path(source)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(
            f"{path}: unsupported input; extract visible text first "
            f"(supported: {supported}, or use '-' for stdin)"
        )
    return str(path), path.read_text(encoding="utf-8"), path.suffix


def _serialize(source: str, finding: Finding) -> dict[str, object]:
    return {
        "source": source,
        "line": finding.line,
        "column": finding.column,
        "category": finding.category,
        "severity": finding.severity,
        "match": finding.match,
        "message": finding.message,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Audit user-specified reader-visible text for production-context residue. "
            "The tool reports findings and never edits input."
        )
    )
    parser.add_argument("sources", nargs="+", help="UTF-8 text paths or '-' for stdin")
    parser.add_argument(
        "--mode",
        choices=("final", "process-record"),
        default="final",
        help="final suppresses production/revision residue; process-record permits it",
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", dest="output_format"
    )
    parser.add_argument(
        "--allow",
        action="append",
        choices=sorted(CATEGORIES),
        default=[],
        help="suppress one advisory category; may be repeated",
    )
    parser.add_argument(
        "--stdin-name",
        default="stdin.txt",
        help="display name and suffix hint for '-' input",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="return exit code 1 when unsuppressed findings remain",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    serialized: list[dict[str, object]] = []

    try:
        for source in args.sources:
            source_name, raw_text, suffix = _read_source(source, args.stdin_name)
            text = visible_text(raw_text, suffix=suffix)
            for finding in audit_text(text, mode=args.mode):
                if finding.category not in args.allow:
                    serialized.append(_serialize(source_name, finding))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.output_format == "json":
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "finding_count": len(serialized),
                    "findings": serialized,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif not serialized:
        print("No advisory findings.")
    else:
        for item in serialized:
            print(
                f"{item['source']}:{item['line']}:{item['column']} "
                f"[{item['severity']}/{item['category']}] {item['match']}"
            )
            print(f"  {item['message']}")
        print(f"Advisory findings: {len(serialized)}")

    if args.fail_on_findings and serialized:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
