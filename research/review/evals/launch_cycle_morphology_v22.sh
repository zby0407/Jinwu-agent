#!/usr/bin/env bash
set -euo pipefail

repo="/home/zzz/2026tzb/8.20.4"
run_label="main_cycle_morphology.v22"
run_dir="$repo/research/review/evals/runs/$run_label"

export TMPDIR=/tmp
export TEMP=/tmp
export TMP=/tmp
export JW_DATA_DIR="$run_dir/jw_data"
export JW_WORKSPACE_DIR="$repo/.morphology-workspace-v22-20260827"
export JW_WORKSPACE_BINDINGS_DIR="$repo/.morphology-bindings-v22-20260827"
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

mkdir -p \
  "$run_dir" \
  "$JW_DATA_DIR" \
  "$JW_WORKSPACE_DIR" \
  "$JW_WORKSPACE_BINDINGS_DIR"
cd "$repo"

case "${1:-}" in
  backend)
    exec .venv/bin/langgraph dev \
      --config "$repo/jw/langgraph_dev/langgraph.json" \
      --port 6178 \
      --n-jobs-per-worker 10 \
      --allow-blocking \
      --no-browser \
      --no-reload
    ;;
  frontend)
    export JW_LANGGRAPH_DEV_PORT=6178
    export PORT=4720
    export HOSTNAME=127.0.0.1
    exec node webui/dist/server.js
    ;;
  *)
    printf 'usage: %s [backend|frontend]\n' "$0" >&2
    exit 2
    ;;
esac
