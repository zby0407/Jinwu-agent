from __future__ import annotations

import json
import os
from pathlib import Path
from textwrap import dedent
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"


class BailianLLMError(RuntimeError):
    """Raised when the Bailian LLM client cannot produce a usable response."""


def load_bailian_config() -> dict[str, Any]:
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise BailianLLMError(
            "Missing dependency python-dotenv. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    load_dotenv(ROOT / ".env")

    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise BailianLLMError("Missing DASHSCOPE_API_KEY in environment or .env")

    timeout_raw = os.getenv("BAILIAN_TIMEOUT_SECONDS", "60").strip()
    try:
        timeout_seconds = float(timeout_raw)
    except ValueError as exc:
        raise BailianLLMError(f"Invalid BAILIAN_TIMEOUT_SECONDS: {timeout_raw!r}") from exc

    return {
        "api_key": api_key,
        "base_url": os.getenv("BAILIAN_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        "model": os.getenv("BAILIAN_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "timeout_seconds": timeout_seconds,
        "trust_env": os.getenv("BAILIAN_TRUST_ENV", "false").strip().lower()
        in {"1", "true", "yes", "y", "on"},
    }


def build_system_prompt() -> str:
    return dedent(
        """
        You are the experiment-summary LLM inside the Solar-Cycle Co-Scientist data feature agent.
        Use only the provided JSON payload. Do not invent files, fields, observations, experiments,
        metrics, or scientific conclusions.

        Safety rules:
        - Do not modify, fabricate, interpolate, or overwrite observed data.
        - Do not treat any next_cycle_* field as a model input.
        - Do not treat GOES XRS flare data as long-term primary solar-cycle evidence.
        - Do not describe F10.7 or sunspot number as the Sun's internal magnetic field.
        - Describe 1940-1991 hemispheric data as RGO/NOAA external calibrated observation.
        - Explicitly mention coverage limits, missingness, quality flags, auxiliary proxies,
          evidence tier, leakage risks, and recommended experiment splits.

        Return Markdown with these exact section headings:
        # Bailian Experiment Summary
        ## Data Readiness
        ## Recommended Experiments
        ## Feature Use
        ## Forbidden Inputs
        ## Risk Flags
        ## Suggested Next Agent Actions
        """
    ).strip()


def build_user_prompt(payload: dict[str, Any]) -> str:
    return (
        "Summarize this structured handoff for a downstream experiment agent.\n\n"
        "JSON payload:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def generate_experiment_summary(payload: dict[str, Any]) -> str:
    return call_bailian(build_system_prompt(), build_user_prompt(payload))


def build_agent_answer_system_prompt() -> str:
    return dedent(
        """
        You are an interactive Q&A agent for the Solar-Cycle Co-Scientist data feature workflow.
        Answer the user's question using only the provided project context. If the context is
        insufficient, say what is missing and which local output file should be checked next.

        Safety rules:
        - Do not invent observations, files, fields, metrics, functions, tool names, or experiment results.
        - Base every explanation on the actual tool traces and outputs provided in the context.
        - If a tool failed, quote the actual error type and message from the tool trace.
        - Do not modify, fabricate, interpolate, or overwrite observed data.
        - Do not treat any next_cycle_* field as a model input.
        - Do not treat GOES XRS flare data as long-term primary solar-cycle evidence.
        - Do not describe F10.7 or sunspot number as the Sun's internal magnetic field.
        - Describe 1940-1991 hemispheric data as RGO/NOAA external calibrated observation.

        Answer in Chinese unless the user asks otherwise. Be concise, concrete, and include
        relevant file paths when useful.
        """
    ).strip()


def build_agent_answer_user_prompt(payload: dict[str, Any], question: str) -> str:
    return (
        f"User question:\n{question.strip()}\n\n"
        "Project context JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def generate_agent_answer(payload: dict[str, Any], question: str) -> str:
    return call_bailian(
        build_agent_answer_system_prompt(),
        build_agent_answer_user_prompt(payload, question),
    )


def create_bailian_tool_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> Any:
    """Return one Bailian assistant message, including any requested tool calls."""
    config = load_bailian_config()
    try:
        from openai import OpenAI
        import httpx
    except ImportError as exc:
        raise BailianLLMError(
            "Missing dependency openai/httpx. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    http_client = httpx.Client(
        timeout=config["timeout_seconds"],
        trust_env=config["trust_env"],
    )
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
        http_client=http_client,
    )
    try:
        try:
            response = client.chat.completions.create(
                model=config["model"],
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as exc:
            raise BailianLLMError(f"Bailian tool-calling request failed: {exc}") from exc
    finally:
        http_client.close()
    if not response.choices:
        raise BailianLLMError("Bailian returned no choices")
    return response.choices[0].message


def call_bailian(system_prompt: str, user_prompt: str) -> str:
    config = load_bailian_config()

    try:
        from openai import OpenAI
        import httpx
    except ImportError as exc:
        raise BailianLLMError(
            "Missing dependency openai/httpx. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    http_client = httpx.Client(
        timeout=config["timeout_seconds"],
        trust_env=config["trust_env"],
    )
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
        http_client=http_client,
    )

    try:
        try:
            response = client.chat.completions.create(
                model=config["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
        except Exception as exc:
            raise BailianLLMError(f"Bailian LLM call failed: {exc}") from exc
    finally:
        http_client.close()

    content = response.choices[0].message.content if response.choices else None
    if not content or not content.strip():
        raise BailianLLMError("Bailian LLM returned an empty response")
    return content.strip() + "\n"
