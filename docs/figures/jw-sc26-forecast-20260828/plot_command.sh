#!/usr/bin/env bash
set -euo pipefail
cd /home/zzz/2026tzb/8.20.4
python3 scripts/plot_jw_historical_sc26_forecast.py \
  --legacy-dir research/review/evals/runs/sc26_forecast_backtest_20260827/results \
  --polar-run-dir research/review/evals/runs/jw_solar_upgrade_20260828/project_root/projects/default/runs/run_jw-solar-upgrade-2_24729abb/experiment/runs/question_f30956e10616-20260828T121022Z-42d44151 \
  --output-dir docs/figures/jw-sc26-forecast-20260828
