# 严格提交流水线运行证明

- checked_at：`2026-07-14T11:57:29.962990+00:00`
- status：`passed`
- strict_qwen_requested：`False`

| 步骤 | 通过 | 耗时ms | 命令 |
| --- | --- | --- | --- |
| `qwen_connection_check` | `True` | `179.5` | `python scripts_b3/check_qwen_connection.py` |
| `run_b3_analysis` | `True` | `1082.8` | `python scripts_b3/run_b3_analysis.py` |
| `export_representative_test_cases` | `True` | `341.8` | `python scripts_b3/export_representative_test_cases.py` |
| `build_final_technical_report` | `True` | `1175.0` | `python scripts_b3/build_final_technical_report.py` |
| `frontend_api_smoke` | `True` | `1596.2` | `python scripts_b3/check_frontend_api_smoke.py` |
| `frontend_visual_qa_proof` | `True` | `482.9` | `python scripts_b3/build_frontend_visual_qa_proof.py` |
| `evaluate_pi_science_agents_fixture` | `True` | `4386.7` | `python scripts_b3/evaluate_pi_science_agents.py --mode fixture` |
| `write_pi_science_agents_readiness` | `True` | `31933.3` | `python scripts_b3/verify_pi_agent_skills.py --run-gates --write-proof` |
| `build_final_submission_checklist_initial` | `True` | `631.1` | `python scripts_b3/build_final_submission_checklist.py` |
| `build_submission_release_initial` | `True` | `1108.3` | `python scripts_b3/build_submission_release.py` |
| `unit_tests` | `True` | `82764.7` | `python -m unittest discover -s tests` |
| `verify_b3_package` | `True` | `6057.3` | `python scripts_b3/verify_b3_package.py` |
| `verify_b3_release` | `True` | `479.9` | `python scripts_b3/verify_b3_release.py` |
| `build_final_submission_checklist_final` | `True` | `589.5` | `python scripts_b3/build_final_submission_checklist.py` |
| `build_submission_release_final` | `True` | `967.3` | `python scripts_b3/build_submission_release.py` |
| `build_submission_zip` | `True` | `2462.2` | `python scripts_b3/build_submission_zip.py` |
| `build_three_agent_bundle` | `True` | `1489.8` | `python scripts_b3/build_three_agent_bundle.py` |
| `verify_three_agent_bundle_replay` | `True` | `33863.0` | `python scripts_b3/verify_three_agent_bundle.py dist/B3_三Agent --source-root . --json --replay` |

## 最新提交zip

- 文件：`B3太阳活动周AI_Scientist提交包_20260714_195652.zip`
- SHA256：`5e8ea4051ce41c8f948e0c07369edf79873a34a99a444db3db841c44b498ebe5`
- ready：`True`
- 文件数：`181`
