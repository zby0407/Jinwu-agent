# Figure Contract and Regression Gate

Create one JSON contract per formal figure. Record purpose, data source,
editable source, route, panels, visual encodings, and acceptance values. Use
named Visio shapes where geometry will be checked automatically.

## Four ordered gates

1. **Meaning:** verify data rows, units, groups, estimands, intervals, thresholds and captions.
2. **Coordinates:** verify every point, error bar and reference line through the same plot transform.
3. **Layout:** verify fonts, sizes, label centers, panel centers, connector visibility and aspect ratio.
4. **Insertion:** export at 600 dpi and inspect at the paper's actual width.

For an Origin-to-Visio data chart, the layout gate requires measured geometry,
not self-certified booleans. Record the VSDX audit receipt, standalone preview,
paper-page preview, fitted text-box width versus composed text width, panel and
axis-title center errors, axis-title clearance normalized by font em, and the
visible-content-to-page area ratio. Contract-specific tolerances may vary by
figure, but each tolerance and measurement must use the same coordinate system.

A panel geometry record should use the following shape. Numeric lengths may be
in inches or another declared native unit, but `text_width`, `box_width`,
`font_em`, and `gap` must share that unit:

```json
{
  "geometry": {
    "panel_title_center_error": 0.0,
    "axis_title_center_errors": {"x": 0.0, "y": 0.0},
    "text_boxes": [
      {
        "role": "x_axis_title",
        "text_width": 0.50,
        "box_width": 0.64,
        "font_em": 0.14,
        "max_extra_per_side_em": 0.75
      }
    ],
    "axis_title_gaps": [
      {
        "axis": "x",
        "gap": 0.12,
        "font_em": 0.14,
        "minimum_gap_em": 0.5,
        "maximum_gap_em": 1.25
      }
    ]
  }
}
```

Include `panel_title_center_error` when the panel has an embedded title. Empty
measurement lists do not pass the layout gate.

## Reusable regression cases

- a legend does not explain category colors or missing/non-missing states;
- a point hides a narrow confidence interval;
- fold points overlap the mean or uncertainty symbol;
- zero, random, ideal, or threshold lines use the wrong value or an ambiguous label;
- an overlay uses the SVG outer bounding box while plotted data use the inner plot origin;
- raw, calibrated, and ideal curves share indistinguishable visual keys;
- phrases such as “large point” do not identify the actual marker and meaning;
- a visible axis has no quantitative or categorical information;
- a forced three-across layout flattens the panels;
- a title appears centered only because its transparent text box spans the
  whole panel;
- an axis title is positioned from the page edge instead of the outermost tick-
  label bounding box;
- the visible plot occupies a weak fraction of the page despite technically
  non-overlapping objects;
- a suspected threshold error is diagnosed as whole-chart displacement without separately checking bars and the reference line.

## Typography and alignment profile

- Chinese: project-approved Song font; Latin, digits and symbols: Times New Roman.
- Ordinary labels: 9--12 pt, integer or half-point values only.
- Each y-tick label's center Y equals its tick Y within the declared tolerance.
- Each x-tick label's center X equals its tick X within the declared tolerance.
- The x-axis title center X and y-axis title center Y equal the panel center.
- Panel titles center on their panel, not on an unequal plot-plus-colorbar group.
- Each measured short text box contains the composed text plus only its declared
  per-side margin; zero clipping and excessive empty box width both fail.
- Each axis-title gap lies within the contract's declared em-normalized interval.
- The figure's visible-content area ratio meets the declared minimum.

## Flowchart reference route

When asking a web AI for a flowchart reference, provide the verified model
topology, target aspect ratio, typography rules, and selected reference crops.
State that each crop contributes only hierarchy, whitespace, or connector style
and that text, models, and results must not be copied. Save the generated image
in a candidate area. Produce a Visio redraw specification and obtain approval
before any formal sync.
