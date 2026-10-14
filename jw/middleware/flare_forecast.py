"""Make current 24-hour flare requests execute once and return the verified delivery."""

import json

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelResponse,
    hook_config,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_config

from jw.flare_forecast.chat import bounded_tool_model, is_live_request, latest_request
from jw.tools.flare_forecast import (
    forecast_flare_24h,
    render_delivery,
    verified_delivery,
    turn_id,
)
from jw.flare_forecast.assets import child
from jw.flare_forecast.workflow import read
from jw.workspaces import workspace_root_from_config

from .utils import append_to_system_message


class FlareForecastMiddleware(AgentMiddleware):
    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        text, latest = latest_request(state.get("messages", []))
        if not is_live_request(text):
            return None
        messages = state["messages"]
        following = messages[messages.index(latest) + 1 :]
        for m in reversed(following):
            if isinstance(m, ToolMessage) and m.name == forecast_flare_24h.name:
                try:
                    result = json.loads(m.content)
                    config = getattr(runtime, "config", None) or get_config()
                    root = workspace_root_from_config(config)
                    if result.get("status") == "completed":
                        key = turn_id(messages)
                        canonical = read(
                            child(root, f"receipts/flare_forecast/{key}.json")
                        )
                        actual_calls = {
                            call["id"]
                            for msg in following
                            if isinstance(msg, AIMessage)
                            for call in msg.tool_calls
                            if call["name"] == forecast_flare_24h.name
                        }
                        if result != canonical or m.tool_call_id not in actual_calls:
                            raise ValueError(
                                "Tool response differs from this actual turn's stored delivery"
                            )
                    verified_delivery(root, result)
                    answer = render_delivery(result)
                except Exception:
                    answer = "预报交付回执未通过核验，本次未返回未经核验的概率。请检查当前任务回执。"
                return {"messages": [AIMessage(content=answer)], "jump_to": "end"}
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        import asyncio

        return await asyncio.to_thread(self.before_model, state, runtime)

    def _prepare(self, request):
        text, _ = latest_request(request.messages)
        if not is_live_request(text):
            return request
        prompt = "用户明确请求当前未来24小时耀斑预报。现在只调用 forecast_flare_24h 一次，不询问已登记的窗口或模型，不自行下载、训练、绘图或编造结果。该工具负责新输入、真实Data审阅、签发及报告；失败必须如实返回。"
        model = bounded_tool_model(request.model)
        settings = dict(request.model_settings)
        settings["parallel_tool_calls"] = False
        if getattr(model, "extra_body", None):
            body = dict(settings.get("extra_body") or {})
            body.update(model.extra_body)
            if body.get("enable_thinking") is False:
                body.pop("thinking_budget", None)
                body.pop("preserve_thinking", None)
            settings["extra_body"] = body
        settings.pop("reasoning_effort", None)
        return request.override(
            model=model,
            model_settings=settings,
            tools=[forecast_flare_24h],
            tool_choice=forecast_flare_24h.name,
            system_message=append_to_system_message(request.system_message, prompt),
        )

    def wrap_model_call(self, request, handler):
        response = handler(self._prepare(request))
        return self._checked_response(request, response)

    async def awrap_model_call(self, request, handler):
        response = await handler(self._prepare(request))
        return self._checked_response(request, response)

    def _checked_response(self, request, response):
        if not is_live_request(latest_request(request.messages)[0]):
            return response
        calls = [
            call
            for m in response.result
            if isinstance(m, AIMessage)
            for call in m.tool_calls
        ]
        if len(calls) != 1 or calls[0]["name"] != forecast_flare_24h.name:
            return ModelResponse(
                result=[
                    AIMessage(
                        content="模型未发起唯一的预报工具调用，本次未获取数据或签发预报。"
                    )
                ]
            )
        return response
