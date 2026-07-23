#!/usr/bin/env python3
"""Pi 科学假设 Agent 工具的内部确定性桥接。

本模块不是产品入口，也不是模型入口。Pi 拥有 Agent 循环、模型提供方、
Prompt、Skills、工具生命周期和会话状态；扩展只把合同检查、证据登记和
保存这类更适合确定性实现的工作交给这个桥接。证据登记簿驻留在每次
Pi 工具调用产生的桥接进程内；由于每个检查与保存调用都会携带当前
已绑定证据的完整清单，桥接可以无状态运行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scientific_hypothesis.contracts import (  # noqa: E402
    ContractError,
    validate_hypothesis_request,
)
from scientific_hypothesis.harness import (  # noqa: E402
    EvidenceRegister,
    build_hypothesis_brief,
    build_natural_hypothesis_request,
    freeze_hypothesis_portfolio,
    preflight_hypothesis_ranking,
    preflight_hypothesis_response,
)
from scientific_hypothesis.upstream import inspect_experiment_run  # noqa: E402


def read_stdin_object() -> dict[str, Any]:
    try:
        value = json.load(sys.stdin)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("stdin 必须包含一个 UTF-8 JSON 对象") from exc
    if not isinstance(value, dict):
        raise ContractError("stdin 必须包含一个 JSON 对象")
    return value


def _register_from(payload: dict[str, Any]) -> EvidenceRegister:
    register = EvidenceRegister()
    for entry in payload.get("evidence_register", []):
        register.bind(entry)
    return register


def run_pi_bridge(mode: str) -> int:
    try:
        payload = read_stdin_object()
        if mode == "brief":
            if "request" in payload:
                request = validate_hypothesis_request(payload.get("request"))
            else:
                request = build_natural_hypothesis_request(payload.get("research_question"))
            result = build_hypothesis_brief(request)
        elif mode == "bind-evidence":
            register = _register_from(payload)
            result = register.bind(payload.get("bind"))
        elif mode == "inspect-upstream":
            result = inspect_experiment_run(payload, ROOT)
        else:
            request = validate_hypothesis_request(payload.get("request"))
            response = payload.get("response")
            if not isinstance(response, dict):
                raise ContractError("response 必须是一个 JSON 对象")
            register = _register_from(payload)
            if mode == "preflight":
                result = preflight_hypothesis_response(
                    request, response, register, include_validated_response=True
                )
            elif mode == "ranking-preflight":
                result = preflight_hypothesis_ranking(
                    request,
                    response,
                    payload.get("ranking"),
                    register,
                    include_validated_ranking=True,
                )
            elif mode == "freeze":
                result = freeze_hypothesis_portfolio(
                    request, response, register, ranking_payload=payload.get("ranking")
                )
                result["mode"] = "pi"
            else:
                raise ContractError(f"不支持的桥接模式：{mode}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ContractError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "scientific-hypothesis-outcome-v1",
                    "status": "needs_revision",
                    "validation_error": str(exc),
                    "user_message": "假设组合仍在内部校正；请保留已正确内容并一次性修正全部列出问题。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi-brief-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-bind-evidence-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-preflight-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-ranking-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-inspect-upstream-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--pi-freeze-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    bridge_modes = (
        (args.pi_brief_stdin, "brief"),
        (args.pi_bind_evidence_stdin, "bind-evidence"),
        (args.pi_preflight_stdin, "preflight"),
        (args.pi_ranking_stdin, "ranking-preflight"),
        (args.pi_inspect_upstream_stdin, "inspect-upstream"),
        (args.pi_freeze_stdin, "freeze"),
    )
    selected = [mode for enabled, mode in bridge_modes if enabled]
    if len(selected) != 1:
        print(
            json.dumps(
                {
                    "schema_version": "scientific-hypothesis-outcome-v1",
                    "status": "pi_required",
                    "error": "本文件是 Pi 工具的内部桥接；请通过 Pi 启动科学假设 Agent。",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    return run_pi_bridge(selected[0])


if __name__ == "__main__":
    raise SystemExit(main())
