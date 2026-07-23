#!/usr/bin/env bash
set -euo pipefail

attempt_root=""
runtime_file=""
wall_seconds=""
cpu_seconds=""
memory_bytes=""
file_bytes=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --attempt-root) attempt_root="$2"; shift 2 ;;
    --runtime-file) runtime_file="$2"; shift 2 ;;
    --wall-seconds) wall_seconds="$2"; shift 2 ;;
    --cpu-seconds) cpu_seconds="$2"; shift 2 ;;
    --memory-bytes) memory_bytes="$2"; shift 2 ;;
    --file-bytes) file_bytes="$2"; shift 2 ;;
    *) echo "unsupported sandbox runner argument" >&2; exit 64 ;;
  esac
done

for value in "$wall_seconds" "$cpu_seconds" "$memory_bytes" "$file_bytes"; do
  [[ "$value" =~ ^[0-9]+$ ]] || { echo "numeric sandbox limit required" >&2; exit 64; }
done

[[ -d "$attempt_root/code" ]] || { echo "attempt code directory missing" >&2; exit 66; }
[[ -d "$attempt_root/output" ]] || { echo "attempt output directory missing" >&2; exit 66; }
[[ -f "$attempt_root/code/experiment.py" ]] || { echo "experiment.py missing" >&2; exit 66; }
[[ -f "$attempt_root/code/worker_request.json" ]] || { echo "worker request missing" >&2; exit 66; }
[[ -f "$runtime_file" ]] || { echo "trusted worker missing" >&2; exit 66; }
site_packages="$(/usr/bin/python3 -c 'import site; print(site.getusersitepackages())')"
[[ -d "$site_packages" ]] || { echo "locked user site-packages directory missing" >&2; exit 66; }

input_root="$(dirname "$(dirname "$attempt_root")")/inputs"
[[ -d "$input_root" ]] || { echo "input snapshot directory missing" >&2; exit 66; }
prior_root="$(dirname "$(dirname "$attempt_root")")/stage_artifacts"
[[ -d "$prior_root" ]] || { echo "prior stage artifact directory missing" >&2; exit 66; }

bwrap_args=(
  --die-with-parent
  --new-session
  --unshare-user
  --uid 65534
  --gid 65534
  --unshare-pid
  --unshare-net
  --unshare-ipc
  --unshare-uts
  --clearenv
  --setenv HOME /tmp
  --setenv LANG C.UTF-8
  --setenv LC_ALL C.UTF-8
  --setenv PYTHONDONTWRITEBYTECODE 1
  --setenv PYTHONHASHSEED 0
  --setenv MPLCONFIGDIR /tmp/matplotlib
  --setenv OMP_NUM_THREADS 1
  --setenv OPENBLAS_NUM_THREADS 1
  --setenv MKL_NUM_THREADS 1
  --setenv NUMEXPR_NUM_THREADS 1
  --ro-bind /usr /usr
  --proc /proc
  --dev /dev
  --tmpfs /tmp
  --dir /workspace
  --dir /runtime
  --ro-bind "$site_packages" /runtime/site-packages
  --ro-bind "$attempt_root/code" /workspace/code
  --ro-bind "$input_root" /workspace/input
  --ro-bind "$prior_root" /workspace/prior
  --bind "$attempt_root/output" /workspace/output
  --ro-bind "$runtime_file" /runtime/sandbox_worker.py
  --chdir /workspace
)

for system_path in /lib /lib64 /etc/ld.so.cache /etc/fonts; do
  if [[ -e "$system_path" ]]; then
    bwrap_args+=(--ro-bind "$system_path" "$system_path")
  fi
done

exit_code=1
startup_attempt=0
startup_retries=0
while [[ "$startup_attempt" -lt 4 ]]; do
  startup_attempt=$((startup_attempt + 1))
  startup_log="$attempt_root/sandbox-start-${startup_attempt}.stderr"
  : > "$startup_log"
  set +e
  setsid /usr/bin/time -v -o "$attempt_root/resource.txt" \
    timeout --signal=TERM --kill-after=5 "$wall_seconds" \
    bwrap "${bwrap_args[@]}" \
      /usr/bin/prlimit \
        --as="$memory_bytes" \
        --cpu="$cpu_seconds" \
        --fsize="$file_bytes" \
        --nproc=32 \
        --nofile=256 \
        -- \
        /usr/bin/python3 -I -B /runtime/sandbox_worker.py \
          --experiment /workspace/code/experiment.py \
          --request /workspace/code/worker_request.json \
          --result /workspace/output/result.json \
    2> >(tee -a "$startup_log" >&2) &
  child_pid=$!
  printf '%s\n' "$child_pid" > "$attempt_root/sandbox.pid"
  wait "$child_pid"
  exit_code=$?
  set -e
  if [[ "$exit_code" -eq 1 ]] \
    && grep -Fq "bwrap: Creating new namespace failed: Resource temporarily unavailable" "$startup_log" \
    && [[ "$startup_attempt" -lt 4 ]]; then
    startup_retries=$((startup_retries + 1))
    sleep $((1 << (startup_attempt - 1)))
    continue
  fi
  break
done
printf '{"attempts":%s,"retries":%s}\n' "$startup_attempt" "$startup_retries" \
  > "$attempt_root/sandbox_start.json"
printf '{"exit_code":%s}\n' "$exit_code" > "$attempt_root/sandbox_exit.json"
exit "$exit_code"
