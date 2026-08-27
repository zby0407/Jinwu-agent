---
name: math-modeling-figure-production
description: Evidence-first, publication-grade figure-production skill for mathematical modeling contests. Use when a contest paper needs data-derived charts, Origin/Visio composition, numerical figures, diagrams, editable native sources, or per-figure judge-facing visual QA beyond a default draft.
---

# Math Modeling Figure Production

Use this skill when figure quality is part of the judged deliverable. Produce
reader-facing evidence, not decorative pixels or an internal production diary.

## Project Delivery Policy and Native Route Gate

First read the resolved project's `delivery_policy`. For formal contest
figures, flowcharts use an editable Visio route and data figures use an
editable Origin route, normally Origin SVG composed and finalized in Visio.
Matplotlib may produce an exploratory numerical draft, but is never an
automatic formal fallback. If Origin or Visio fails, repair the native route or
record a blocking defect; do not silently insert script pixels.

Create one figure contract and one standalone-render plus paper-insertion-page
acceptance receipt for every formal figure. Under the workspace three-role team
policy, `paper_visual_lead` owns the whole formal figure set and locks shared
style, captions, figure numbering, and insertion order. Do not consume the
modeling or computation role slots by automatically assigning one concurrent
subagent per figure. A temporary one-figure specialist is allowed only after a
team slot is idle and the paper/visual lead supplies the shared figure contract;
the paper/visual lead still performs final integration and acceptance.

Do not call a formal figure set complete until `audit_release_candidate` from
`math-modeling-release-guard` passes with the current delivery policy.

## Route and Evidence Gate

Choose a figure route before production and record the figure purpose, data or
model source, editable source, export target, and acceptance checks. Consider
scripted, numerical, vector-diagram, and native-production routes according to
mathematical fit, reproducibility, insertion readability, and expected judged
quality. Python is a reproducible baseline, not an automatic final route.

Before production, create a structured figure contract. Read
`references/figure-contract-and-regressions.md` and validate the contract with
`scripts/validate_figure_contract.py`. Complete the gates in this order:

1. data and statistical meaning;
2. coordinate transforms, reference lines, intervals, and marker semantics;
3. native-layout geometry and typography;
4. 600 dpi export and actual paper insertion.

Later visual polish cannot repair an earlier semantic failure.

Use an available native bridge when it materially improves the artifact. It is
valid only after a real render, edit, export, or inspection receipt is written
to the project validation record. Host-specific application inventories and
staging instructions belong in `runtime-adapters/windows-native-bridge.md`.

If a dependency is missing, identify it, state a safe fallback, and prefer an
existing project-approved environment. Installation, download, credential use,
or privilege change requires explicit user authorization; only then may a
minimal recovery command be prepared.

## Native Source Priority

When a credible editable native source exists, inspect it before recreating a
figure. Do not silently replace a maintained native diagram or numerical figure
with a script-generated imitation. Change routes only when the source is
missing, corrupted, not reproducible, demonstrably weaker for the task, or the
user explicitly requests replacement.

## Figure Taxonomy

- Flowchart, framework, algorithm pipeline, or process topology: use Visio;
  preserve topology, connector semantics, and editable source.
- Numerical curve, surface, contour, simulation, sensitivity, optimization, or
  mechanism figure: prefer the route that can reproduce the numerical result
  and retain its source.
- Statistical chart: use Origin and retain editable chart data; finalize
  paper-facing composition in Visio when layout or relabeling is needed.
- High-volume exploratory chart: a scripted draft is acceptable, but it is not
  automatically a final contest figure.

Separate numerical computation from final chart authoring. For data-driven
charts, statistical comparisons, sensitivity plots, Pareto fronts, heatmaps,
and solver-result comparisons, use Origin first when the route is stable and
compose the exported SVG in Visio when paper-level relabeling or layout is
needed. Use MATLAB first when the visual object is itself a MATLAB-native
simulation, field, surface, mechanism, or numerical routine. Follow an explicit
user or project route in preference to these defaults.

When web image generation is used to propose flowchart layouts, create a
separate complete prompt and upload list for every materially different
flowchart. The generated PNG is an internal composition candidate only; verify
the topology and redraw the accepted design in Visio before delivery.

## Visual Quality Rules

- Every formal figure must support evidence, structure, mechanism, comparison,
  or a decision; otherwise omit it.
- Use restrained contest-paper styling: clear before decorative, low color
  count, readable labels, and minimal in-image prose.
- Keep local paths, script names, TODOs, production notes, and internal
  workflow language out of judge-facing pixels.
- Treat the paper caption as the primary title. Avoid redundant large image
  titles, long subtitles, or paragraphs inside the figure.
- Do not add triangles, squares, or multiple marker shapes by default. Use shape
  only when it encodes a real variable, supports monochrome printing, or is
  needed for color-vision accessibility.
- Show major tick labels when they improve quantitative reading; do not hide
  them merely for visual minimalism. Align panel labels, axis labels, legends,
  and embedded titles deliberately rather than accepting application defaults.
- Choose a restrained, color-vision-safe palette informed by the discipline's
  current journal style. Keep category colors stable across related figures.
- Preserve data, code, or native sources needed to reproduce figures actually
  used by the paper; keep redraw instructions internal by default.
- For a Chinese contest figure finished in Visio, use the project-approved Song
  font for Chinese and Times New Roman for Latin letters, digits, and symbols.
  Use 9--12 pt in 0.5 pt increments unless the venue specifies otherwise.
- For Origin-to-Visio data charts, treat Visio as the final style owner. As a
  starting hierarchy at final paper size, use about 0.75--1.0 pt for primary
  data lines, 0.5--0.75 pt for axes or secondary lines, and 0.25--0.5 pt for
  grids. Adjust from the rendered insertion, not from nominal Origin values.
- When Visio owns final composition, redraw or deliberately normalize final
  tick labels there instead of trusting Origin's imported defaults. Center
  y-tick text vertically on its tick, x-tick text horizontally on its tick,
  and center axis and panel titles on the corresponding panel rather than the
  full imported group.
- Size every title and short-label text box to its visible text plus a small,
  declared margin before aligning it. Do not use a panel-wide, axis-wide, or
  otherwise oversized text box to simulate centering: an aligned box is not
  proof that the text itself is visually centered. For named Visio titles,
  prefer a content-fit formula such as `TEXTWIDTH(TheText)` and retain a
  measurable margin.
- Keep tick labels outside the plotting region and leave visible clearance
  between tick labels and axis titles. Place an axis title from the union of
  the plot and tick-label bounding boxes, then measure the visible gap in font-
  em units. Any intentional in-panel label must be identified in the figure
  contract and checked against the data marks.
- Center panel and axis titles against the inner plotting rectangle, excluding
  a legend or color bar unless the figure contract explicitly makes it part of
  the centering target. Inspect the visible-content bounding box against the
  page as well as the individual object coordinates; excessive unused page
  area is a layout defect, not harmless whitespace.
- Do not force a fixed subplot count. Reject a three-across layout when it makes
  panels shallow or unreadable; use separate figures or another meaningful
  layout rather than inventing a filler fourth panel.

## Required QA

Before accepting a formal figure, verify both:

1. the standalone rendered export;
2. the image at its actual paper insertion width.

Under the three-role team contract, the acceptance receipt records three
distinct signoffs: `computation_lead` confirms the plotted data and units,
`modeling_lead` confirms the mathematical or decision meaning, and
`paper_visual_lead` confirms layout, caption, legend, typography, and insertion
readability. These are role checks on one figure, not three independent redraws.

Blocking defects include overlap, clipping, unreadable labels, crowded legends,
oversized title boxes, visibly off-center text, excessive axis-title clearance,
weak page utilization, inconsistent font hierarchy, misleading scale choices,
duplicated titles, or a mismatch between the embedded paper image and the
support-material figure.

Inspect every formal figure, not a sample. A contact sheet can reveal family-
level inconsistency but does not replace opening each figure and each paper page
at useful scale. Panel titles must align consistently and be centered over the
panel when that is the intended grammar.

If a reference figure is being matched, inspect both at useful scale and
compare topology, spacing, text fit, line weight, visual hierarchy, and
insertion readability before claiming success.

For VSDX figures, use `scripts/audit_visio_style.py` to inventory character-run
fonts, line weights, and text-box geometry. Supply a project-specific
`--max-line-pt` when the figure contract defines one. Name geometrically
important shapes, such as `PanelTitle_*`, `XAxisTitle_*`, and `YAxisTitle_*`,
then pass matching `--require-content-fit-pattern` expressions. The script is
structural evidence only: rendered inspection still controls visual centering,
line hierarchy, axis spacing, and insertion quality.

For a web-AI-generated flowchart reference, treat the image as a candidate
layout only. Learn hierarchy, whitespace, palette, and connector routing; do
not insert it into the formal paper. Rebuild the approved topology in Visio
with editable native shapes, then wait for explicit formal-sync authorization.

## Read Order

Always read:

- `references/tool-routing.md`
- `references/windows-gui-routing.md`
- `references/manual-redraw-spec.md`
- `references/visual-style-defaults.md`
- `references/figure-quality-summary.md`
- `references/figure-contract-and-regressions.md`

Read data-chart, numerical-figure, diagram, or final-polish playbooks only
when their figure class applies.

When Origin output is composed or relabeled in Visio, always read
`references/origin-visio-publication-workflow.md` before editing or exporting.

## Delivery Boundary

Allow judge-facing code from `modeling`, `validation`, and `result_generation`:
models and solvers, substantive numerical checks, and code that writes judged
result tables or required output files. Keep `figure_generation`,
`paper_production`, and `release` scripts internal, including drawing,
beautification, layout, export, contact-sheet, document-build, and package-copy
automation. Split mixed solve-and-plot scripts at the numerical result-data
interface.

Allow final PNG, VSDX, OPJU, FIG, and AI artifacts when they are current paper
figures or meaningful editable sources. Keep the scripts that generate, style,
compose, export, or audit them in the internal production layer.

Keep the final figure, its needed source, and a concise reproducibility note
traceable. Keep screenshots, failed exports, route diagnostics, and redraw
specifications in internal records unless the contest explicitly requests a
full internal archive.

When the project declares its submission tree as the active deliverable source,
keep the only final PNG and editable `.vsdx`, `.opju`, `.fig`, or `.ai` source
in the judge-facing figure folder. Do not maintain a
second editable figure-working copy elsewhere in the project.

"Only final" means one authoritative current version, not deletion of version
history, recoverable backups, source data, or analytical preparation code. Keep
those reproducibility assets in their project-defined internal locations while
preventing a second directory from competing as the current figure source.

For an Origin-to-Visio chart, the judge-facing final set normally contains the
600 dpi PNG, the final VSDX, and the OPJU analysis project. Export SVG from
Origin as the vector bridge into Visio; do not rasterize in Origin and then
place that PNG into Visio. Recalibrate font size and line weight in Visio,
because identical point values in Origin and Visio do not guarantee identical
visual weight after import.

Formal figure delivery uses a 600 dpi PNG plus the corresponding editable
source. SVG may be retained as an intermediate vector bridge, for example from
Origin into Visio, but it does not replace the PNG or editable source. Do not
generate or package a standalone PDF for an individual figure.
