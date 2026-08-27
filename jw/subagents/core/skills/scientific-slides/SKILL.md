---
name: scientific-slides
description: Use when planning, creating, revising, or reviewing the content or narrative of a scientific, mathematical, or technical talk, including source-grounded slides, formulas, figures, speaker notes, or rendered-deck critique; not for layout-only PPTX edits or LaTeX/Beamer build repair.
allowed-tools: Read Write Edit Bash
license: MIT license
metadata:
    skill-author: K-Dense Inc.
---

# Scientific Slides

Build a talk from verified research content. Make the question, method, evidence, uncertainty, and conclusion understandable at presentation pace while preserving editable source material.

## Select the mode

- **Plan:** define audience, purpose, duration, format, narrative, and evidence needs.
- **Create:** turn approved sources into slide content, visuals, and notes.
- **Revise:** preserve requested content or structure, then change only the named scope.
- **Review:** inspect rendered slides and give specific, slide-numbered corrections without editing unless asked.

## Route the artifact

| Need | Supporting Skill |
|---|---|
| Read, create, edit, or export an actual `.pptx` | `pptx` |
| User explicitly requests the high-customization SVG-to-native-PPTX production workflow | `ppt-master` |
| Compile, debug, render, or inspect Beamer/LaTeX | `latex-quality-control` |
| Create data plots or publication-grade scientific figures | `scientific-visualization` or project analysis code |
| Review final audience-visible wording | `writing-reader-facing-content` |

Default to editable PPTX or Beamer source plus rendered output. Use generated full-slide images only when explicitly requested.

## Build from evidence

1. Establish audience, purpose, duration, language, format, and required template. Infer minor preferences when safe.
2. Read the sources needed for the talk. Map claims, theorem conditions, values, uncertainty, figures, and citations before drafting.
3. Design an appropriate narrative around the question, necessary background, method, evidence, interpretation, limitations, and conclusion; adapt this order to the talk.
4. Give each content slide one main message and prefer a title that states the result or question.
5. Use a figure, equation, table, diagram, or text composition when it carries evidence or explanation. Decoration is not a requirement.
6. Preserve values, units, sample sizes, uncertainty, assumptions, and qualifiers. Never redraw observed data with a generative model. Generated imagery is optional conceptual illustration and must be labelled.
7. Maintain slide IDs, sources, speaking time, transitions, and notes. Notes must not hide facts needed to understand the slide.

## Rendered review

Do not make visual claims from an outline, extracted text, or unrendered source. Inspect every slide and reveal state for source fidelity, story, formulas, figures, axes and units, hierarchy, contrast, overflow, alignment, citations, timing, and notes.

For a formal talk, report the verdict, blocking issues, slide-specific corrections, audience questions, and priorities. For a discussion deck, emphasize known versus unknown, evidence versus interpretation, and the next decision. Open high-stakes decks in PowerPoint/WPS or an equivalent native renderer when available.

## Common mistakes

- Shrinking a report onto slides instead of designing a talk.
- Treating every slide as an image, bullet list, or identical card grid.
- Replacing editable charts or formulas with generated pixels.
- Calling the deck finished after source checks without rendered inspection.
