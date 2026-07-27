---
name: solar-cycle
description: |
  Solar-cycle research toolkit for Solar-Cycle Co-Scientist. Use this skill whenever the task involves
  sunspot cycle prediction, solar-dynamo mechanism explanation, F10.7 or polar-field proxy analysis,
  cycle-feature engineering, cross-cycle backtesting, index-drift detection, or structured hypothesis
  cards for solar physics. It provides data loaders, experiment scripts, plotting helpers, and an
  LLM Wiki of canonical solar-cycle knowledge.
---

# Solar-Cycle Co-Scientist Toolkit

This skill supports end-to-end solar-cycle research: load observations, build physically meaningful
features, run diagnostic experiments, generate structured hypotheses, and review evidence.

## When to use

- Predicting solar cycle 26 strength or trend
- Testing polar-field precursor, Waldmeier effect, or Hale-cycle hypotheses
- Analyzing F10.7 vs sunspot-number relationship drift
- Building cycle-level features from SILSO or F10.7 data
- Running cross-cycle backtests or feature ablations
- Creating hypothesis cards and evidence scores for solar physics

## Quick workflow

### Research modes and contract boundaries

Use the smallest mode that matches the task:

- **Explore**: return grounded working notes, alternatives, uncertainty, or a
  blocker. No frozen artifact is required.
- **Checkpoint**: use the relevant contract tools when another stage needs a
  stable structured handoff. A failed checkpoint remains partial work.
- **Publish/execute**: freeze a plan or hypothesis only when a durable formal
  artifact was explicitly requested. Real experiments still use their auditable
  bind/inspect/validate/execute/verify/finalize boundary.

The commands below protect artifact and execution integrity, but they do not
force every specialist to run:

- `solar-planner`: may return a working plan in explore mode. Use the
  research-planner contract for a requested checkpoint or published plan.
- `solar-hypothesis` and `solar-evidence`: may return evidence-labelled draft
  hypotheses/reviews. For continuing work, update individual candidates in the
  mutable draft and recover that draft after interruptions; do not regenerate
  the entire portfolio for a local edit. Bind evidence and hard-check the
  current draft for a requested checkpoint; freeze only for explicit
  publication.
- `solar-experiment`: when actual execution is requested, bind the request,
  inspect registered inputs, validate the design, prepare/execute immutable
  attempts, verify results, and finalize. Embedded copies of unbound input data
  are invalid.
- `solar-knowledge`: bind the parent research question and task-supplied distillation
  focus before literature search. Use `lit_bind_task → lit_search → lit_fetch →
  lit_distill`; do not bypass it with a generic knowledge proposal.

Parent agents accept honest draft, partial, checkpointed, published, and blocked
results. They must not describe a draft as published, repackage prose as an
execution artifact, or claim that an experiment ran without its real receipt.
After the same validation problem appears twice, stop automatic repair and return
the usable partial result plus the unresolved issue.

For literature distillation, the task owns `distill_focus`. Keep distinctive terms
from the research question and add source-language equivalents when necessary so
relevance can be audited. Single-source abstract candidates default to `low` and
cannot exceed `medium`; DOI presence never makes an entry canonical.

### Domain helper workflow

For exact historical cycle-extrema reproduction, do not infer boundaries or
reimplement the 13-month smoothing formula. Run the source-preserving helper
against SILSO's official smoothed product and official extrema table:

```bash
python /skills/solar-cycle/scripts/reproduce_silso_cycles.py \
    --cycles 21-24 \
    --output-dir ./artifacts/silso-cycle-reproduction
```

The helper keeps the downloaded source files and emits both the official and
recomputed results. If they differ, report both instead of selecting one
silently.

1. **Fetch data**
   ```bash
   python /skills/solar-cycle/scripts/fetch_data.py --output-dir ./data
   ```

2. **Build features**
   ```bash
   python /skills/solar-cycle/scripts/build_features.py \
       --sunspot ./data/SN_m_tot.csv \
       --f10.7 ./data/observed-solar-indices-*.txt \
       --output ./features/cycle_features.csv
   ```

3. **Run experiments**
   ```bash
   python /skills/solar-cycle/scripts/run_experiments.py \
       --features ./features/cycle_features.csv \
       --output-dir ./artifacts \
       --experiments baseline,backtest,ablation,polar_precursor,drift
   ```

4. **Plot**
   ```bash
   python /skills/solar-cycle/scripts/plot_cycle.py \
       --sunspot ./data/SN_m_tot.csv \
       --output ./artifacts/cycle_plot.png
   ```

## Key files

- `scripts/fetch_data.py` — download SILSO sunspot number and F10.7 data
- `scripts/reproduce_silso_cycles.py` — exact official-table versus smoothed-series cycle-extrema reproduction
- `scripts/build_features.py` — cycle-level and precursor feature engineering
- `scripts/run_experiments.py` — backtest, ablation, drift, precursor experiments
- `scripts/plot_cycle.py` — sunspot cycle and diagnostic visualizations
- `references/llm_wiki/` — canonical solar-cycle knowledge entries

## Domain constraints

- Sunspot number and F10.7 are **proxies**, not direct measurements of the internal magnetic field.
- Polar-field records are short; conclusions based on them must carry sample-size caveats.
- Correlation is not mechanism. Always separate statistical relationship from causal dynamo explanation.
- Never present a trend judgment as an official deterministic forecast.

## LLM Wiki

The built-in Wiki is a compact, versioned domain knowledge pack rather than a
raw-document RAG corpus. Before using individual entries:

1. Read `references/llm_wiki/00_core/scope_and_epistemic_rules.md` and
   `references/llm_wiki/00_core/core_mechanism_map.md`.
2. Resolve the current research task through
   `references/llm_wiki/_meta/manifest.yaml`.
3. Load the task bundle's module indexes and implemented canonical entries.
4. Carry every material entry id and its boundary conditions into downstream
   plans, hypotheses, reviews, and reports.

The versioned files under `references/llm_wiki/` provide built-in canonical
grounding. Dynamic findings, counterexamples, and failed-run experience remain
candidate research memory in the knowledge service until they pass the review
gate. Do not silently treat a runtime candidate as built-in canonical knowledge.

Each knowledge entry retains the project schema:
`id`, `type`, `title`, `content`, `source_type`, `source_ref`, `confidence`,
`status`, `valid_range`, `related_ids`, with provenance and version history
managed by the knowledge service where applicable.
