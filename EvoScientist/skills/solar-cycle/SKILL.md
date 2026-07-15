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
