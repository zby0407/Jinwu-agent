#!/usr/bin/env bash
set -euo pipefail

reviewer="${1:-qwen}"
review_mode="${2:-two_pass}"

case "$reviewer" in
  qwen)
    reviewer_model="${JW_EVAL_REVIEWER_MODEL:-qwen3.8-max}"
    reviewer_provider="${JW_EVAL_REVIEWER_PROVIDER:-custom-openai}"
    ;;
  deepseek)
    # Explicit comparison route only. Formal/default evaluation is Qwen-first
    # and never falls back to DeepSeek automatically.
    reviewer_model="${JW_EVAL_REVIEWER_MODEL:-deepseek-v4-pro}"
    reviewer_provider="${JW_EVAL_REVIEWER_PROVIDER:-deepseek}"
    ;;
  kimi)
    # Production Evidence route. Kimi K3 is provided by Kimi for Coding's
    # Anthropic-compatible endpoint; no cross-family fallback is automatic.
    reviewer_model="${JW_EVAL_REVIEWER_MODEL:-kimi-k3}"
    reviewer_provider="${JW_EVAL_REVIEWER_PROVIDER:-kimi-coding}"
    ;;
  *)
    echo "reviewer must be kimi, deepseek, or qwen" >&2
    exit 2
    ;;
esac

case "$review_mode" in
  closed|two_pass) ;;
  *)
    echo "review_mode must be closed or two_pass" >&2
    exit 2
    ;;
esac

export JW_EVIDENCE_REVIEW_MODE="$review_mode"
agent_overrides="solar-evidence:${reviewer_model}:${reviewer_provider}"
producer_model="${JW_EVAL_PRODUCER_MODEL:-qwen3.8-max}"
producer_provider="${JW_EVAL_PRODUCER_PROVIDER:-custom-openai}"
for producer_agent in solar-planner solar-hypothesis solar-experiment; do
  agent_overrides+=",${producer_agent}:${producer_model}:${producer_provider}"
done
light_model="${JW_EVAL_AUXILIARY_MODEL:-qwen3.7-plus}"
light_provider="${JW_EVAL_AUXILIARY_PROVIDER:-custom-openai}"
for light_agent in solar-data solar-knowledge; do
  agent_overrides+=",${light_agent}:${light_model}:${light_provider}"
done
export JW_AGENT_MODEL_OVERRIDES="$agent_overrides"
# Routing and helper work is lighter than stage production/review, so use the
# Qwen Plus tier by default. In this deployment Qwen is served by the
# Bailian OpenAI-compatible endpoint; callers may explicitly select another
# supported provider when credentials are configured.
export JW_AUXILIARY_MODEL="$light_model"
export JW_AUXILIARY_PROVIDER="$light_provider"
# Independent review is a distinct role, never the auxiliary selector. It is
# invoked only at the deterministic hypothesis/integration/final_release gates.
# Evaluation artifacts already capture the complete task state. Post-run memory
# workers are outside the Planner/Evidence graph and some providers reject their
# structured response format, so isolate them from formal gate traffic.
export JW_MEMORY_WORKERS_ENABLED="${JW_EVAL_MEMORY_WORKERS_ENABLED:-false}"
export JW_MEMORY_SKILL_SYNTHESIS_ENABLED="${JW_EVAL_MEMORY_SKILL_SYNTHESIS_ENABLED:-false}"
# Long research-stage tool planning can legitimately exceed the generic
# five-minute Qwen wall clock.  Keep normal callers unchanged, but give the
# production evaluation launcher the already-supported bounded maximum.
export JW_DASHSCOPE_REQUEST_TIMEOUT_S="${JW_DASHSCOPE_REQUEST_TIMEOUT_S:-900}"
export JW_WORKSPACE_DIR="$PWD"

log_file="${JW_EVAL_BACKEND_LOG:-$PWD/research/review/evals/runs/backend.${reviewer}.${review_mode}.log}"
mkdir -p "$(dirname "$log_file")"

.venv/bin/langgraph dev \
  --config "$PWD/jw/langgraph_dev/langgraph.json" \
  --port 6174 \
  --n-jobs-per-worker 10 \
  --allow-blocking \
  --no-browser \
  --no-reload 2>&1 | tee -a "$log_file"
