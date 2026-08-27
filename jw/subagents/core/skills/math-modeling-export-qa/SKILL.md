---
name: math-modeling-export-qa
description: Export and visual-QA skill for mathematical modeling contest deliverables. Use when a contest paper, appendix, figure set, or support-material release has been exported and needs final checks for formula rendering, figure readability, font consistency, judge-facing package cleanliness, and screenshot-based visual acceptance.
---

# Math Modeling Export QA

Use this skill only near the end, when the content already exists and the question is whether the exported deliverable is visually safe and structurally clean enough to submit.

## Project Delivery Policy Gate

First read the resolved project's `delivery_policy`. When
`appendix_mode=draft_only`, reject a formal appendix in the PDF/DOCX/source and
accept only `04_提交材料/论文/附录草稿.md` as a non-integrated draft. For CUMCM,
verify page 1 contains only abstract and keywords and the body starts on page
2. Do not waive these checks because an official format mentions an appendix or
because the export already compiles.

Before passing export QA, require `audit_release_candidate` from
`math-modeling-release-guard` to pass against the same policy.

## Naming Convention for Chinese Contests

Do not force English-only names for folders, spreadsheets, figures, or standalone run scripts in a Chinese mathematical-modeling workspace.
Prefer clear Chinese or mixed Chinese-English names when they make the package easier for the team to read.
Keep English names only where imports, hard-coded paths, or tool stability would otherwise break.

## Environment and Dependency Policy

When export QA needs a missing package, converter, renderer, OCR tool, PDF
utility, screenshot tool, font, or inspection dependency, identify the exact
need, its QA impact, and a safe fallback. Prefer an existing
project-approved environment. Installation, download, credential use, and
privilege changes require explicit user authorization; only after that
authorization may a minimal recovery command or helper be prepared.

## Use This Skill When

- the paper has been exported to PDF, DOCX, HTML, LaTeX, or another visual form
- formulas, figures, tables, or code blocks may render incorrectly
- the support-material release may still contain internal-only files
- the user wants a final visual QA pass before submission

## Default QA Baseline

Do not treat export QA as only a renderer check. For contest papers, QA must also protect:

- page rhythm
- typography compliance
- figure insertion rhythm
- appendix readability
- support-material naming realism

By default, assume the paper should read like an excellent judged contest paper even if the user did not explicitly mention national-prize examples.

## Two-Level QA

Always do both:

1. page-level export QA
2. figure-level render QA

Use a lighter gate for ordinary pages and a stricter gate for figures.

Page-level checks are not enough on their own.

Any formal figure that has not been checked in a rendered screenshot or rendered preview is not considered QA-complete.

Use bundled scripts when appropriate:

- `scripts/check_docx_semantics.py` for DOCX OMML formula, raw formula text, TOC/link, caption, and color checks

When `zbot-word` is exposed, use it to inspect the actual DOCX through Word COM,
update fields only in a separate copy when needed, and export a Word-rendered PDF
for page QA. Static DOCX XML checks and Word-native rendering are complementary;
neither replaces the other.

## Native Bridge Export Verification

When visual risk exists only in a final renderer, use an available native or
GUI bridge if it materially improves evidence. Keep script checks for
structure, but require a real open, render, inspect, or export receipt before
claiming visual correctness. The receipt may be a screenshot, page count,
exported artifact, visible formula/table/figure status, or recorded failure.

Store QA evidence in the resolved project's internal validation area, not in
judge-facing materials unless the user or contest explicitly requests an
internal archive. Host-specific application and staging instructions belong in
`runtime-adapters/windows-native-bridge.md`.

## Core Checks

### Formula Checks

- formulas render as math rather than raw LaTeX
- inline variables and displayed equations survive export cleanly
- code blocks are not damaged by math rendering
- symbol tables and variable definitions in Word use proper equation/math formatting for formula letters, subscripts, and indexed symbols when those symbols are part of the formal notation, instead of leaving all notation as plain body text
- do not delete user comments merely to make the document look clean; treat comments as issue markers and solve the underlying numbering, formula, figure, or text problem unless the user explicitly asks to remove comments

### Font and Binding Checks

- if the venue or user requires a specific body font such as 宋体小四, verify the source actually binds to the intended font file or font family rather than only uploading the font asset
- for dual-environment workflows such as local plus Overleaf, verify that the current exported file uses the intended font route in the active environment
- do not assume "font file exists in the tree" means the export truly used it

### DOCX Semantic QA Gate

For DOCX contest deliverables, visual similarity is not enough. Check the internal Word structure:

- displayed formulas use Word OMML objects such as `m:oMath` or `m:oMathPara`
- ordinary body paragraphs do not retain visible raw LaTeX or source-like formula strings such as `\\frac`, `_{...}`, `^{...}`, `I_conf`, `x_nom`, or `arg max`
- table of contents uses TOC fields or internal hyperlinks/bookmarks rather than a hand-typed plain list
- figure and table captions are consistent and centered according to the paper style
- headings and body text do not retain non-black direct colors unless the official template requires them
- code appendix blocks are not accidentally converted into math or damaged by formula conversion
- official front matter starts on the correct pages and in the correct order

If these checks fail, do not call the DOCX final. For formula checks, code appendix paragraphs may be excluded only after confirming they are genuine code blocks.

### Figure and Table Checks

- titles, axes, legends, annotations, and captions are readable
- Chinese explanatory text is Chinese unless English is standard for a metric, formula, or algorithm
- Chinese text uses 宋体 or the intended Song-family fallback where controllable
- English text uses Times New Roman or the intended Times-family fallback where controllable
- ordinary figure text is normally 10-12 pt and not larger than 12 pt unless the user or venue explicitly asks otherwise
- for Chinese contest figures, verify mixed character runs: Chinese uses 宋体,
  while Latin letters and digits use Times New Roman; a file-level font list is
  not sufficient evidence
- for Origin-to-Visio data charts, verify the final Visio line hierarchy rather
  than reusing nominal Origin line weights
- no overlap, clipping, missing panels, or crushed labels
- no semantic element visibly covers, crowds, or collides with another semantic element
- legends, annotation boxes, or color bars do not cover titles, key labels, or data regions
- key figures have a traceable source path or app-native build trace
- the figure visible in the current paper matches the final figure file placed in the support materials; for DOCX, extract or screenshot embedded figures when there is any doubt
- Python-generated formal figures have a print-quality raster export, preferably 600 dpi PNG, and a vector export such as SVG when the chart type supports it
- line art, plots, flowcharts, and proof schematics may retain SVG when a vector bridge is useful; final raster delivery is a sharp 600 dpi PNG, and standalone figure PDFs are not generated or packaged
- proof schematics use the same notation as the surrounding formulas and do not contain unexplained variables or decorative geometry

### Judge-Package Boundary Checks

Verify that judge-facing support materials do not accidentally include:

- redraw specifications
- review notes
- export QA logs
- preview files
- paper assembly scripts
- package assembly scripts
- internal memos

### Page-Screenshot Checks

Spot-check at least:

- one formula-heavy page
- one figure-heavy page
- one table-heavy page
- one appendix code page if code appears in the appendix
- one page containing a flowchart or framework diagram if such a page exists

Also inspect for page-rhythm defects such as:

- a figure or table stranded alone on a page with weak information density
- a single sentence orphaned above or below a large float
- abnormal blank space created by manual spacing hacks
- a flowchart or framework page that interrupts reading rhythm more than it helps it
- appendix code blocks whose framing or line wrapping dominates the page visually

Do not turn ordinary text-page QA into a heavy ritual unless the export already shows obvious problems.

For national-prize-target submissions, current-paper rebuilds after major rewriting, or any time the user explicitly asks for page-by-page inspection, render and inspect every page image at least once before final delivery. Record defects by page number internally, then fix the source rather than only reporting cosmetic issues.

### Figure-Screenshot Checks

Review every formal figure used in the paper for:

- text overlap
- label clipping
- connector collisions
- unreadable font size
- uneven spacing
- inconsistent font families
- primary lines that are visually too heavy at insertion size, or grids and axes
  that compete with the data
- ordinary tick labels inside the plotting region, axis titles touching tick
  labels, or labels that intrude into data marks
- mismatch between the embedded paper version and the support-material version
- internal paths, script names, sync notes, preview notes, TODOs, or other operator-only wording visible inside the figure
- proof or method schematics whose labels do not match the paper notation
- visually weak or semantically vague final appearance
- missed layout or readability defects that should have been corrected before export

Check dense tables and multi-panel figures at the intended paper insertion width, not only at their original export size.

## Blocking Defects

- visible raw LaTeX such as `\\frac`, `\\beta`, `$$...$$`, `\\(...\\)`, or `\\[...\\]`
- DOCX body formulas left as plain source-like text instead of Word OMML objects
- DOCX table of contents is a plain hand-typed list when the venue expects a usable Word/PDF TOC
- formal paper headings retain blue, red, purple, theme accent, or other decorative colors not required by the official template
- Chinese contest figures with obvious English explanatory leftovers
- code blocks rendered as broken math-like text
- missing or overlapping figures and tables
- any formal figure with unreadable labels or obvious collision problems
- any Origin-to-Visio figure lacking final-app line normalization, mixed-run
  font verification, or axis-spacing inspection at actual insertion width
- any formal figure whose support-material file is a different version from the one inserted in the current paper without an explicit reason
- judge-facing support materials polluted by internal-only files
- formal paper pages contain local paths, tool-version strings, screenshot/export QA notes, evidence-position labels, appendix-support labels, or package-assembly language
- flowcharts or framework diagrams are visibly rough, misrouted, or inconsistent with the paper's formal style
- legends, annotation boxes, color bars, inset panels, labels, arrows, text boxes, or tables visibly cover, crowd, or collide with titles, axes, labels, or data regions
- required venue font/size rules are claimed but not actually achieved in the exported file
- page rhythm is visibly broken by isolated lead-in sentences, oversized whitespace, or a low-information figure occupying a full page without justification

## Common Failure Modes

- paper content is good but export is unreadable
- figures look acceptable at page thumbnail size but fail when inspected alone
- fonts drift into random defaults across paper, figures, and support tables
- appendix code is present but formatting collapses after export
- support materials are technically complete but include internal operator files that judges never needed to see
