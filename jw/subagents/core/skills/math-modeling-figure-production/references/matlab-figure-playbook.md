# MATLAB Figure Playbook

Use this guide when a contest figure is fundamentally numerical, simulation-driven, or mechanism-heavy and MATLAB is the natural production route.

## Good Fits for MATLAB

- dynamic trajectories
- phase portraits
- contour maps
- surfaces and meshes
- field plots
- mechanism curves built from numerical solvers
- figures driven by existing MATLAB models or scripts
- sensitivity sweeps
- Pareto-front comparisons
- heatmaps with numerical comparison meaning
- scenario-comparison charts tied directly to solver output

When a contest chart is fundamentally numerical and judge-facing, MATLAB should be considered a preferred route rather than an optional extra. Do not stay with default Python output just because the numerical data already exists in CSV form.

## What to Leave Behind

Whenever MATLAB is the primary route, try to leave behind:

- the `.m` script or function that generated the figure
- the native `.fig` file when feasible
- the final exported figure used in the paper

## What to Record

Always specify:

- source data path or simulation entry point
- parameter set or scenario name
- figure type
- axis ranges and viewing angle when relevant
- line widths, marker settings, and colormap
- font setup
- export format and size

## Typical Instruction Pattern

1. Load the exact data or compute the exact simulation result.
2. Build the figure using a paper-ready size, not the default window.
3. Set Chinese labels to SimSun if controllable and English labels to Times New Roman.
4. Normalize line widths, markers, legends, and axis ticks.
5. Save the native `.fig` when feasible.
6. Export a judge-facing 600 dpi PNG; retain `.fig`/`.m` as editable source and do not create a standalone figure PDF.

## Hard Rule

Do not treat MATLAB as successful merely because the executable opens.

It counts as stable only after it produces a real figure file in-session.

It also does not count as a successful MATLAB route if the resulting chart still contains obvious layout defects such as:

- legends covering the title or data
- labels colliding with axes or panel edges
- crowded annotation blocks
- low-information default styling that still looks notebook-like
