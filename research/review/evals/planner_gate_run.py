"""Run the run3 planner question 3x via langgraph dev SDK and report freeze.

Each run: fresh thread -> submit run3 question to the JW graph with
qwen3.8-max / custom-openai -> poll runs until terminal -> locate the new run
workspace -> detect planner freeze (planner/runs/<plan_id>/research_plan.json
with status=="frozen") and any 400 / illegal-route signal.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from langgraph_sdk import get_sync_client

BACKEND = "http://127.0.0.1:6174"
QUESTION = (
    "请针对太阳活动周前兆预测做一遍完整研究。基于现有 SILSO 黑子数与 "
    "MWO/WSO 极区磁场数据，比较多种前兆指标（极区场强、黑子数、地磁 aa 指数），"
    "设计留一活动周交叉验证的预测方案，明确数据来源、评估规则、报告结构与停止条件，"
    "提出可检验假设并完成实验，最后整理成完整报告。"
)
WORKSPACE = Path("/home/zzz/2026tzb/8.7.16")
# The langgraph dev server runs with JW_WORKSPACE_DIR=<repo root> (no
# "/workspace" suffix), so per-run workspaces land in <repo>/projects/... .
# Read the base from the live dev-server process env when possible, else fall
# back to the repo root; never assume the "/workspace" subdir.
def _dev_workspace_base() -> Path:
    import subprocess

    try:
        out = subprocess.run(
            ["pgrep", "-f", "langgraph dev"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.split()
        for pid in out:
            env = Path(f"/proc/{pid}/environ")
            if not env.is_file():
                continue
            for entry in env.read_bytes().decode("utf-8", "ignore").split("\0"):
                if entry.startswith("JW_WORKSPACE_DIR="):
                    return Path(entry.split("=", 1)[1]).resolve()
    except Exception:
        pass
    return WORKSPACE.resolve()


_BASE = _dev_workspace_base()
RUNS = _BASE / "projects" / "default" / "runs"
REPORT = WORKSPACE / "research" / "review" / "evals" / "planner_gate_report.json"
POLL_TIMEOUT_S = 90 * 60  # multi-round repair can push a run past 45 min; allow headroom


def _find_run_for_thread(thread_id: str, timeout_s: int = 180) -> Path | None:
    """Locate the per-run workspace for THIS thread only.

    The langgraph dev server names each per-run workspace
    ``run_<thread-id-first-3-segments>_<suffix>``. Matching on the thread id
    prefix (rather than a snapshot set-difference) makes detection immune to
    unrelated runs created concurrently in the same shared runs directory —
    a snapshot-diff would misattribute a foreign run to this gate run.
    """
    prefix = "-".join(thread_id.split("-")[:3])
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if RUNS.is_dir():
            matches = [
                p
                for p in RUNS.iterdir()
                if p.is_dir() and p.name.startswith(f"run_{prefix}_")
            ]
            if matches:
                return sorted(matches)[-1]
        time.sleep(3)
    return None


def _check_freeze(run_dir: Path) -> dict:
    result = {
        "frozen": False,
        "plan_id": None,
        "illegal_route": None,
        "error": None,
    }
    try:
        runs_root = run_dir / "planner" / "runs"
        if runs_root.is_dir():
            for plan_dir in runs_root.iterdir():
                plan_file = plan_dir / "research_plan.json"
                if plan_file.is_file():
                    plan = json.loads(plan_file.read_text(encoding="utf-8"))
                    if plan.get("status") == "frozen":
                        result["frozen"] = True
                        result["plan_id"] = plan.get("plan_id")
        # planner working_state failure receipts signal a rejected (illegal) route
        failures = list(run_dir.glob("planner/drafts/*/failures/**/*.json"))
        if failures:
            msgs = []
            for f in failures:
                try:
                    msgs.append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    pass
            illegal = [
                m for m in msgs
                if "transition" in json.dumps(m, ensure_ascii=False).lower()
                or "outcome" in json.dumps(m, ensure_ascii=False).lower()
            ]
            if illegal:
                result["illegal_route"] = f"{len(illegal)} route failure receipts"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def run_once(client, index: int) -> dict:
    thread = client.threads.create()
    thread_id = thread["thread_id"]
    run = client.runs.create(
        thread_id,
        "JW",
        input={"messages": [{"id": str(uuid.uuid4()), "type": "human", "content": QUESTION}]},
        config={
            "configurable": {
                "project_id": "default",
                "model": "qwen3.8-max",
                "model_provider": "custom-openai",
            }
        },
        metadata={"project_id": "default", "title": f"planner-gate-{index}"},
        multitask_strategy="interrupt",
    )
    run_id = run.get("run_id")
    rec = {
        "index": index,
        "thread_id": thread_id,
        "run_id": run_id,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "terminal_status": None,
        "run_dir": None,
        "freeze": None,
        "provider_400": False,
        "elapsed_s": None,
    }
    start = time.time()
    deadline = start + POLL_TIMEOUT_S
    status = "unknown"
    while time.time() < deadline:
        try:
            row = client.runs.get(thread_id, run_id)
            status = row.get("status", "unknown")
        except Exception as exc:  # noqa: BLE001
            status = f"poll_error:{type(exc).__name__}"
        if status in {"success", "error", "interrupted", "timeout"}:
            break
        if status.startswith("poll_error"):
            break
        time.sleep(15)
    rec["terminal_status"] = status
    rec["elapsed_s"] = round(time.time() - start, 1)

    run_dir = _find_run_for_thread(thread_id)
    if run_dir is not None:
        rec["run_dir"] = run_dir.name
        rec["freeze"] = _check_freeze(run_dir)
    # provider 400 detection from run error text
    try:
        row = client.runs.get(thread_id, run_id)
        err_blob = json.dumps(row, ensure_ascii=False)
        rec["provider_400"] = ("400" in err_blob and ("Arrearage" in err_blob or "access_denied" in err_blob))
        if rec["freeze"] is None:
            rec["freeze"] = {"frozen": False, "error": "no run workspace located"}
    except Exception:  # noqa: BLE001
        pass
    return rec


def main() -> None:
    client = get_sync_client(url=BACKEND)
    results = []
    for i in range(1, 4):
        print(f"[gate] starting run {i}/3 ...", flush=True)
        rec = run_once(client, i)
        results.append(rec)
        print(
            f"[gate] run {i}: terminal={rec['terminal_status']} "
            f"frozen={rec['freeze'].get('frozen') if rec['freeze'] else None} "
            f"400={rec['provider_400']} elapsed={rec['elapsed_s']}s",
            flush=True,
        )
        REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = sum(
        1 for r in results
        if r["freeze"] and r["freeze"].get("frozen") and not r["provider_400"]
    )
    print(f"[gate] RESULT: {ok}/3 frozen without provider 400", flush=True)
    REPORT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
