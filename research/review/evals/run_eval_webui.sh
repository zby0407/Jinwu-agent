#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$root"

export JW_WORKSPACE_DIR="$root"
export JW_LANGGRAPH_DEV_PORT="${JW_LANGGRAPH_DEV_PORT:-6174}"
export PORT="${PORT:-4717}"
# HOSTNAME is normally pre-populated with the machine name and therefore is
# not a safe default for a loopback-only evaluation server.
export HOSTNAME="${JW_EVAL_WEBUI_HOST:-127.0.0.1}"

exec node webui/dist/server.js
