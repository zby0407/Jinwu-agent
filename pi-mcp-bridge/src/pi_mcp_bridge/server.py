"""MCP server that exposes Pi coding agent capabilities to JW."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .pi_client import PiClient, PiConfig, PiError, config_from_env, encode_image

logger = logging.getLogger(__name__)

APP_NAME = "pi-mcp-bridge"

IMAGE_PATHS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Optional paths to image files (png, jpg, etc.) to attach to the prompt. "
        "The bridge will base64-encode them and send them to Pi."
    ),
}

TOOLS: list[Tool] = [
    Tool(
        name="pi_code_assist",
        description=(
            "Ask the Pi coding agent to write, refactor, or modify code in the workspace. "
            "Describe what you want in natural language; Pi will perform the edit and report back."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Detailed description of the coding task.",
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of relevant file paths to mention to Pi.",
                },
                "image_paths": IMAGE_PATHS_SCHEMA,
            },
            "required": ["task"],
        },
    ),
    Tool(
        name="pi_review_code",
        description=(
            "Ask Pi to review code for bugs, style issues, performance, or design problems."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Code snippet or file content to review.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional path to the file being reviewed.",
                },
                "focus": {
                    "type": "string",
                    "description": "What to focus on, e.g. 'security', 'performance', 'correctness'.",
                },
                "image_paths": IMAGE_PATHS_SCHEMA,
            },
            "required": ["code"],
        },
    ),
    Tool(
        name="pi_debug",
        description=(
            "Give Pi an error message and context; ask it to diagnose and propose or apply a fix."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "error": {
                    "type": "string",
                    "description": "The error message, traceback, or failure description.",
                },
                "context": {
                    "type": "string",
                    "description": "Additional context: what command was run, relevant files, etc.",
                },
                "file_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of file paths Pi should inspect.",
                },
                "image_paths": IMAGE_PATHS_SCHEMA,
            },
            "required": ["error"],
        },
    ),
    Tool(
        name="pi_explain",
        description="Ask Pi to explain how a piece of code or an error works.",
        inputSchema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Code snippet or concept to explain.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Optional path to the file being explained.",
                },
                "question": {
                    "type": "string",
                    "description": "Specific question about the code.",
                },
                "image_paths": IMAGE_PATHS_SCHEMA,
            },
            "required": ["code"],
        },
    ),
    Tool(
        name="pi_read_file",
        description=(
            "Read a file from the workspace and optionally ask Pi a question about it. "
            "Useful for getting Pi's interpretation of logs, configs, or code without "
            "running bash."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to read.",
                },
                "question": {
                    "type": "string",
                    "description": (
                        "Optional question to ask Pi about the file. If omitted, the raw "
                        "file content is returned."
                    ),
                },
                "image_paths": IMAGE_PATHS_SCHEMA,
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="pi_edit_file",
        description=(
            "Edit a specific file in the workspace via Pi. The bridge reads the file, "
            "sends it to Pi with the requested change, and writes Pi's returned content "
            "back to disk."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to edit.",
                },
                "instruction": {
                    "type": "string",
                    "description": "Precise description of the edit to make.",
                },
                "image_paths": IMAGE_PATHS_SCHEMA,
            },
            "required": ["file_path", "instruction"],
        },
    ),
    Tool(
        name="pi_list_commands",
        description=(
            "List Pi's available extension commands, prompt templates, and skills. "
            "Use this before invoking a skill or template with pi_invoke_command."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="pi_invoke_command",
        description=(
            "Invoke a Pi extension command, prompt template, or skill by name. "
            "Use pi_list_commands first to discover available names."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Command/skill/template name (without the leading /).",
                },
                "message": {
                    "type": "string",
                    "description": "Additional message or context to pass to the command.",
                },
                "image_paths": IMAGE_PATHS_SCHEMA,
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="pi_bash",
        description=(
            "Execute a shell command through Pi's bash tool. Output is added to Pi's context "
            "and returned. Prefer this for commands that Pi itself should reason about; "
            "otherwise use JW's own execute tool."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run.",
                },
            },
            "required": ["command"],
        },
    ),
    Tool(
        name="pi_reset_session",
        description=(
            "Start a fresh Pi session. Use this when context has grown too large or when "
            "switching to an unrelated coding task."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


def _resolve_path(path: str, cwd: str | None) -> Path:
    """Resolve a possibly-relative path against the Pi working directory."""
    p = Path(path)
    if p.is_absolute():
        return p
    base = Path(cwd) if cwd else Path.cwd()
    return (base / p).resolve()


def _read_text_file(path: str, cwd: str | None) -> str:
    """Read a text file from the workspace, raising a clear error if missing."""
    file_path = _resolve_path(path, cwd)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path} (resolved: {file_path})")
    return file_path.read_text(encoding="utf-8", errors="replace")


def _write_text_file(path: str, content: str, cwd: str | None) -> Path:
    """Write text content back to a workspace file."""
    file_path = _resolve_path(path, cwd)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if Pi wrapped the file content in them."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Drop opening fence
        while lines and lines[0].startswith("```"):
            lines.pop(0)
        # Drop closing fence
        while lines and lines[-1].strip() == "```":
            lines.pop()
        return "\n".join(lines)
    return text


def _load_images(image_paths: list[str] | None) -> list[dict[str, str]]:
    """Encode a list of image paths for Pi's prompt command."""
    if not image_paths:
        return []
    return [encode_image(p) for p in image_paths]


def _format_file_paths(paths: list[str] | None) -> str:
    if not paths:
        return ""
    lines = "\n\nRelevant files:\n" + "\n".join(f"- {p}" for p in paths)
    return lines


def _build_code_assist_prompt(arguments: dict[str, Any]) -> str:
    task = arguments["task"]
    files = _format_file_paths(arguments.get("file_paths"))
    return (
        "You are working as a coding assistant inside a larger research agent system.\n\n"
        f"Task: {task}{files}\n\n"
        "Please implement the requested change in the workspace, then summarize what you did, "
        "which files you modified, and any important caveats."
    )


def _build_review_prompt(arguments: dict[str, Any]) -> str:
    code = arguments["code"]
    focus = arguments.get("focus", "general correctness and code quality")
    file_path = arguments.get("file_path")
    header = f"File: {file_path}\n\n" if file_path else ""
    return (
        f"{header}Please review the following code. Focus on {focus}.\n\n"
        f"```\n{code}\n```\n\n"
        "Provide a concise review: bugs, risks, style issues, and concrete suggestions."
    )


def _build_debug_prompt(arguments: dict[str, Any]) -> str:
    error = arguments["error"]
    context = arguments.get("context", "")
    files = _format_file_paths(arguments.get("file_paths"))
    parts = ["Please diagnose and fix the following error."]
    if context:
        parts.append(f"\nContext:\n{context}")
    parts.append(f"\nError:\n{error}")
    parts.append(files)
    parts.append(
        "\n\nInvestigate the relevant files, apply the minimal fix, and report the root cause "
        "and what you changed."
    )
    return "".join(parts)


def _build_explain_prompt(arguments: dict[str, Any]) -> str:
    code = arguments["code"]
    question = arguments.get("question", "Explain how this works.")
    file_path = arguments.get("file_path")
    header = f"File: {file_path}\n\n" if file_path else ""
    return f"{header}{question}\n\n```\n{code}\n```"


def _build_read_file_prompt(file_path: str, content: str, question: str | None) -> str:
    if question:
        return (
            f"File: {file_path}\n\n"
            f"```\n{content}\n```\n\n"
            f"Question: {question}"
        )
    return f"File: {file_path}\n\n```\n{content}\n```"


def _build_edit_file_prompt(file_path: str, content: str, instruction: str) -> str:
    return (
        f"You are editing the file `{file_path}`.\n\n"
        f"Current content:\n```\n{content}\n```\n\n"
        f"Instruction: {instruction}\n\n"
        "Return the complete updated file content. Do not add explanations outside the code."
    )


async def run_server(config: PiConfig | None = None) -> None:
    """Run the MCP server over stdio."""
    config = config or config_from_env()
    pi = PiClient(config)

    # Eagerly start Pi so the first tool call is fast.
    try:
        await pi.start()
    except Exception as exc:
        logger.error("Failed to start Pi RPC subprocess: %s", exc)
        # Keep going; we will retry on first tool call and return a clear error.

    server = Server(APP_NAME)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        arguments = arguments or {}
        cwd = config.cwd
        try:
            if name == "pi_code_assist":
                prompt = _build_code_assist_prompt(arguments)
                images = _load_images(arguments.get("image_paths"))
                text = await pi.prompt(prompt, images=images, timeout=600.0)
                return [TextContent(type="text", text=text)]

            if name == "pi_review_code":
                prompt = _build_review_prompt(arguments)
                images = _load_images(arguments.get("image_paths"))
                text = await pi.prompt(prompt, images=images, timeout=300.0)
                return [TextContent(type="text", text=text)]

            if name == "pi_debug":
                prompt = _build_debug_prompt(arguments)
                images = _load_images(arguments.get("image_paths"))
                text = await pi.prompt(prompt, images=images, timeout=600.0)
                return [TextContent(type="text", text=text)]

            if name == "pi_explain":
                prompt = _build_explain_prompt(arguments)
                images = _load_images(arguments.get("image_paths"))
                text = await pi.prompt(prompt, images=images, timeout=300.0)
                return [TextContent(type="text", text=text)]

            if name == "pi_read_file":
                file_path = arguments["file_path"]
                content = _read_text_file(file_path, cwd)
                question = arguments.get("question")
                if not question:
                    return [TextContent(type="text", text=content)]
                prompt = _build_read_file_prompt(file_path, content, question)
                images = _load_images(arguments.get("image_paths"))
                text = await pi.prompt(prompt, images=images, timeout=300.0)
                return [TextContent(type="text", text=text)]

            if name == "pi_edit_file":
                file_path = arguments["file_path"]
                instruction = arguments["instruction"]
                content = _read_text_file(file_path, cwd)
                prompt = _build_edit_file_prompt(file_path, content, instruction)
                images = _load_images(arguments.get("image_paths"))
                new_content = await pi.prompt(prompt, images=images, timeout=600.0)
                new_content = _strip_code_fences(new_content)
                written_path = _write_text_file(file_path, new_content, cwd)
                return [
                    TextContent(
                        type="text",
                        text=(
                            f"Updated file: {written_path}\n\n"
                            f"```\n{new_content}\n```"
                        ),
                    )
                ]

            if name == "pi_list_commands":
                commands = await pi.get_commands()
                if not commands:
                    return [TextContent(type="text", text="No Pi commands available.")]
                lines = ["Available Pi commands:"]
                for cmd in commands:
                    source = cmd.get("source", "unknown")
                    desc = cmd.get("description") or "(no description)"
                    lines.append(f"- /{cmd['name']} [{source}]: {desc}")
                return [TextContent(type="text", text="\n".join(lines))]

            if name == "pi_invoke_command":
                cmd_name = arguments["name"]
                message = arguments.get("message", "")
                full_message = f"/{cmd_name}"
                if message:
                    full_message += f"\n\n{message}"
                images = _load_images(arguments.get("image_paths"))
                text = await pi.prompt(full_message, images=images, timeout=600.0)
                return [TextContent(type="text", text=text)]

            if name == "pi_bash":
                data = await pi.bash(arguments["command"], timeout=120.0)
                output = data.get("output", "")
                exit_code = data.get("exitCode", 0)
                text = f"Exit code: {exit_code}\n\n{output}"
                return [TextContent(type="text", text=text)]

            if name == "pi_reset_session":
                await pi.new_session()
                return [TextContent(type="text", text="Pi session reset.")]

            raise ValueError(f"Unknown tool: {name}")
        except PiError as exc:
            logger.error("Tool %s failed: %s", name, exc)
            return [TextContent(type="text", text=f"pi-mcp-bridge error: {exc}")]
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return [TextContent(type="text", text=f"pi-mcp-bridge error: {exc}")]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )

    await pi.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP bridge to the Pi coding agent")
    parser.add_argument(
        "--pi-bin", default=None, help="Path to the pi executable (default: pi)"
    )
    parser.add_argument(
        "--cwd", default=None, help="Working directory for the Pi subprocess"
    )
    parser.add_argument("--provider", default=None, help="LLM provider for Pi")
    parser.add_argument("--model", default=None, help="Model for Pi")
    parser.add_argument("--session-dir", default=None, help="Pi session directory")
    parser.add_argument(
        "--session-name", default="jw-pi-bridge", help="Pi session display name"
    )
    parser.add_argument(
        "--no-session",
        action="store_true",
        help="Disable Pi session persistence",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging to stderr",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = config_from_env()
    if args.pi_bin:
        config.pi_bin = args.pi_bin
    if args.cwd:
        config.cwd = args.cwd
    if args.provider:
        config.provider = args.provider
    if args.model:
        config.model = args.model
    if args.session_dir:
        config.session_dir = args.session_dir
    if args.session_name:
        config.session_name = args.session_name
    if args.no_session:
        config.no_session = True

    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
