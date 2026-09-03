from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from starlette.testclient import TestClient

from jw.langgraph_dev import reproduction_api
from jw.langgraph_dev.http import app
from jw.config import JWConfig
from jw.reproduction.service import launch_solar_h1_h2
from jw.reproduction.suite import CASES, H1_PROMPT, H2_PROMPT


EXPECTED_H1 = (
    "“请完成一次范围受控、独立且可复核的 SILSO 太阳活动周形态实验。”核心要求是：使用 SILSO v2.0 "
    "第 1—24 周，分析周期长度、上升时间—峰值、下降时间—峰值及早期/现代稳定性；使用官方极值表；"
    "计算 Pearson、Spearman、bootstrap、逐周期留一；生成 CSV、报告和散点图；不得把相关性写成因果机制。"
)
EXPECTED_H2 = (
    "实际任务文本为：“检验极小期极区场对下一太阳活动周峰值的历史预测技能。”随后固定为：使用已登记的极区前兆表"
    "和回执；前五个周期训练，逐步留出第 20—24 周；拟合极区场线性模型；与训练均值、持续性基线比较；"
    "固定种子 `20260828`，进行 10,000 次活动周级 bootstrap；检查时间泄漏及 MWO/WSO 测量制度差异。"
)


class FakeThreads:
    def __init__(self, owner):
        self.owner = owner
        self.deleted = []

    async def create(self, *, graph_id, metadata):
        case_id = metadata["reproduction_case"]
        self.owner.events.append(("thread-start", case_id))
        await asyncio.sleep(0)
        if case_id in self.owner.fail_cases:
            raise RuntimeError(f"{case_id} thread failed")
        self.owner.events.append(("thread-finish", case_id))
        return {"thread_id": f"thread-{case_id.lower()}"}

    async def delete(self, thread_id):
        self.deleted.append(thread_id)


class FakeRuns:
    def __init__(self, owner):
        self.owner = owner

    async def create(self, thread_id, assistant_id, *, input, metadata, config):
        case_id = metadata["case_id"]
        self.owner.run_calls.append(
            {
                "thread_id": thread_id,
                "assistant_id": assistant_id,
                "input": input,
                "metadata": metadata,
                "config": config,
            }
        )
        return {"run_id": f"run-{case_id.lower()}"}


class FakeClient:
    def __init__(self, fail_cases=()):
        self.fail_cases = set(fail_cases)
        self.events = []
        self.run_calls = []
        self.threads = FakeThreads(self)
        self.runs = FakeRuns(self)


def test_prompt_text_and_hashes_are_versioned_exactly():
    assert H1_PROMPT == EXPECTED_H1
    assert H2_PROMPT == EXPECTED_H2
    assert [case.prompt_sha256 for case in CASES] == [
        "e8278c7cd2d98bbca2254961458ecfaa099616681ff6b364c1fc3881d804db4d",
        "909c12a5f8b827fb0f0f12cf714a16eae06b8247e610bcb10cc945f5421abfba",
    ]


def test_launch_creates_two_isolated_fixed_runs_and_receipts(tmp_path):
    client = FakeClient()
    result = asyncio.run(
        launch_solar_h1_h2(
            trigger="webui",
            base_workspace=tmp_path,
            client=client,
            launched_at="2026-09-03T10:00:00Z",
            batch_id="batch-test",
        )
    )

    assert result["status"] == "submitted"
    assert {run["thread_id"] for run in result["runs"]} == {"thread-h1", "thread-h2"}
    assert len({run["workspace"] for run in result["runs"]}) == 2
    assert client.events[:2] == [("thread-start", "H1"), ("thread-start", "H2")]
    assert {call["assistant_id"] for call in client.run_calls} == {"JW-reproduction"}
    for call in client.run_calls:
        case = next(
            item for item in CASES if item.case_id == call["metadata"]["case_id"]
        )
        assert call["input"] == {"messages": [{"role": "user", "content": case.prompt}]}
        configurable = call["config"]["configurable"]
        assert configurable["model"] == "qwen3.7-max"
        assert configurable["model_provider"] == "dashscope"
        assert configurable["project_id"] == "default"
        assert configurable["enable_ask_user"] is False
        assert configurable["auto_approve"] is True

    for run in result["runs"]:
        task = json.loads(
            (Path(run["workspace"]) / "task.json").read_text(encoding="utf-8")
        )
        assert task["schema_version"] == 2
        assert task["status"] == "active"
        assert task["run_id"].startswith("run_")
        assert task["reproduction_launch"]["langgraph_run_id"] == run["run_id"]
        receipt = json.loads(
            (
                Path(run["workspace"]) / "receipts" / "reproduction_launch.json"
            ).read_text(encoding="utf-8")
        )
        assert receipt["prompt"] in {EXPECTED_H1, EXPECTED_H2}
        assert receipt["langgraph_run_id"] == run["run_id"]
        assert receipt["claim_boundary"].startswith("This receipt proves dispatch only")
    batch_receipt = (
        tmp_path
        / "projects"
        / "default"
        / "shared"
        / "decisions"
        / "reproduction_batches"
        / "batch-test.json"
    )
    assert (
        json.loads(batch_receipt.read_text(encoding="utf-8"))["status"] == "submitted"
    )


def test_launch_preserves_single_success_as_partial(tmp_path):
    client = FakeClient(fail_cases={"H2"})
    result = asyncio.run(
        launch_solar_h1_h2(
            trigger="cli", base_workspace=tmp_path, client=client, batch_id="partial"
        )
    )
    assert result["status"] == "partial"
    assert [run["case_id"] for run in result["runs"]] == ["H1"]
    assert result["errors"][0]["case_id"] == "H2"


def test_launch_reports_all_failure_and_audit_failure(tmp_path):
    failed = asyncio.run(
        launch_solar_h1_h2(
            trigger="cli",
            base_workspace=tmp_path,
            client=FakeClient(fail_cases={"H1", "H2"}),
            batch_id="failed",
        )
    )
    assert failed["status"] == "failed"
    assert failed["runs"] == []

    writes = 0

    def flaky_writer(path, payload):
        nonlocal writes
        writes += 1
        if path.name == "reproduction_launch.json":
            raise OSError("audit unavailable")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    audited = asyncio.run(
        launch_solar_h1_h2(
            trigger="cli",
            base_workspace=tmp_path,
            client=FakeClient(),
            writer=flaky_writer,
            batch_id="audit-failed",
        )
    )
    assert writes >= 5
    assert audited["status"] == "partial"
    assert len(audited["runs"]) == 2
    assert {item["stage"] for item in audited["errors"]} == {"audit"}


def test_http_route_enforces_origin_intent_key_and_dangerous_mode(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(reproduction_api.paths, "WORKSPACE_ROOT", tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/reproductions/solar-h1-h2",
        headers={
            "Origin": "https://evil.example",
            "X-JW-Reproduction-Intent": "solar-h1-h2-v1",
        },
        json={"trigger": "webui"},
    )
    assert response.status_code == 403
    assert (
        client.post(
            "/api/reproductions/solar-h1-h2", json={"trigger": "cli"}
        ).status_code
        == 400
    )

    monkeypatch.setattr(
        reproduction_api,
        "get_effective_config",
        lambda: SimpleNamespace(dangerous_mode=False, dashscope_api_key=""),
    )
    headers = {"X-JW-Reproduction-Intent": "solar-h1-h2-v1"}
    assert (
        client.post(
            "/api/reproductions/solar-h1-h2", headers=headers, json={"trigger": "cli"}
        ).status_code
        == 503
    )

    monkeypatch.setattr(
        reproduction_api,
        "get_effective_config",
        lambda: SimpleNamespace(dangerous_mode=True, dashscope_api_key="key"),
    )
    assert (
        client.post(
            "/api/reproductions/solar-h1-h2", headers=headers, json={"trigger": "cli"}
        ).status_code
        == 403
    )


def test_http_route_returns_201_and_207(monkeypatch, tmp_path):
    monkeypatch.setattr(reproduction_api.paths, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(
        reproduction_api,
        "get_effective_config",
        lambda: SimpleNamespace(dangerous_mode=False, dashscope_api_key="key"),
    )

    async def fake_launch(**kwargs):
        assert kwargs["trigger"] == "webui"
        return {
            "schema_version": "jw-reproduction-launch-v1",
            "suite_id": "solar-h1-h2-v1",
            "batch_id": "batch",
            "status": fake_launch.status,
            "model": {"name": "qwen3.7-max", "provider": "dashscope"},
            "runs": [],
            "errors": [],
        }

    fake_launch.status = "submitted"
    monkeypatch.setattr(reproduction_api, "launch_solar_h1_h2", fake_launch)
    client = TestClient(app)
    headers = {
        "Origin": "http://localhost:4716",
        "X-JW-Reproduction-Intent": "solar-h1-h2-v1",
    }
    assert (
        client.post(
            "/api/reproductions/solar-h1-h2", headers=headers, json={"trigger": "webui"}
        ).status_code
        == 201
    )
    fake_launch.status = "partial"
    assert (
        client.post(
            "/api/reproductions/solar-h1-h2", headers=headers, json={"trigger": "webui"}
        ).status_code
        == 207
    )


def test_reproduction_graph_forces_restricted_auto_config(monkeypatch):
    import deepagents
    import jw.agent as agent
    import jw.llm as llm

    original = JWConfig(
        model="other-model",
        provider="openai",
        dangerous_mode=True,
        auto_approve=True,
        enable_ask_user=True,
    )
    captured = {}

    class FakeGraph:
        def with_config(self, config):
            captured["graph_config"] = config
            return self

    monkeypatch.setenv("JW_DEPLOY_MODE", "stripped")
    monkeypatch.setattr(agent, "_ensure_config", lambda: original)
    monkeypatch.setattr(llm, "get_chat_model", lambda **kwargs: "fixed-model")
    monkeypatch.setattr(agent, "_get_scoped_backend_factory", lambda: "backend")

    def middleware(**kwargs):
        captured["cfg"] = kwargs["cfg"]
        return ["middleware"]

    monkeypatch.setattr(agent, "_get_default_middleware", middleware)
    monkeypatch.setattr(
        agent,
        "_build_base_kwargs",
        lambda backend, middleware, **kwargs: {
            "name": "JW",
            "model": "fixed-model",
            "middleware": middleware,
            "skills": [],
        },
    )
    monkeypatch.setattr(deepagents, "create_deep_agent", lambda **kwargs: FakeGraph())

    assert agent.create_reproduction_agent().__class__ is FakeGraph
    cfg = captured["cfg"]
    assert (cfg.model, cfg.provider) == ("qwen3.7-max", "dashscope")
    assert cfg.auto_mode is True
    assert cfg.auto_approve is True
    assert cfg.enable_ask_user is False
    assert cfg.dangerous_mode is False
    assert original.dangerous_mode is True
