# Origin Chart Playbook

Use this guide for contest-paper charts that should be rebuilt or polished in Origin.

When Origin is the primary route, try to leave behind the `.opju` project in addition to the final exported figure.

## What to Record

Always specify:

- source file path
- workbook or sheet name
- column mapping such as X, Y, Y error, group, label
- preprocessing steps
- chart type
- axis range and tick strategy
- legend labels
- color and marker mapping
- final project filename and export filename

## Typical Instruction Pattern

Describe the build in this order:

1. Open the exact file.
2. Import the exact sheet or range.
3. Map columns to X and Y roles.
4. Apply the specific chart type.
5. Set axis range, tick interval, and scale.
6. Set line colors, marker shapes, bar fills, or heatmap palette.
7. Add labels, callouts, and threshold lines.
8. Save the project file.
9. If Visio will compose or relabel the figure, export a clean SVG bridge.
10. Export the required final format only after the final authoring application
    has completed typography, line-weight, and alignment checks.

## Detail Level Expected

For a data chart, the redraw spec should say things like:

- use `E:\...\results.xlsx`, sheet `model_compare`
- use column A as model name, B as RMSE, C as MAE
- plot a grouped bar chart with two bars per model
- set y-axis from 0 to 1.2 with major ticks every 0.2
- color RMSE bars dark blue and MAE bars orange
- place the legend at the upper right
- export to PNG at 600 dpi; retain SVG only when it is needed as the vector bridge into Visio, and do not generate a standalone figure PDF

Do not import an Origin PNG into Visio for final composition when an SVG bridge
is available. After SVG import, inspect every stroke, tick, label, and symbol in
Visio: Origin `0.5 pt` and `12 pt` settings may look materially heavier or
larger after transfer even when Visio reports the same nominal values.

Do not stop at "draw a bar chart in Origin."
