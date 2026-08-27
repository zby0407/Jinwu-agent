#!/usr/bin/env bash
set -euo pipefail
repo="/home/zzz/2026tzb/8.20.4"
export JW_WORKSPACE_DIR="$repo/.sc26-webui-workspace-20260827"
export JW_WORKSPACE_BINDINGS_DIR="$repo/.sc26-webui-bindings-20260827"
export JW_DATA_DIR="$repo/research/review/evals/runs/sc26_forecast_backtest_20260827/jw_data"
export JW_DASHSCOPE_PROXY_MODE=direct
export JW_EVIDENCE_REVIEW_MODE=two_pass
export JW_EVAL_PRODUCER_MODEL=qwen3.7-plus
export JW_EVAL_PRODUCER_PROVIDER=custom-openai
export JW_EVAL_AUXILIARY_MODEL=qwen3.7-plus
export JW_EVAL_AUXILIARY_PROVIDER=custom-openai
export JW_EVAL_REVIEWER_MODEL=qwen3.7-plus
export JW_EVAL_REVIEWER_PROVIDER=custom-openai
export JW_AGENT_MODEL_OVERRIDES="solar-evidence:qwen3.7-plus:custom-openai,solar-planner:qwen3.7-plus:custom-openai,solar-hypothesis:qwen3.7-plus:custom-openai,solar-experiment:qwen3.7-plus:custom-openai,solar-data:qwen3.7-plus:custom-openai,solar-knowledge:qwen3.7-plus:custom-openai"
export JW_MEMORY_WORKERS_ENABLED=false
export JW_MEMORY_SKILL_SYNTHESIS_ENABLED=false
export JW_DASHSCOPE_REQUEST_TIMEOUT_S=900
mkdir -p "$JW_WORKSPACE_DIR" "$JW_WORKSPACE_BINDINGS_DIR" "$JW_DATA_DIR"
cd "$repo"
case "${1:-}" in
  backend) exec .venv/bin/langgraph dev --config "$repo/jw/langgraph_dev/langgraph.json" --port 6183 --n-jobs-per-worker 10 --allow-blocking --no-browser --no-reload ;;
  frontend) export JW_LANGGRAPH_DEV_PORT=6183; export PORT=4723; export HOSTNAME=127.0.0.1; exec node webui/dist/server.js ;;
  *) echo "usage: $0 backend|frontend" >&2; exit 2 ;;
esac
