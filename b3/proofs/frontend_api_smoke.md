# 前端/API真实调用证明

- checked_at：`2026-07-14T11:54:42.655350+00:00`
- status：`passed`
- server：`ThreadingHTTPServer(app_b3.SolarCycleHandler)`

| 检查项 | 通过 | 证据 |
| --- | --- | --- |
| `frontend_home_loaded` | `True` | `{"status": 200, "content_type": "text/html"}` |
| `frontend_iteration_tab_present` | `True` | `{"status": 200, "bytes": 10983}` |
| `api_health_ok` | `True` | `{"status": "ok", "app": "Solar-Cycle Co-Scientist", "direction": "2B3 solar-cycle origin exploration", "model_route": "Qwen/Bailian-ready; local deterministic fallback is active"}` |
| `api_model_status_declares_qwen_route` | `True` | `{"mode": "deterministic_fallback", "api_key_present": false}` |
| `api_readiness_reports_iteration_trace` | `True` | `{"ready": true, "check_count": 20, "metrics": {"cycle_count": 24, "hypothesis_count": 5, "top_hypothesis": "H1_poloidal_precursor_needed", "polar_precursor_pairs": 4, "dynamo_toy_rmse_ssn": 18.487, "tournament_top_hypothesis": "H1_poloidal_precursor_needed", "model_mode": "deterministic_fallback", "evidence_source_count": 21}, "iteration_trace_check_passed": true}` |
| `api_evidence_query_returns_sources` | `True` | `{"result_count": 4, "first_source_id": "SRC_WSO_POLAR"}` |
| `api_research_run_closed_loop` | `True` | `{"run_id": "cycle26_prediction_20260714T115442Z_8798b549", "experiment_count": 8, "hypothesis_count": 5, "iteration_count": 3, "completed_iterations": 3, "top_hypothesis": "H1_poloidal_precursor_needed", "confidence": 0.74}` |
| `api_research_markdown_report_available` | `True` | `{"status": 200, "bytes": 2458}` |

该证明由`scripts_b3/check_frontend_api_smoke.py`自动生成。脚本会在本机随机端口启动`app_b3.py`同一HTTP处理器，访问前端首页和核心API，并保存脱敏结果；不需要外网、不读取密钥。
