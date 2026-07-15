from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import error, request

def resolve_roots() -> tuple[Path, Path]:
    env_root = os.getenv("B3_PROJECT_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        code_root = root / "code" if (root / "code" / "src").exists() else root
        return root, code_root
    candidate = Path(__file__).resolve().parents[1]
    if candidate.name == "code" and (candidate.parent / "release_manifest.json").exists():
        return candidate.parent, candidate
    return candidate, candidate


ROOT, CODE_ROOT = resolve_roots()
SRC = CODE_ROOT / "src"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from app_b3 import SolarCycleHandler

PROOFS_DIR = ROOT / "proofs" if (ROOT / "release_manifest.json").exists() else ROOT / "b3" / "proofs"
JSON_PROOF = PROOFS_DIR / "frontend_api_smoke.json"
MD_PROOF = PROOFS_DIR / "frontend_api_smoke.md"


def http_request(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json, text/html, text/plain"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=45) as resp:
            body = resp.read()
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
    except error.HTTPError as exc:
        body = exc.read()
        status = exc.code
        content_type = exc.headers.get("Content-Type", "")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    text = body.decode("utf-8", errors="replace")
    parsed: Any | None = None
    if "json" in content_type:
        parsed = json.loads(text)
    return {
        "method": method,
        "path": path,
        "status": status,
        "content_type": content_type,
        "elapsed_ms": elapsed_ms,
        "text": text,
        "json": parsed,
    }


def ok_check(checks: list[dict[str, Any]], check_id: str, passed: bool, evidence: Any) -> None:
    checks.append({"id": check_id, "passed": bool(passed), "evidence": evidence})


def build_smoke_proof() -> dict[str, Any]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), SolarCycleHandler)
    port = int(server.server_address[1])
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    checks: list[dict[str, Any]] = []
    responses: dict[str, Any] = {}
    try:
        home = http_request(base_url, "GET", "/")
        responses["home"] = {k: v for k, v in home.items() if k not in {"text", "json"}}
        ok_check(
            checks,
            "frontend_home_loaded",
            home["status"] == 200
            and "Solar-Cycle Co-Scientist" in home["text"]
            and "启动研究" in home["text"]
            and "研究任务" in home["text"],
            {"status": home["status"], "content_type": home["content_type"]},
        )

        app_js = http_request(base_url, "GET", "/static_b3/app.js")
        responses["app_js"] = {k: v for k, v in app_js.items() if k not in {"text", "json"}}
        ok_check(
            checks,
            "frontend_iteration_tab_present",
            app_js["status"] == 200 and "renderIterations" in app_js["text"] and "迭代轨迹" in app_js["text"],
            {"status": app_js["status"], "bytes": len(app_js["text"].encode("utf-8"))},
        )

        health = http_request(base_url, "GET", "/api/health")
        responses["health"] = health["json"]
        ok_check(
            checks,
            "api_health_ok",
            health["status"] == 200 and health["json"].get("status") == "ok",
            health["json"],
        )

        model = http_request(base_url, "GET", "/api/model/status")
        responses["model_status"] = model["json"]
        ok_check(
            checks,
            "api_model_status_declares_qwen_route",
            model["status"] == 200 and model["json"].get("provider") == "Alibaba Cloud Model Studio / Qwen",
            {"mode": model["json"].get("mode"), "api_key_present": model["json"].get("api_key_present")},
        )

        readiness = http_request(base_url, "GET", "/api/readiness-report")
        readiness_json = readiness["json"]
        responses["readiness"] = {
            "ready": readiness_json.get("ready"),
            "check_count": len(readiness_json.get("checks", [])),
            "metrics": readiness_json.get("metrics", {}),
        }
        iteration_trace_check = next(
            (
                item
                for item in readiness_json.get("checks", [])
                if item.get("id") == "research_iteration_trace_visible"
            ),
            {},
        )
        responses["readiness"]["iteration_trace_check_passed"] = (
            iteration_trace_check.get("passed") is True
        )
        ok_check(
            checks,
            "api_readiness_reports_iteration_trace",
            readiness["status"] == 200
            and iteration_trace_check.get("passed") is True,
            responses["readiness"],
        )

        evidence = http_request(
            base_url,
            "POST",
            "/api/evidence/query",
            {"query": "polar precursor Babcock-Leighton WSO", "limit": 5},
        )
        evidence_json = evidence["json"]
        evidence_results = evidence_json.get("results", []) if isinstance(evidence_json, dict) else []
        responses["evidence_query"] = {
            "result_count": len(evidence_results),
            "first_source_id": evidence_results[0].get("source", {}).get("id") if evidence_results else None,
        }
        ok_check(
            checks,
            "api_evidence_query_returns_sources",
            evidence["status"] == 200
            and isinstance(evidence_json, dict)
            and isinstance(evidence_results, list)
            and len(evidence_results) >= 1,
            responses["evidence_query"],
        )

        research_payload = {
            "task": "cycle26_prediction",
            "data_sources": ["silso_sunspot", "noaa_f10_7", "silso_hemispheric", "wso_polar_field"],
            "agent_mode": "hypothesis_experiment_review",
            "max_iterations": 3,
        }
        research = http_request(base_url, "POST", "/api/research/run", research_payload)
        run = research["json"]
        completed_iterations = sum(1 for item in run.get("iteration_trace", []) if item.get("status") == "completed")
        responses["research_run"] = {
            "run_id": run.get("run_id"),
            "experiment_count": len(run.get("experiments", [])),
            "hypothesis_count": len(run.get("hypotheses", [])),
            "iteration_count": len(run.get("iteration_trace", [])),
            "completed_iterations": completed_iterations,
            "top_hypothesis": run.get("report", {}).get("top_hypothesis", {}).get("id"),
            "confidence": run.get("report", {}).get("prediction", {}).get("confidence"),
        }
        ok_check(
            checks,
            "api_research_run_closed_loop",
            research["status"] == 200
            and responses["research_run"]["experiment_count"] >= 8
            and responses["research_run"]["completed_iterations"] >= 3
            and responses["research_run"]["top_hypothesis"] == "H1_poloidal_precursor_needed",
            responses["research_run"],
        )

        report_path = f"/api/research/{run.get('run_id')}/report?format=md"
        report = http_request(base_url, "GET", report_path)
        responses["markdown_report"] = {k: v for k, v in report.items() if k not in {"text", "json"}}
        ok_check(
            checks,
            "api_research_markdown_report_available",
            report["status"] == 200 and "Iteration Trace" in report["text"] and "Top Hypothesis" in report["text"],
            {"status": report["status"], "bytes": len(report["text"].encode("utf-8"))},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    passed = all(item["passed"] for item in checks)
    return {
        "schema_version": "b3-frontend-api-smoke-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if passed else "failed",
        "base_url": base_url,
        "server": "ThreadingHTTPServer(app_b3.SolarCycleHandler)",
        "checks": checks,
        "responses": responses,
    }


def write_markdown(proof: dict[str, Any], path: Path) -> None:
    rows = [
        "# 前端/API真实调用证明",
        "",
        f"- checked_at：`{proof['checked_at']}`",
        f"- status：`{proof['status']}`",
        f"- server：`{proof['server']}`",
        "",
        "| 检查项 | 通过 | 证据 |",
        "| --- | --- | --- |",
    ]
    for item in proof["checks"]:
        rows.append(f"| `{item['id']}` | `{item['passed']}` | `{json.dumps(item['evidence'], ensure_ascii=False)}` |")
    rows.extend(
        [
            "",
            "该证明由`scripts_b3/check_frontend_api_smoke.py`自动生成。脚本会在本机随机端口启动`app_b3.py`同一HTTP处理器，访问前端首页和核心API，并保存脱敏结果；不需要外网、不读取密钥。",
        ]
    )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the B3 frontend/API smoke check.")
    parser.add_argument("--output-dir", type=Path, default=PROOFS_DIR)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    proof = build_smoke_proof()
    json_path = args.output_dir / JSON_PROOF.name
    md_path = args.output_dir / MD_PROOF.name
    if not args.no_write:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf-8")
        write_markdown(proof, md_path)
    print(json.dumps({
        "proof": None if args.no_write else str(json_path),
        "status": proof["status"],
        "checks": len(proof["checks"]),
        "written": not args.no_write,
    }, ensure_ascii=False, indent=2))
    if proof["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
