# Visio Diagram Playbook

Use this guide for flowcharts, frameworks, logic maps, and algorithm routes.

Pair this guide with `visio-execution-checklist.md` whenever the task is to actually produce or replicate a judged-paper flowchart in Visio.

Default assumption: a contest-paper flowchart should look like a clean modeling-process figure, not like a software architecture slide or project-management chart.

Visio should not be chosen merely because it exists. It is the right route only when the resulting diagram has cleaner topology, connector routing, spacing, and text hierarchy than a script-generated substitute.

For formal contest-paper flowcharts, if a usable Visio route exists, treat Visio as the default primary tool rather than a nice-to-have option. Do not quietly replace that route with Python while still implying that the Visio path has been honored.

## What to Record

Always specify:

- page size and orientation
- grid or alignment logic
- process topology before coordinates: main path, decision branches, loopbacks, exits, and validation paths
- object list
- shape type for each object
- fill color and outline color
- box width and height
- object placement
- connector style, direction, branch labels, and loopback routes
- text content and font rules

## Typical Instruction Pattern

Describe the build in this order:

1. Open a blank Visio flowchart page.
2. Set the page size and orientation.
3. Define the flow topology: main line, branch decisions, loopbacks, and final exits.
4. Place the start/end, process, decision, and output nodes according to that topology.
5. State approximate coordinates or relative alignment.
6. Add connectors with arrow direction, right-angle routing, branch labels, and loopbacks.
7. Apply palette, outline, and font rules.
8. Inspect a rendered preview for routing and alignment defects.
9. Export to SVG or high-resolution PNG.

## Algorithm Flowchart Quality Bar

Contest-paper flowcharts should look like algorithm logic diagrams, not like loose boxes connected after the fact.

- Start and end: rounded terminal shapes.
- Ordinary computation or model steps: rectangles.
- Decisions and convergence tests: diamonds.
- Data input/output: parallelograms only when that distinction helps; otherwise use simple rectangles consistently.
- Main process direction: left-to-right or top-to-bottom, but do not mix both casually.
- Branches: keep "yes/no" or equivalent labels close to the branch line they describe.
- Loopbacks: route them around the outside of the figure, not through the middle of unrelated nodes.
- Captions: keep the formal figure title in the paper caption. Do not put a large title or explanation paragraph inside the diagram unless it must stand alone.
- Prefer simple top-to-bottom or left-to-right logic over highly branched engineering-style hierarchies when the same modeling meaning can be conveyed more cleanly.
- If the reference quality target is a prize-winning mathematical-modeling paper, the flowchart should usually explain one question's solving route cleanly enough that it could sit beside pseudocode without redundancy.
- A low-information flowchart that merely repeats section titles is not acceptable even if neatly aligned.

Prefer a restrained black/gray line-art style for algorithm flowcharts unless color encodes real categories. A clean monochrome algorithm diagram usually looks more like a contest paper than a decorative presentation graphic.

For national-prize-target papers, treat a flowchart as a model explanation figure rather than a decorative workflow graphic. It should normally show one clear logic:

- decision variables, constraints, solver, and verification loop for optimization papers
- data, state variables, mechanism, simulation, and validation loop for mechanism papers
- input features, estimation model, evaluation, and decision rule for data-decision papers

If the diagram only lists what the paper or code does, redraw it as a model framework, algorithmic closure, or evidence mechanism.

Avoid diagram wording such as:

- 数据层
- 模型层
- 输出层
- 技术路线
- 压力测试

Prefer wording such as:

- 赛题数据
- 数据预处理
- 建模思路
- 求解流程
- 方案比较
- 系统影响分析

## Quality Target from Strong Contest Papers

When a user provides a prize-winning reference paper, treat its flowchart quality as a real benchmark rather than a vague style suggestion.

At minimum, a final contest flowchart should satisfy all of the following:

- connector routing looks intentional rather than merely connected
- the main path is obvious within one glance
- branch and loop logic can be followed without tracing clutter
- node text is short and judged-paper-like, not software-architecture-like
- the chart can be inserted at paper size without the text becoming unreadable
- the figure explains solving logic, not just section order

If the figure still looks like a Visio draft, an office workflow chart, or a script-export placeholder, it does not meet the benchmark even if it is technically clean.

## Connector Rules

For flowcharts, use Visio Dynamic Connector with right-angle routing by default.

- Do not use plain line shapes for formal flowchart connectors.
- Do not use straight connectors by default when the logic includes decisions, loops, returns, or cross-row movement.
- Attach connectors to connection points on the target shapes, not to arbitrary visual positions.
- Use consistent arrowheads and line weights across the whole diagram.
- Enable line jumps or manually reroute when connectors cross.
- Keep connector labels horizontal when possible and visually tied to the corresponding branch.
- A connector may bend, but it should not look accidental: bends should align to the grid and preserve clear gutters.

Use straight connectors only for short, unambiguous adjacent steps on the same row or column.

If connectors still feel accidental, cramped, or office-draft-like after a first pass, redraw the topology instead of merely nudging boxes.

## Layout Rules

- Build the diagram from a small number of aligned rows or columns.
- Keep repeated process boxes the same size unless text length genuinely requires a different width.
- Keep decision diamonds large enough for centered text, and avoid squeezing multi-line decisions into tiny diamonds.
- Place final outputs at clear exits, not in a leftover corner.
- Keep whitespace around loopbacks so the reader can follow the return path without tracing through clutter.
- If a flow cannot fit cleanly on portrait A4, split it into stages or convert it into a compact method pipeline plus a separate detailed flowchart.
- Keep the number of visual layers small. Use two to four stage bands when they clarify the model; avoid excessive nested frames, decorative shadows, gradient fills, or unrelated colors.
- Leave enough side gutters for loopbacks and branch labels; if the loopback has no visual channel, the topology is not ready.
- If a diagram is inserted at half-page width, limit node count aggressively or split it; a readable full-page source does not guarantee a readable paper figure.

## Detail Level Expected

For a framework diagram, the redraw spec should say things like:

- use A4 portrait, 210 mm x 297 mm, when the diagram is intended for a portrait A4 paper page; use landscape only when the paper layout or diagram structure clearly needs it
- place a centered title box 3 cm from the top
- create three rounded rectangles in the first row, each 4.5 cm by 1.5 cm, evenly spaced
- use light blue fill for input boxes, light green for model boxes, light orange for output boxes
- use 1 pt dark gray outlines
- connect adjacent steps with dynamic connectors; use right-angle dynamic connectors for branches and loopbacks
- keep Chinese text in SimSun and English/numerals in Times New Roman where Visio permits mixed font runs
- keep ordinary text at 10-12 pt and avoid exceeding 12 pt
- after any page-size or orientation change, re-check text centering, connector alignment, box spacing, line breaks, and whether text still fits within shapes

Do not stop at "draw a flowchart in Visio."

## Reference-Paper Calibration

When an excellent paper is provided as a visual reference, compare against it before accepting the diagram:

- Does the diagram have a clear topological grammar, or is it a box collection?
- Are branch labels, loopbacks, and exit conditions visible without tracing clutter?
- Does the figure explain model structure, optimality logic, or evidence closure?
- Does the caption carry the takeaway while the diagram itself stays clean?
- Would the figure still look formal at the actual inserted size?
- Does the figure reach the same level of solver-process clarity as the reference paper's question-level flowcharts?

Do not rely on memory or verbal impression. Inspect the recreated figure and the reference figure side by side. A replication attempt fails if any of the following remain materially worse than the reference:

- text fits poorly or overflows its box
- box proportions are visibly different from the intended topology
- connector entry/exit behavior changes the reading path
- arrowheads, line weights, or routing look rougher
- spacing and whitespace no longer support the same visual rhythm

For algorithmic or multi-stage multi-question papers, require one overall
thought-flow figure and a separate solving flowchart for every materially
different question route. Do not treat a single generic framework figure as
sufficient.

## Flowchart Failure Modes

- boxes are placed first and the logic is improvised afterward
- every connector is a straight line even though the process has decisions or returns
- loopback arrows cut through unrelated nodes
- branch labels float far away from the branch they describe
- connectors attach to random points instead of stable shape connection points
- the diagram uses many colors, shadows, or decorative effects to compensate for weak topology
- the figure looks acceptable full-size but becomes unreadable at the paper insertion width
- the diagram is technically exported from Visio but still looks like an office draft rather than a judged contest-paper figure
- the diagram only paraphrases headings instead of showing actual solving logic
- the user supplied a stronger reference flowchart, but the delivered chart is still materially weaker in connector logic, topology, or readability
- a Python or script-generated placeholder is used where a working Visio route was available, and the result is presented as if that still counts as proper flowchart production
