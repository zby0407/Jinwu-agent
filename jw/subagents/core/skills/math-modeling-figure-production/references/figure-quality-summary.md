# Figure Quality Summary for Mathematical Modeling Contest Papers

This note summarizes the default figure-quality standard that now applies across the mathematical-modeling figure workflow.

Use it as a quick orientation file before reading the more detailed route-specific guides.

## The Core Standard

A judged mathematical-modeling paper should not contain figures that merely "work."

The default target is stronger:

- the figure should look like it belongs in an excellent contest paper
- the reading path should be obvious
- the figure should survive paper-width insertion
- the figure should not look like a notebook export, office draft, or internal workflow diagram

## Tool Priority

### Flowcharts and Framework Diagrams

Default route:

- use Visio when a usable `.vsdx` route exists
- edit the native source rather than redrawing in Python
- if a reference paper is supplied, compare the exported figure against the reference side by side

Python may still help for quick exploratory mockups, but it must not be used to masquerade as a successful Visio route.

### Data-Driven Charts

Default route:

- use Origin for statistical comparisons, sensitivity plots, Pareto fronts,
  heatmaps, parameter curves, and solver-result comparisons when the route is
  stable
- export SVG from Origin and compose or relabel in Visio when that materially
  improves paper-width readability
- do not remain with a default Python or MATLAB plot merely because the data was
  computed in that environment

### MATLAB-Native Numerical Figures

Actively consider MATLAB when the figure is fundamentally a MATLAB-native
numerical object, especially:

- simulation and mechanism curves
- surfaces, contour fields, dynamic-system trajectories, and numerical fields
- figures that depend on domain-specific numerical routines already maintained
  in MATLAB

Computing a sensitivity sweep or optimization archive in MATLAB does not by
itself make MATLAB the final chart-authoring tool. Follow a user- or
project-declared Origin-to-Visio route when one exists.

## Native Source Honesty

If a native editable source exists, such as:

- `.vsdx`
- `.fig`
- `.opju`
- `.ai`

then that source is the first editing target by default.

Do not replace a good native route with a script-only redraw for convenience.

## Zero-Tolerance Rule for Overlap

Any visible overlap among semantic elements is a blocking defect.

This includes:

- legend covering title
- legend covering data
- annotation box covering the main plot region
- color bar colliding with labels
- text labels colliding with each other
- arrows or callouts obscuring nearby text
- inset panels or embedded tables crowding the main figure
- dense heatmap numbers colliding with cell borders or neighboring text

The following geometry defects are equally blocking even when no objects touch:

- a short title sits in an unnecessarily long or wide transparent text box
- the visible title is not centered on the inner plotting rectangle
- an axis title is conspicuously distant from its tick labels
- the visible chart occupies too little of its figure page because page fitting
  used outer object bounds rather than the intended content

If a judge would notice the collision immediately, the figure is not acceptable.

## Reference-Paper Replication Rule

When the task is to match or approach a prize-winning paper's figure quality:

1. inspect the reference figure at full size
2. inspect the recreated figure at full size
3. compare them side by side

Do not claim success until that comparison has been made.

Replication fails if the recreated figure is still materially weaker in:

- topology
- connector routing
- text fit
- visible-text centering and axis-title clearance
- line weight
- white-space rhythm
- paper-size readability

## Page-Rhythm Rule

Figure quality is not judged only on the standalone image.

It also depends on how the figure sits in the paper:

- do not strand a low-information figure on a full page
- do not leave an orphan lead-in sentence above a large float
- do not accept abnormal blank areas created by manual spacing hacks
- do not let appendix code blocks or figure boxes dominate a page visually

## What "Good Enough" Means

A figure is not final until:

- the standalone export looks clean
- the inserted paper page looks clean
- the support-material figure matches the paper figure
- the route used was the correct one for the figure type

For contest work, "usable" is not the same as "final."

## Quick Checklist

Before accepting a final figure, confirm:

- correct tool route chosen
- native source used when available
- no overlap or collision
- no unreadable text at insertion width
- no rough connector routing
- no stale mismatch between paper version and support-material version
- side-by-side comparison done when a reference paper exists

If any of the above fails, continue polishing instead of calling the figure complete.
