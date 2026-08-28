# Tool Routing

Choose the tool based on what the figure fundamentally is.

Before routing, inspect only the capabilities exposed by the current runtime or
explicitly supplied by the user. Do not infer host application availability
from disk locations, drive letters, or desktop shortcuts.

Availability alone is not enough. Promote an app to the primary route only after a minimal real action succeeds in-session.

## Use Origin First

Choose Origin when the figure is mainly a data chart:

- line, scatter, bar, box, violin, radar, heatmap, contour, surface
- parameter sweeps
- sensitivity plots
- robustness comparisons
- prediction versus observation plots

Origin is the default when the judge cares about polished axes, legends, symbol styles, and chart readability, and the route is stable.

The route is strongest when you can leave behind:

- the final exported figure
- the `.opju` project
- the source table or script that fed the chart

When the local `zbot-origin` MCP is exposed, first call `detect_origin`, inspect
the OPJU, and export the verified graph as SVG through that bounded route.

## Use MATLAB First

Choose MATLAB when the figure is mainly a numerically generated or simulation-heavy figure:

- differential-equation trajectories
- phase portraits
- mechanism curves
- 3D surfaces
- contour fields
- algorithm or simulation animations reduced to key frames
- plots that rely on domain-specific numerical routines already written in MATLAB

MATLAB is the default when numerical generation is the main job and the session can reliably leave behind a `.fig` or equivalent native source file.

## Use Visio First

Choose Visio when the figure is mainly a logic or structure diagram:

- workflow chart
- system architecture
- model framework
- algorithm pipeline
- decision tree
- mechanism block diagram

Visio is the default when the figure needs clean boxes, connectors, alignment, and page-level structure, and the route is stable.

When the local `zbot-visio` MCP is exposed, use it to inspect VSDX structure,
import the Origin SVG with Visio-unit normalization, apply bounded named-shape
corrections, and export the final page at explicit 600 dpi.

## Use Illustrator or Photoshop After the Core Figure Exists

Choose Illustrator when the work is mainly:

- vector cleanup
- multi-panel composition
- annotation cleanup
- consistent typography
- final composition for paper-ready export

Choose Photoshop when the work is mainly:

- raster cleanup
- bitmap annotation
- background cleanup
- compositing or export repair on already rasterized assets

Do not use Adobe as a vague label. State which app is being used and why.
If the user or project explicitly assigns multi-panel composition to Visio,
that route takes precedence over the generic Illustrator preference. In that
case, use the Origin-to-SVG-to-Visio workflow and reserve Illustrator for a
specific cleanup need that Visio cannot meet.

## Keep Python in the Loop, But Not as the Final Excuse

Use Python when:

- a fast baseline is needed
- reproducibility matters
- the app route cannot be driven directly in-session

When Python is the execution route, still produce an internal redraw specification if the final paper would benefit from Origin, MATLAB, Visio, or Illustrator quality.

## Font Default

Unless the user or venue requires otherwise:

- Chinese labels: SimSun or closest controllable Song-family font
- English labels and numerals: Times New Roman or closest controllable Times-family font

## Aesthetic Default

Unless the task clearly demands something else:

- prefer restrained color palettes over bright default notebook colors
- keep generous whitespace around titles, labels, and panels
- keep line weights and marker sizes consistent within one figure family
- avoid crowding multiple messages into one figure
- favor clean, contest-paper-ready visuals over flashy dashboard styling
