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

### Closed-contract routing (mandatory for agents)

The commands below are domain helpers, not substitutes for an agent contract.

- `solar-planner`: first bind the exact request, then use the research-planner
  evidence/data tools as needed, validate the complete response, and freeze it.
  A free Markdown plan or workspace file without a frozen `planner/runs/<run_id>/`
  receipt is invalid.
- `solar-hypothesis`: bind the request; inspect every referenced automatic-experiment
  run; bind evidence; validate the complete response; submit the seven-dimension,
  evidence-anchored ranking; then freeze it. Free-form cards, unverified run summaries,
  and portfolios without a ranking are invalid. A parent that receives a bundled
  `hypothesis/inputs/` path must pass that relative path unchanged to this specialist;
  it must not probe, copy, create, or stage the input with generic file or shell tools.
- `solar-evidence`: use its declared evidence-review contract. Do not substitute the
  hypothesis Agent's tools or produce an unreceipted score list.
- `solar-experiment`: bind request, inspect registered inputs, validate the design,
  prepare/execute immutable attempts, verify results, and finalize. Embedded copies
  of unbound input data are invalid.
- `solar-knowledge`: bind the parent research question and task-supplied distillation
  focus before literature search. Use `lit_bind_task → lit_search → lit_fetch →
  lit_distill`; do not bypass it with a generic knowledge proposal.

Parent agents must reject a specialist result that lacks the contract's completion
status, run id, and saved artifact path. They must not repackage free prose as if the
specialist had completed its contract.

For literature distillation, the task owns `distill_focus`. Keep distinctive terms
from the research question and add source-language equivalents when necessary so
relevance can be audited. Single-source abstract candidates default to `low` and
cannot exceed `medium`; DOI presence never makes an entry canonical.

### Domain helper workflow

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

Retrieve canonical knowledge from `references/llm_wiki/` when grounding assumptions,
mechanisms, data sources, or experiment paradigms. Each entry follows the schema:
`id`, `type`, `title`, `content`, `source_type`, `source_ref`, `confidence`, `status`, `valid_range`, `related_ids`.
