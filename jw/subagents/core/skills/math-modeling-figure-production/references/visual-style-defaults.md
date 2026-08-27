# Visual Style Defaults

Use these defaults unless the user, contest, or venue clearly needs something else.

## General Style

- Prefer restrained academic styling over bright dashboard colors.
- Assume award-paper visual restraint by default, not only when the user cites excellent papers.
- Leave controlled whitespace around panels, legends, and callouts. Whitespace
  should separate semantic elements; it must not arise from oversized invisible
  text boxes, distant axis titles, or a poorly fitted page.
- Keep one figure focused on one primary message.
- Avoid cramming too many series, labels, or callouts into one panel.
- For line art, diagrams, and proof schematics, retain SVG when a vector bridge is useful and export the final PNG at 600 dpi. Do not generate a standalone PDF for an individual figure.

## Typography

- Chinese: SimSun or closest controllable Song-family font
- English and numerals: Times New Roman or closest controllable Times-family font
- Ordinary formal-paper figure text should usually stay in the 10-12 pt range, and should not exceed 12 pt unless the figure has a specific poster or presentation purpose.
- Keep font sizes consistent inside one figure family
- Do not mix multiple unrelated fonts in the same figure
- Fit short title and label boxes to the composed glyphs plus a small margin
  before centering them. Box width must follow the text; it must not inherit the
  panel or axis width merely to make alignment easier.

For figures intended for a Chinese Word contest paper, check the rendered figure at the actual insertion width. A full-size export can look acceptable while its labels become cramped or misaligned once inserted into the paper.

## Lines and Shapes

- Use consistent line widths inside one figure
- For data charts finalized in Visio, start near 0.75--1.0 pt for primary data
  lines, 0.5--0.75 pt for axes or secondary lines, and 0.25--0.5 pt for grids;
  accept the final hierarchy only after inspection at paper insertion size.
- Keep connector arrows clean and avoid sharp visual clutter
- For formal flowcharts, prefer dynamic right-angle connectors with stable connection points. Use straight connectors only for adjacent same-row or same-column steps where the logic is unambiguous.
- Route decision branches and loopbacks around the outside of the main path when possible, and use line jumps or manual rerouting when crossings are unavoidable.
- Prefer rounded rectangles or clean rectangles for logic diagrams instead of mixed random shapes
- Use subtle stroke colors such as dark gray rather than pure black when the app allows it

## Contextual Scene Images

- Use a scene image only when it helps the reader understand a real application,
  physical setting, or system context; do not add a generic decorative photo.
- Follow the selected excellent-paper benchmark for wrapped, side-by-side, or
  full-width placement. For a right-side image, keep the adjacent paragraph
  readable and prevent the caption from becoming narrower than its text.
- Use a high-resolution source, crop to the relevant subject, and inspect the
  image at its actual paper size. Record provenance internally even when the
  visible caption does not emphasize the source.
- When the scene image becomes Figure 1, renumber later figures and verify every
  cross-reference after compilation.

## Proof and Method Schematics

- Use schematics to clarify an implication, construction, containment relation, projection, boundary argument, or algorithmic shortcut.
- Label only the variables, points, regions, and arrows needed for the proof or method.
- Keep the claim and proof text in the paper body; the schematic should not contain a paragraph of explanation.
- Prefer black/gray line art unless color distinguishes mathematical sets or categories.
- Check that all symbols in the figure match the notation table and surrounding equations.

## Color

- Prefer 2 to 5 coordinated colors, not rainbow defaults
- Use high-contrast text on filled boxes
- Keep category colors stable across related figures
- Avoid neon or overly saturated default palettes unless the data genuinely needs it

## Panel Composition

- Align panels to a clear grid
- Keep equal gutters between panels
- Standardize caption anchors, legends, and annotation positions
- Center titles on the inner plotting rectangle. Position axis titles after the
  outermost tick-label box, using a restrained gap judged in units of the final
  font size rather than an arbitrary page distance.
- If one panel is much more important, make that hierarchy visually obvious
- If a title is embedded inside the figure rather than handled by the paper caption, center it and keep it short. Avoid long subtitles, method explanations, data-path notes, or source-file notes inside the image.
- Split multi-panel figures when the panels ask the reader to perform unrelated tasks or when one panel forces the other below readable size.

## Judge-Facing Text Boundary

- Never expose local paths, script names, package-sync notes, preview notes, TODOs, or internal collaboration wording in visible figure pixels.
- Prefer human-facing terms that match the paper body. If the body uses an academic term such as "最优匹配模型", do not leave a figure label as a rough working term such as "入选模型".
- Put data-source detail, method explanation, and long definitions in the paper text, caption, table note, or support-material index unless the figure must stand alone outside the paper.
- Avoid engineering-architecture wording inside figures by default. Prefer `建模思路`, `求解流程`, `比较结果`, `参数影响`, `可行性检验` over `数据层`, `输出层`, `技术路线`, `压力测试`, or `价值闭环`.

## Figure QA Mindset

After rendering, ask:

- Does the figure still look like a draft?
- Is the main message obvious in 3 seconds?
- Are there any collisions, crowding, or awkward blank zones?
- Does it look like it belongs in a contest paper instead of a notebook?
- Does it still match the exact version inserted in the current paper?
