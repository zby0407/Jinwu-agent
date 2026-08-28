# Manual Redraw Specification

When a figure cannot be produced directly in the target app, create a redraw specification with enough detail for a human teammate to rebuild it exactly.

Default assumption:

- redraw specifications are internal operator materials
- they should not enter judge-facing support materials unless the user explicitly requests that
- they are not default figure logs and should be created only when the direct figure still needs clear human refinement or the user explicitly wants the spec

## Required Sections

### 1. Figure Identity

- figure ID
- figure title
- supported subquestion
- one-sentence message of the figure

### 2. Target Tool

- Origin, Visio, Illustrator, or Photoshop
- reason for that choice

### 3. Source Inputs

- full file path
- sheet name or table name
- exact columns or variables
- filters, grouping, sorting, and derived fields

### 4. Build Instructions

List the build steps in order. Avoid vague verbs like "beautify" or "adjust a bit."

### 5. Visual Specification

State:

- canvas or page size
- palette
- fonts and sizes
- line weights
- marker shapes
- box sizes
- relative or absolute placement
- annotation text

Use the default font contract unless the task clearly overrides it:

- Chinese text: 宋体 or closest controllable Song font
- English text and numerals: Times New Roman or closest controllable Times font

### 6. Export Specification

State:

- output format
- target width or page occupancy
- resolution when raster export is needed
- final filename

## Hard Rule

If a human would still have to guess the data source, the box positions, or the styling after reading the spec, the spec is not complete.

Also record whether the spec is:

- internal-only
- or intentionally included in a broader internal delivery bundle

Do not create a separate diary-style production note when the figure itself, the source asset, and the redraw specification already cover the needed traceability.
