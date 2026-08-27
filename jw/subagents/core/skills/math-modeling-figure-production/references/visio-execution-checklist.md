# Visio Execution Checklist for Contest Flowcharts

Use this checklist when a mathematical-modeling paper needs a formal flowchart, solving-route diagram, or framework figure and a Visio route is available.

This file is intentionally practical. It is not a style essay. It records the concrete steps that should be completed before claiming a Visio figure is good enough for a judged paper.

## When This Checklist Applies

Apply this checklist if any of the following is true:

- the figure is a flowchart, algorithm route, model framework, or question-level solving diagram
- a `.vsdx` source already exists
- the user asks for Visio
- the user provides a prize-winning paper and expects comparable flowchart quality

Do not bypass this checklist by drawing a substitute in Python while a working Visio route is available.

## Phase 1: Choose the Correct Tool Route

Before drawing, confirm the route explicitly:

- Is there an editable `.vsdx` source already?
- Can Visio open and export in-session?
- Is the target figure primarily a logic/process figure rather than a numerical plot?

If the answers are yes, Visio is the primary route.

## Phase 2: Inspect the Reference Before Rebuilding

If a reference paper or reference flowchart exists:

- open the reference image at full size
- inspect it directly instead of relying on memory
- record what makes it strong

At minimum, note:

- page direction: horizontal or vertical
- main reading path
- number of rows or columns
- shape families used
- line weight hierarchy
- text density
- white-space rhythm
- where loopbacks and vertical connectors are routed

Do not start reconstruction until those observations are concrete.

## Phase 3: Set the Topology Before Placing Shapes

Decide the topology first:

- main path
- branch path
- loopback path
- entry points
- exits

Write that structure in one or two lines before drawing.

Example:

- top row: initialization and forward task setup
- middle row: global search and comparison
- bottom row: local refinement and final output

If the topology is unclear, drawing boxes earlier will make the final figure feel accidental.

## Phase 4: Draw with Shape Semantics

Use consistent shape grammar:

- rounded terminals for start/end
- rectangles for process/model steps
- diamonds for decisions/convergence tests

Avoid changing shape families only for decoration.

When reproducing a strong reference figure, match:

- terminal width/height ratio
- rectangle compactness
- diamond proportion
- line thickness contrast between node borders and connectors

## Phase 5: Use Visio as a Real Drawing Tool

Do not use Visio only as an export container.

Actively use:

- shape size editing
- connector routing
- text block controls
- margins and internal text spacing
- grouping where appropriate
- alignment when it helps
- distribution when it helps
- manual nudging when distribution hurts topology

Important:

- left align / center / distribute are tools, not the whole method
- if automatic distribution damages the reading path, revert it
- manual micro-adjustment is normal in final judged-paper diagrams

## Phase 6: Text Fit Rules

Before export, inspect every text block:

- no overflow
- no clipped bottom lines
- no text floating too high or too low
- no accidental over-compression caused by too-small text boxes

Practical controls to check:

- font family
- font size
- line spacing feel
- text block width and height
- left/right/top/bottom margins

If a box only works because the font was made too small, resize the box instead.

## Phase 7: Connector Rules

Check every connector:

- does it enter and leave the correct side of each node?
- does it preserve the intended reading order?
- does it stay clear of unrelated nodes?
- does it have the same arrowhead language as the rest of the diagram?

Red flags:

- diagonal shortcut lines where the reference uses a cleaner orthogonal route
- connectors touching nodes at arbitrary-looking points
- loopbacks running through the middle of the figure
- lines that technically connect but make the logic harder to read

## Phase 8: Page Rhythm and White Space

A strong contest-paper flowchart is not just correctly connected; it has stable rhythm.

Check:

- are the three main horizontal bands visually balanced?
- is the whitespace between rows intentional?
- are start/end nodes too far left or too low?
- is one side of the figure visibly heavier than the other?
- do local boxes look too thin, too tall, or too sparse compared with the reference?

If yes, adjust spacing, not just text.

## Phase 9: Export and Compare

A Visio figure is not finished when the `.vsdx` saves.

Required export step:

- export high-resolution PNG
- if useful, also export SVG

Required QA step:

- inspect the exported figure by itself
- create a side-by-side comparison with the reference if one exists

The comparison must answer:

- Is the topology equally clear?
- Is the line routing equally intentional?
- Is the text equally readable?
- Is the visual weight equally mature?

If not, the figure is not done.

## Phase 10: Final Honesty Check

Before claiming success, ask:

- Is this truly a Visio-produced judged-paper figure?
- Or is it still acting like a rough office draft?

Do not claim the Visio route is successful if:

- text overflow was only hidden, not solved
- connectors are still materially rougher than the reference
- page rhythm is still visibly weaker
- the figure is merely “usable” rather than judged-paper level

## Failure Patterns Seen in Real Work

These are common ways a Visio attempt fails even after export:

- using Python first and only later pretending the task was Visio-first
- drawing boxes correctly but leaving text block sizing as defaults
- making every line thicker to imitate quality, instead of fixing spacing
- using alignment/distribution tools mechanically and destroying topology
- exporting once and never checking the rendered PNG
- comparing only by memory instead of by side-by-side image inspection

## Minimal Deliverables for a Completed Visio Task

A proper completion should leave behind:

- editable `.vsdx`
- exported PNG
- if relevant, exported SVG
- one rendered comparison image when a reference figure was provided

Only then can the Visio route be treated as genuinely demonstrated.
