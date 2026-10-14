"""One explicit frontend request -> bounded acquisition, native review and report."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from jw.flare_forecast.assets import child, sha
from jw.flare_forecast.chat import bounded_tool_model, is_live_request, latest_request
from jw.flare_forecast.engine import PACKAGE
from jw.flare_forecast.model import load_forest
from jw.flare_forecast.native_data import review_live, write
from jw.flare_forecast.report import build_report
from jw.flare_forecast.workflow import fetch_live, issue, read
from jw.workspaces import workspace_root_from_config

from .registry import register_tool_bundle


def assets_root():
    configured = os.getenv("JW_FLARE_ASSETS_DIR")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else PACKAGE.parent.parent / "workspace/flare_forecast/assets"
    )


def turn_id(messages):
    text, message = latest_request(messages)
    if message is None or not is_live_request(text):
        raise ValueError("Explicit current 24-hour solar flare request required")
    # Human ids survive graph retries. Count distinguishes equal new turns on CLI.
    identity = (
        getattr(message, "id", None)
        or f"{sum(getattr(m, 'type', '') == 'human' for m in messages)}:{text}"
    )
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:24]


def verified_delivery(root: Path, receipt: dict):
    for row in receipt.get("artifacts", []):
        path = child(root, row["path"])
        if sha(path) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("Forecast delivery bytes changed")
    return receipt


def reviewer_model(config):
    from jw.config import get_effective_config
    from jw.llm import get_chat_model

    cfg = get_effective_config()
    overrides = config.get("configurable", {})
    if overrides.get("model"):
        from jw.llm.models import validate_model_override

        model, provider = validate_model_override(
            overrides["model"],
            overrides.get("model_provider") or overrides.get("provider"),
        )
    else:
        model, provider = cfg.model, cfg.provider
    return bounded_tool_model(
        get_chat_model(model=model, provider=provider, max_retries=0, timeout=150)
    )


def execute_forecast(root: Path, messages, call_id: str, model_factory, assets: Path):
    key = turn_id(messages)
    receipt_path = child(root, f"receipts/flare_forecast/{key}.json")
    if receipt_path.exists():
        return verified_delivery(root, read(receipt_path))
    work = child(root, f"work/flare_forecast/{key}")
    outputs = child(root, f"outputs/flare_forecast/{key}")
    work.parent.mkdir(parents=True, exist_ok=True)
    try:
        work.mkdir(exist_ok=False)
    except FileExistsError:
        interrupted = {
            "status": "attempt_in_progress_or_interrupted",
            "forecast_issued": (work / "issued/issued_forecast.json").exists(),
            "message": "本请求已开始，未再次获取或签发；请查看当前任务回执。",
            "artifacts": [],
        }
        if interrupted["forecast_issued"]:
            interrupted["forecast_path"] = (
                (work / "issued/issued_forecast.json").relative_to(root).as_posix()
            )
            interrupted["message"] = (
                "本请求已签发，但流程中断；保留原始签发结果，未再次获取或签发。"
            )
        return interrupted
    result = {
        "schema_version": "jw-flare-chat-delivery-v1",
        "attempt_id": key,
        "native_main_tool_call_id": call_id,
        "requested_utc": datetime.now(timezone.utc).isoformat(),
        "forecast_issued": False,
        "future_truth_read": False,
        "no_fit_or_recalibration": True,
        "status": "running",
        "artifacts": [],
    }
    issued = None
    stage = "model_assets"
    try:
        if not call_id:
            raise ValueError("Actual native main tool-call ID required")
        # Verify the model before any HTTP. Never implicitly rebuild missing assets.
        load_forest(assets)
        write(
            work / "registered_request.json",
            {
                **result,
                "model_profile": "frozen_SHARP_available24_20261013",
                "source_assets": str(assets),
                "HTTP_calls_limit": 2,
                "HTTP_retries": 0,
                "Data_model_calls_limit": 4,
                "Data_tool_calls_limit": 2,
                "Data_active_seconds_limit": 600,
            },
        )
        live_dir = work / "live"
        stage = "live_source"
        acquired = fetch_live(assets, live_dir)
        if acquired["status"] != "eligible":
            reason = read(live_dir / "live_qualification.json").get(
                "reason", "no_eligible_input"
            )
            raise ValueError("LIVE_INPUT_NOT_ELIGIBLE: " + str(reason))
        contract = Path(acquired["contract"])
        stage = "native_Data"
        review = review_live(live_dir, contract, work / "native_data", model_factory())
        review_result = read(review)
        result["data_review_decision"] = review_result["decision"]
        result["data_review_path"] = review.relative_to(root).as_posix()
        result["data_review_reasons"] = review_result.get("assessment", {}).get(
            "issues", []
        )
        if review_result["decision"] != "accept_with_limits":
            raise ValueError("CURRENT_NATIVE_DATA_REVIEW_NOT_ACCEPTED")
        stage = "issuance"
        issued = issue(assets, live_dir, contract, review, work / "issued")
        result["forecast_issued"] = True
        result["issued_utc"] = issued["issued_utc"]
        result["prediction_window"] = issued["prediction_window"]
        write(
            work / "native_execution_receipt.json",
            {
                "tool": "forecast_flare_24h",
                "actual_tool_call_id": call_id,
                "issued_forecast_sha256": sha(work / "issued/issued_forecast.json"),
                "live_manifest_sha256": issued["live_manifest_sha256"],
                "native_Data_review_sha256": issued["native_Data_review_sha256"],
                "execution": "real JW main-agent tool with independent bounded original solar-data review",
            },
        )
        stage = "report"
        build_report(issued, assets, outputs)
        write(outputs / "issued_forecast.json", issued)
        result["status"] = "completed"
        result["report_path"] = (outputs / "report.md").relative_to(root).as_posix()
        result["html_path"] = (outputs / "report.html").relative_to(root).as_posix()
        result["forecast_path"] = (
            (outputs / "issued_forecast.json").relative_to(root).as_posix()
        )
        result["probabilities"] = issued["probabilities"]
    except Exception as exc:
        result["failure_stage"] = stage
        result["status"] = "issued_report_failed" if issued else "not_issued"
        # Raw exceptions stay in the task audit, not the reader-facing response.
        write(
            work / "failure.json",
            {
                "error_class": type(exc).__name__,
                "detail": str(exc),
                "forecast_issued": bool(issued),
                "no_retry": True,
            },
        )
        outputs.mkdir(parents=True, exist_ok=True)
        if issued:
            if not (outputs / "issued_forecast.json").exists():
                write(outputs / "issued_forecast.json", issued)
            result["forecast_path"] = (
                (outputs / "issued_forecast.json").relative_to(root).as_posix()
            )
        result["message"] = (
            "本次预报已经签发，报告生成失败；保留原签发结果，不重复签发。"
            if issued
            else "本次未签发预报：输入、模型或原生Data审阅未通过。没有复用旧概率，请查看失败回执。"
        )
        if not issued:
            reason = {
                "model_assets": "预测模型或必要输入未通过校验",
                "live_source": "未取得最新合格磁场输入",
                "native_Data": "本次新输入的原生Data审阅未完成或未接受",
                "issuance": "签发前输入时效或完整性校验未通过",
            }.get(stage, "流程未完成")
            result["message"] = (
                f"本次未签发预报：{reason}。没有复用旧概率，请查看失败回执。"
            )
        result["failure_path"] = (work / "failure.json").relative_to(root).as_posix()
    for base in (work, outputs):
        if base.exists():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    path = child(root, path.relative_to(root).as_posix())
                    result["artifacts"].append(
                        {
                            "path": path.relative_to(root).as_posix(),
                            "bytes": path.stat().st_size,
                            "sha256": sha(path),
                        }
                    )
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    write(receipt_path, result)
    return result


def render_delivery(result):
    if result.get("status") != "completed":
        text = result.get("message", "本次请求已登记，未重复获取或签发。")
        if result.get("data_review_decision"):
            text += f"\n\nJW Data原生裁决：`{result['data_review_decision']}`。"
            for reason in result.get("data_review_reasons", []):
                text += "\n\n- " + str(reason)
        for key in ("forecast_path", "failure_path", "data_review_path"):
            if result.get(key):
                text += f"\n\n`{result[key]}`"
        return text
    from jw.flare_forecast.report import local_time

    window = result["prediction_window"]
    p = result["probabilities"]
    return f"未来24小时实验预报已签发。\n\n北京时间：**{local_time(window['start'])} 至 {local_time(window['end'])}**。\n\nC1.0+概率 **{p['C1.0+']:.2%}**，M1.0+概率 **{p['M1.0+']:.2%}**。概率不是正确率，未来真实爆发情况仍未知。C级历史技能未获支持，M级仅有受限历史技能支持。\n\n阅读报告：`{result['report_path']}`\n\n可下载的完整图文报告：`{result['html_path']}`\n\n原始签发结果：`{result['forecast_path']}`"


@tool
def forecast_flare_24h(runtime: ToolRuntime) -> str:
    """For an explicit user request, fetch the latest qualified SHARP input, obtain a real original solar-data review, issue with the frozen model and deliver a report. No fit, retries, future truth, or reuse of old forecasts. Takes no user-provided paths or probabilities."""
    try:
        root = workspace_root_from_config(runtime.config)
        value = execute_forecast(
            root,
            runtime.state.get("messages", []),
            runtime.tool_call_id,
            lambda: reviewer_model(runtime.config),
            assets_root(),
        )
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return json.dumps(
            {
                "status": "not_issued",
                "forecast_issued": False,
                "message": "当前任务或预测资产未正确绑定，本次未签发；请检查部署配置。",
            },
            ensure_ascii=False,
        )


register_tool_bundle("flare-forecast", [forecast_flare_24h])
