# Core research-delivery skills

This directory contains the versioned skills exposed through JW's `/skills/`
mount. The core sub-agents can discover these skills without depending on a
developer's personal Codex installation.

## Routing order

- **Figures:** `scientific-visualization` for data-derived publication figures;
  `math-modeling-figure-production` for contest figure contracts and native
  source requirements; `math-modeling-export-qa` only for final export review.
- **Reports:** `scientific-writing` for scientific substance, followed by
  `writing-reader-facing-content` for the reader-visible gate.
- **Presentations:** `scientific-slides` for narrative and evidence selection;
  PowerPoint file operations remain a conditional external route until a
  licensed runtime is connected.
- **Documents and builds:** Word operations remain a conditional external
  route; `latex-quality-control` owns LaTeX/Beamer compilation and rendered QA.
- **Completion:** `verification-before-completion` is required before claiming
  an artifact or run is complete.

These skills are conditional capabilities. A sub-agent must still check that
the required runtime, format libraries, GUI bridge, or external service is
available before selecting a route. Missing dependencies are reported as a
bounded limitation rather than silently replaced with an unverified fallback.
