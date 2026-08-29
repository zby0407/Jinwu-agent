---
name: writing-reader-facing-content
description: 当 JW 创建、修订或定稿报告、论文、手册、方案、幻灯片、海报或技术说明等读者可见内容时使用，尤其适合清除内部对话、版本史和证据层级混淆。
---

# 撰写读者可见内容

## Overview

把内部 brief 当作写作素材，而不是可直接发布的正文。将内部约束转化为读者需要的成品属性，准确标注证据层级，并只审校读者最终会看到的文字。

## JW 使用边界

JW 的科研正文使用“研究问题—数据—方法—证据—不确定性—结论”的自然叙事；内部 Agent 名、阶段状态、回执、候选内部 ID、rubric、hash 与调试记录留在系统状态，不进入读者成品。

## Required artifact contract

Before drafting, fix four fields internally:

1. **Audience**: who will read or hear it?
2. **Purpose**: what should they understand, decide, or do?
3. **Status**: internal draft, review copy, or final deliverable?
4. **Process allowance**: none, methods, version history, timeline, or changelog?

Do not print this contract in the artifact unless the artifact itself requires it.

## Keep three channels separate

| Channel | Contains | Destination |
|---|---|---|
| Internal brief | motives, constraints, private audience facts, operator notes | working context only |
| Work/status | edits made, missing inputs, checks, tool output | chat or audit report |
| Artifact | claims, explanations, methods, limitations, instructions | reader-visible deliverable |

Translate rather than repeat:

- “Students lack the textbook” → supply complete definitions and proofs.
- “Judges are experts” → use the right technical density and evidence.
- “The user dislikes AI phrasing” → write direct, natural prose.
- “The third draft fixed an omission” → present the corrected content; retain the history only in a changelog or timeline.

### Judge-facing deliverables: show evidence, never address the judge

“评委展示稿”“面向评委”描述的是使用场景，不是成品中的句式。最终论文、
海报、演示文稿和展板应直接围绕研究对象陈述：

| 内部意图 | 成品表达 |
|---|---|
| 让评委快速判断可信度 | 将结论、定量结果、来源和适用范围放在相邻位置。 |
| 说明评委可以检查什么 | 直接写变量定义、比较协议、不确定性和可证伪条件。 |
| 强调系统做过哪些步骤 | 写采用的方法及其结果，省略操作者和工作流叙述。 |

正式成品不得出现直接呼叫受众或交付过程的措辞，包括“评委可核对”、
“评委关注”“交付标准”“本页展示系统”“证据链构建”“证据闭环”、
“正式运行结果”“已修复”等同类表达。若系统本身是研究对象，可以在方法
章节作技术性描述，但仍不得对评委讲话，也不得把构建历史写成研究结果。

## Accepted-state finalization

After a correction, discarded proposal, or long revision session, regenerate the
deliverable from the accepted and verified state. Treat session-only alternatives
and wording corrections as control information. They do not belong in the title,
opening, labels, filenames, captions, metadata, handoff, or body unless the
audience needs a comparison, audit, migration explanation, quotation, or other
explicit process record.

Inspect every user-visible surface separately. Preserve real safety,
compatibility, legal, audit, limitation, and negative-result information. This
rule governs session residue; it does not ban necessary negative facts or turn an
exact-term scan into a semantic quality claim. See
[references/no-negative-echo-finalization.md](references/no-negative-echo-finalization.md)
for the compact checklist and source attribution.

## Preserve evidence status

Classify each consequential statement as a user-provided fact, source-verified fact, tool observation, or inference. Keep uncertainty and validation limits. Never promote a compressed label, assistant observation, or vague attribution into a confirmed fact.

Methods, limitations, reproducibility details, and legitimate technical paths may remain when they serve the artifact. User-agent dialogue, assistant identity statements, and private commissioning motives do not become methodology.

## Compose with other skills

Let domain skills control substance and evidence. Let DOCX, PPTX, PDF, Markdown, LaTeX, and presentation skills control file structure and rendering. Let `humanizer` or `humanizer-zh` refine register. This skill owns the publication boundary and the final visible-text gate.

## Final visible-text gate

Review the rendered or extracted visible text, not only source files:

1. Every sentence serves the audience and purpose.
2. Internal brief, status narration, and revision residue stay in their channels.
3. Claims retain their real source and confidence level.
4. Tone matches the venue without slogans, invented authority, or assistant chatter.
5. Speaker notes, appendices, captions, and metadata receive the same review if the audience can see them.

6. Titles, openings, filenames, labels, and handoffs describe the accepted final
   state rather than the history of rejected alternatives.

7. 对评委展示类成品，运行扫描器并对 `audience_meta` 与
   `workflow_narration` 做语义复核；除非是明确要求保留的引文或过程记录，
   正式成品必须为零发现。

Run the advisory scanner on user-specified text:

```bash
python3 <skill-dir>/scripts/audit_visible_text.py report.md
python3 <skill-dir>/scripts/audit_visible_text.py - --stdin-name slides.txt
python3 <skill-dir>/scripts/audit_visible_text.py timeline.md --mode process-record
```

For DOCX, PPTX, or PDF, first extract visible text with the relevant format skill and pipe it through standard input. The scanner never edits input. Read every finding semantically; use `--allow <category>` for an intentional exception and `--fail-on-findings` only when a clean automated gate is desired.

## Common mistakes

- Copying the reason for a requirement instead of realizing the requirement.
- Hiding valid methods because they look like “process.”
- Leaving feedback or a change summary inside the final file.
- Treating a scanner hit as proof rather than a review prompt.
- Cleaning style while changing facts, terminology, or uncertainty.
