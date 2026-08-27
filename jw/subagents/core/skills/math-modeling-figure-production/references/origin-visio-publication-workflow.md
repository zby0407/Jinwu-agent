# Origin-to-Visio Publication Workflow

Use this workflow when Origin owns a statistical or numerical chart and Visio
owns the final multi-panel composition, Chinese annotation, or page-ready
layout.

## Route Contract

1. Prepare verified data and record column roles, units, filters, and ordering.
2. Build the analytical chart in Origin and save the OPJU with editable
   worksheets and graph layers.
3. Export SVG from Origin. Do not export PNG for placement into Visio when SVG
   is available.
4. Import SVG into Visio, preserve vector objects, and perform final composition.
5. Save the final VSDX.
6. Export the final PNG at 600 dpi.
7. Inspect the PNG alone and at the actual paper insertion width.

If `zbot-origin` and `zbot-visio` are available, use their inspect/export/import
tools for this route. The Visio import must normalize the imported SVG's text
and line units, then be reinspected; page fitting alone is not sufficient.

SVG is an internal exchange file. The normal formal set is PNG + VSDX + OPJU;
do not package SVG unless the project explicitly requires the bridge artifact.
Do not generate an individual-figure PDF.

## Final-App Typography Rule

The application that performs final composition owns the final typography.
Do not assume Origin's nominal `12 pt` text or `0.5 pt` line will retain the same
visual weight after SVG import into Visio.

In Visio, inspect and, when necessary, reset:

- Chinese text to SimSun or the project-approved Song font
- English text and numerals to Times New Roman or the approved Times font
- ordinary labels to 10--12 pt at the final paper size
- axis and frame strokes to a restrained, consistent weight
- marker size, legend samples, grid lines, and reference lines
- text block margins, line spacing, vertical alignment, and horizontal alignment

At the final paper size, use approximately 0.75--1.0 pt for primary data lines,
0.5--0.75 pt for axes or secondary lines, and 0.25--0.5 pt for grids as starting
ranges. These are not a substitute for inspection: imported SVG scaling may
change visual weight, so the Visio render and paper insertion decide acceptance.

Verify mixed Chinese/English runs, not only a document-level font list. A file
containing both font names does not prove every text run uses the correct font.

## Axis and Label Rules

- Show the main tick labels needed to read values; remove only redundant minor
  labels or clutter.
- Keep axis titles near their axes without colliding with tick labels.
- Keep ordinary tick labels outside the plotting region. Leave a visible gap
  from the axis, then place the axis title beyond the outermost tick-label box;
  do not accept labels that sit on the axis or intrude into data marks.
- Use zero lines, thresholds, and reference lines only when analytically useful.
- Center a panel title over its own plotting region; do not center it over an
  unequal group of plot plus color bar unless that is intentional.
- Align repeated panel titles, axes, baselines, and gutters to one grid.
- Use left alignment for explanatory text blocks and centered alignment for
  short panel titles when that improves the reading grammar.

## Geometry Ownership and Adjustment Order

Visio owns the geometry of the final composed figure. Perform these operations
in order; changing the order commonly produces a nominally centered but visibly
misaligned result:

1. normalize the final mixed-script font and point size;
2. fit each short title or label box to the composed text, preferably with a
   `TEXTWIDTH(TheText)` or `TEXTHEIGHT(TheText)` ShapeSheet formula;
3. add only the small margin required by the final font;
4. center the fitted box on the inner plotting rectangle, not on an imported
   outer SVG group containing unequal legends or color bars;
5. place each axis title beyond the outermost tick-label bounding box and record
   the visible gap normalized by the title font's em size;
6. inspect the union of visible content against the Visio page and then inspect
   the exported image at paper insertion size.

Do not use a long, transparent text box as an alignment device. Its shape center
may be correct while its visible glyphs are displaced, especially after mixed
Chinese and western runs recompose in Visio. Name objects that need repeatable
checks, for example `PanelTitle_1`, `XAxisTitle_1`, and `YAxisTitle_1`.

## Marker and Color Rules

Do not cycle automatically through circles, triangles, squares, and diamonds.
Use marker shape only when it carries information, remains necessary in
grayscale, or supplements color for accessibility. Otherwise prefer a single
restrained marker family or line-only encoding.

Choose 2--5 coordinated colors from a color-vision-safe academic palette. Use
one stable color for the same task, model, class, or group across figures. Avoid
rainbow palettes, high saturation, and decorative gradients. When a current
top-journal reference is used, learn its hierarchy and restraint rather than
copying its exact palette without regard to the data semantics.

## Per-Figure Inspection

For every formal figure, inspect all of the following:

1. Origin graph layer at useful scale;
2. SVG after import into Visio;
3. final VSDX composition;
4. final 600 dpi PNG;
5. the page containing the figure at its actual insertion width.

Run `scripts/audit_visio_style.py <figure.vsdx>` before the visual pass. When a
figure contract specifies a maximum final line weight, pass it with
`--max-line-pt`. For named titles, also pass repeatable patterns such as
`--require-content-fit-pattern '^PanelTitle_'`. Treat its font-run, line-weight,
and text-geometry inventory as evidence, not as a replacement for opening the
rendered figure.

Check:

- panel titles centered and aligned
- short title boxes fitted to their visible text rather than stretched across
  a panel
- axes placed on the correct side and not displaced
- axis-title gaps controlled relative to the final font size
- no text/data/legend/color-bar overlap
- major tick labels present where useful
- no clipped labels or uneven whitespace
- visible content uses the intended page area without a large accidental void
- correct Chinese and western font runs
- 10--12 pt effective label size
- consistent line and marker weight
- palette semantics and cross-figure consistency
- caption and in-image terminology matching the paper

Generate a contact sheet to compare the figure family, but still open every
figure individually. Do not hand off a figure set that has only been batch
exported or spot-checked.

## Source Mapping Gate

Maintain a one-to-one mapping from every paper figure number to:

- final PNG
- final editable source
- analytical source when different from the final layout source
- source data or preparation code when needed to reproduce the analysis

If Python is only preparing data, keep it in the code layer. If Python produces
the final chart itself, put the directly corresponding `.py` beside the PNG
according to the project convention. Do not duplicate the same script in both
the code and figure folders.
