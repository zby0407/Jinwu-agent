---
name: latex-quality-control
description: 当 JW 的 LaTeX 或 Beamer 成品需要编译、排错、渲染和逐页视觉检查时使用，覆盖参考文献、交叉引用、字体、溢出、公式、图表与版面问题。
---

# LaTeX 成品质检

负责从 TeX 源文件到已检查 PDF 的完整质检。编译成功只证明构建完成，不能替代对参考文献、公式、图表和页面视觉效果的核验。

## JW 使用边界

只检查读者可见成品与源文件的一致性；不得为消除编译错误而编造引文、数据、数值、图像或作者观点。科学主张是否成立仍由 Evidence 审查与真实实验结果决定。

## Route and preflight

- Use `pdf` when the task only reads, extracts, combines, or annotates an existing PDF.
- Use `scientific-slides` to design a talk; use this Skill once Beamer source must build and render.
- Use `scientific-writing`, `math-authoring`, or `venue-templates` for substantive authoring before final compilation QA.

Resolve the project root and main TeX file. Inspect its build command, engine hints, class, included files, bibliography backend, images, fonts, and output convention. Preserve a working project-native command.

## Build and diagnose

1. Check required executables without installing anything. If one is missing, state its effect and an available fallback; installation needs explicit authorization.
2. Run the documented build, or an engine justified by the source. Chinese or `fontspec` projects commonly need XeLaTeX or LuaLaTeX, but project instructions control.
3. Read the first relevant error context from the log and classify it before editing: missing input, undefined command, package conflict, encoding/font issue, bibliography failure, malformed environment, stale auxiliary state, or layout warning.
4. Apply the smallest repair that preserves intent, then rerun. Do not invent a citation, image, theorem, value, macro meaning, or author content to obtain a PDF.

## Check the built artifact

After a clean build, verify the PDF and inspect the log for unresolved citations or references, repeated labels, substituted fonts, and overfull or underfull boxes. Treat them separately from compiler errors.

Render and inspect every final page. For papers and notes, check margins, headings, theorem blocks, equations, floats, captions, page breaks, and bibliography. For Beamer, also check frame boundaries, block and table overflow, formula scale, plot labels and units, contrast, title wrapping, and consistency with supplied speaker material.

Fix the source and rebuild; do not edit rendered pages as a substitute for a source correction.

## Report evidence

Return:

- command, engine, bibliography backend, and exit status;
- output PDF path and page count;
- files changed and errors repaired;
- unresolved citation, reference, font, or box findings;
- visual findings by page or slide;
- status: passes the requested checks, passes with named limitations, needs revision, or cannot be completed because of an exact blocker.

Keep static inspection, successful compilation, automated log checks, and visual page inspection as separate evidence.

## Common mistakes

- Calling a PDF final because it exists without opening rendered pages.
- Replacing the project's build command before understanding it.
- Hiding warnings that affect readability or references.
- Treating a thumbnail contact sheet as full inspection of dense pages.
