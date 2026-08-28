# MATLAB Numerical Figure Checklist for Contest Papers

Use this checklist when a contest-paper figure is primarily numerical and MATLAB is a natural route.

This checklist exists because many judged-paper defects are not about the underlying result but about the final chart still looking like a quick analysis plot. MATLAB can produce strong contest figures, but only if the final chart is inspected and cleaned intentionally.

## When to Use MATLAB by Default

Prefer MATLAB when the figure is:

- a sensitivity sweep
- a Pareto front
- a solver-result comparison chart
- a heatmap with numerical meaning
- a mechanism curve or surface
- a scenario-comparison chart derived from optimization or simulation
- a contour/surface/trajectory plot

Do not remain with Python by inertia when MATLAB can more naturally express the figure and the MATLAB route is available.

## Required Deliverables

Whenever MATLAB is the primary route, leave behind:

- `.m` script or function used to generate the figure
- native `.fig` when feasible
- final exported paper image, preferably high-resolution PNG
- vector export when practical

## Build the Figure at Paper Size

Do not treat the default MATLAB figure window as the final layout.

Before exporting:

- set a paper-ready figure size
- choose readable font sizes for the final insertion width
- normalize line width, marker size, and axis ticks
- choose legend placement deliberately

## Typography and Readability

For judged-paper figures:

- Chinese text should use the intended Chinese body family where controllable
- English text and numerals should use the intended Times-family fallback where controllable
- ordinary labels should usually stay in the 10-12 pt range at final insertion size

If text only fits because it is too small, enlarge the figure or simplify the chart instead of forcing tiny labels.

## Overlap Is Never Acceptable

Any visible overlap among semantic elements is a blocking defect.

This includes:

- legend covering title
- legend covering lines or bars in a way that hurts reading
- annotation box covering data
- color bar colliding with labels or title
- data labels colliding with each other
- callout arrows obscuring tick labels or nearby text
- panel titles colliding with neighboring subplots
- heatmap numbers colliding with cell edges or each other

If any of the above happens, the figure is not final.

## Heatmap-Specific Checks

For heatmaps:

- verify that cell values remain readable at paper insertion width
- ensure color bars do not dominate the panel
- keep the number of panels modest if each cell contains numbers
- if one panel is much less important, move it to support materials rather than shrinking all panels together

## Multi-Panel Figure Checks

For multi-panel MATLAB figures:

- align panel widths and heights
- keep panel spacing consistent
- ensure one panel does not visually collapse relative to the others
- remove redundant legends when one shared legend is enough
- verify that subplot titles and axis labels do not collide

## Final QA Before Claiming Success

Before treating a MATLAB figure as judged-paper ready:

1. inspect the standalone exported figure
2. inspect the figure at actual paper insertion width
3. confirm no clipping, overlap, crowding, or weak default styling remains

If a figure still looks like a quick notebook chart or an exploratory analysis plot, it is not final.
