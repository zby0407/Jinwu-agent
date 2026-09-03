"""CLI entry point for the fixed H1/H2 reproduction suite."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Annotated

import httpx
import typer

from jw import paths
from jw.config import apply_config_to_env, get_effective_config
from jw.langgraph_dev.sdk import (
    get_langgraph_sync_client,
    langgraph_dev_headers,
    langgraph_dev_url,
)
from jw.reproduction.suite import SUITE_ID

from ._app import app

_TERMINAL_STATUSES = frozenset({"success", "error", "timeout", "interrupted"})


def _workspace_for(config: object, workdir: Path | None) -> Path:
    raw = workdir or getattr(config, "default_workdir", None) or paths.WORKSPACE_ROOT
    workspace = Path(raw).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"工作区不存在或不是目录：{workspace}")
    return workspace


def _verify_running_workspace(workspace: Path) -> None:
    from jw.langgraph_dev.manager import _read_workspace_sidecar

    sidecar = _read_workspace_sidecar()
    if sidecar is None:
        raise ValueError(
            "已有后端缺少工作区标识，无法安全确认 --workdir 一致性；请先停止该后端"
        )
    served = Path(str(sidecar["workspace"])).expanduser().resolve()
    if served != workspace:
        raise ValueError(f"已有后端工作区为 {served}，与请求的 {workspace} 不一致")


def _submit(base_url: str) -> tuple[int, dict[str, Any]]:
    headers = langgraph_dev_headers()
    headers["X-JW-Reproduction-Intent"] = SUITE_ID
    response = httpx.post(
        f"{base_url}/api/reproductions/solar-h1-h2",
        headers=headers,
        json={"trigger": "cli"},
        timeout=60.0,
    )
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"后端返回了非 JSON 响应（HTTP {response.status_code}）"
        ) from exc
    if response.status_code == 404:
        raise RuntimeError("当前后端不支持 reproduce 接口，请更新并重启后端")
    if response.status_code not in {201, 207}:
        message = body.get("error") if isinstance(body, dict) else None
        raise RuntimeError(message or f"复现调度失败（HTTP {response.status_code}）")
    if not isinstance(body, dict):
        raise RuntimeError("复现接口响应格式无效")
    return response.status_code, body


def _print_dispatch(body: dict[str, Any]) -> None:
    typer.echo(f"批次：{body.get('batch_id', '<unknown>')}")
    typer.echo(f"调度状态：{body.get('status', '<unknown>')}")
    for run in body.get("runs", []):
        typer.echo(
            f"{run.get('case_id')}: threadId={run.get('thread_id')} "
            f"runId={run.get('run_id')} workspace={run.get('workspace')}"
        )
    for error in body.get("errors", []):
        typer.echo(
            f"错误[{error.get('case_id', '?')}/{error.get('stage', '?')}]："
            f"{error.get('message', 'unknown error')}",
            err=True,
        )
    typer.echo(
        "说明：提交成功仅证明两篇固定提示词进入独立 run，不代表实验或科研审查成功。"
    )


def _wait_for_terminal(base_url: str, runs: list[dict[str, Any]]) -> None:
    client = get_langgraph_sync_client(url=base_url)
    remaining = {str(item["case_id"]): item for item in runs}
    last_status: dict[str, str] = {}
    while remaining:
        for case_id, item in list(remaining.items()):
            run = client.runs.get(
                thread_id=str(item["thread_id"]),
                run_id=str(item["run_id"]),
            )
            status = str(run.get("status", "unknown")).lower()
            if last_status.get(case_id) != status:
                typer.echo(f"{case_id} LangGraph 状态：{status}")
                last_status[case_id] = status
            if status in _TERMINAL_STATUSES:
                remaining.pop(case_id)
        if remaining:
            time.sleep(2.0)
    typer.echo("LangGraph 终态已齐备；终态不自动等于科研成功。")
    typer.echo(
        "科研审查状态：请分别打开 H1/H2 会话，核对审查记录、终止文字和真实产物文件。"
    )


@app.command()
def reproduce(
    workdir: Annotated[
        Path | None,
        typer.Option("--workdir", help="复现任务使用的基础工作区"),
    ] = None,
    detach: Annotated[
        bool,
        typer.Option("--detach", help="复用已有外部后端，提交后立即退出"),
    ] = False,
) -> None:
    """并发提交固定的 solar-h1-h2-v1 两轮一次性复现任务。"""
    from jw.langgraph_dev.manager import (
        _is_port_occupied,
        is_langgraph_dev_running,
        start_langgraph_dev,
        stop_langgraph_dev,
    )

    config = get_effective_config()
    apply_config_to_env(config)
    if not str(config.dashscope_api_key or "").strip():
        typer.echo(
            "错误：缺少 DASHSCOPE_API_KEY，无法使用固定 DashScope 模型。", err=True
        )
        raise typer.Exit(2)
    if config.dangerous_mode:
        typer.echo("错误：dangerous_mode 已启用，一次性复现接口拒绝执行。", err=True)
        raise typer.Exit(2)
    try:
        workspace = _workspace_for(config, workdir)
    except ValueError as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(2) from exc

    port = int(config.langgraph_dev_port)
    base_url = langgraph_dev_url(port=port)
    running = is_langgraph_dev_running(port=port)
    if detach and not running:
        typer.echo("错误：--detach 仅允许复用已运行的外部后端。", err=True)
        raise typer.Exit(2)
    if _is_port_occupied(port) and not running:
        typer.echo(f"错误：端口 {port} 被非 JW 后端占用。", err=True)
        raise typer.Exit(2)

    started_process = None
    try:
        if running:
            try:
                _verify_running_workspace(workspace)
            except ValueError as exc:
                typer.echo(f"错误：{exc}", err=True)
                raise typer.Exit(2) from exc
            typer.echo(f"复用后端：{base_url}")
        else:
            typer.echo(f"正在启动完整后端：{base_url}")
            started_process = start_langgraph_dev(
                workspace_dir=workspace,
                port=port,
                file_persistence=bool(config.langgraph_dev_file_persistence),
                jobs_per_worker=int(config.langgraph_dev_jobs_per_worker),
                deploy_mode=True,
            )

        try:
            status_code, body = _submit(base_url)
        except (httpx.HTTPError, RuntimeError) as exc:
            typer.echo(f"错误：{exc}", err=True)
            raise typer.Exit(2) from exc
        _print_dispatch(body)
        runs = body.get("runs", [])
        if status_code != 201 or body.get("status") != "submitted" or len(runs) != 2:
            raise typer.Exit(2)
        if detach:
            typer.echo("已按 --detach 提交；外部后端将继续运行。")
            return
        try:
            _wait_for_terminal(base_url, runs)
        except KeyboardInterrupt as exc:
            typer.echo(
                "监测已由 Ctrl+C 中断；已提交任务的状态请在 WebUI 中继续核对。",
                err=True,
            )
            raise typer.Exit(130) from exc
    finally:
        if started_process is not None:
            stop_langgraph_dev(started_process)
