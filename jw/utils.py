"""Utility functions for JW.

This module primarily contains helpers for displaying messages and prompts in
notebooks, and lightweight configuration loaders used by the agent runtime.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

logger = logging.getLogger(__name__)
console = Console()


def format_message_content(message):
    """Convert message content to displayable string.

    Args:
        message: A LangChain message object with content attribute.

    Returns:
        Formatted string representation of the message content.
    """
    parts = []
    tool_calls_processed = False

    # Handle main content
    if isinstance(message.content, str):
        parts.append(message.content)
    elif isinstance(message.content, list):
        # Handle complex content like tool calls (Anthropic format)
        for item in message.content:
            if item.get("type") == "text":
                parts.append(item["text"])
            elif item.get("type") == "tool_use":
                parts.append(f"\n🔧 Tool Call: {item['name']}")
                parts.append(f"   Args: {json.dumps(item['input'], indent=2)}")
                parts.append(f"   ID: {item.get('id', 'N/A')}")
                tool_calls_processed = True
    else:
        parts.append(str(message.content))

    # Handle tool calls attached to the message (OpenAI format) only when the
    # content traversal above did not already process them.
    if (
        not tool_calls_processed
        and hasattr(message, "tool_calls")
        and message.tool_calls
    ):
        for tool_call in message.tool_calls:
            parts.append(f"\n🔧 Tool Call: {tool_call['name']}")
            parts.append(f"   Args: {json.dumps(tool_call['args'], indent=2)}")
            parts.append(f"   ID: {tool_call['id']}")

    return "\n".join(parts)


def format_messages(messages):
    """Format and display a list of messages with Rich formatting.

    Args:
        messages: List of LangChain message objects to display.
    """
    for m in messages:
        msg_type = m.__class__.__name__.replace("Message", "")
        content = format_message_content(m)

        if msg_type == "Human":
            console.print(Panel(content, title="🧑 Human", border_style="blue"))
        elif msg_type == "Ai":
            console.print(Panel(content, title="🤖 Assistant", border_style="green"))
        elif msg_type == "Tool":
            console.print(Panel(content, title="🔧 Tool Output", border_style="yellow"))
        else:
            console.print(Panel(content, title=f"📝 {msg_type}", border_style="white"))


def show_prompt(prompt_text: str, title: str = "Prompt", border_style: str = "blue"):
    """Display a prompt with rich formatting and XML tag highlighting.

    Args:
        prompt_text: The prompt string to display
        title: Title for the panel (default: "Prompt")
        border_style: Border color style (default: "blue")
    """
    # Create a formatted display of the prompt
    formatted_text = Text(prompt_text)
    formatted_text.highlight_regex(r"<[^>]+>", style="bold blue")  # Highlight XML tags
    formatted_text.highlight_regex(
        r"##[^#\n]+", style="bold magenta"
    )  # Highlight headers
    formatted_text.highlight_regex(
        r"###[^#\n]+", style="bold cyan"
    )  # Highlight sub-headers

    # Display in a panel for better presentation
    console.print(
        Panel(
            formatted_text,
            title=f"[bold green]{title}[/bold green]",
            border_style=border_style,
            padding=(1, 2),
        )
    )


def load_subagents(
    config_path: Path | list[Path] | tuple[Path, ...],
    *,
    tool_registry: dict[str, Any],
    tool_bundles: Mapping[str, Sequence[Any]] | None = None,
    prompt_refs: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Load subagent definitions from one or more directories of YAML files.

    NOTE: This is a custom utility. deepagents does not natively load subagents
    from files - they're normally defined inline in the create_deep_agent() call.
    We externalize to YAML here to keep configuration separate from code.

    ``config_path`` may be:

    * a single directory — scanned **recursively** for ``*.yaml`` files, so
      callers can organise agents into bundle sub-directories (e.g.
      ``subagents/core/``, ``subagents/solar/``) without changing the loader;
    * a list/tuple of directories — each is scanned recursively and merged
      into one mapping, letting a deployment enable multiple domain bundles
      side-by-side.

    All ``*.yaml`` files across every directory are merged into a single
    mapping. Files starting with ``.`` (dotfiles, editor swap files) or ``_``
    (private / disabled) are ignored. ``.yml`` is intentionally not supported
    — keeps one canonical extension and avoids the dev-vs-wheel packaging
    mismatch.

    Each file's top level must be a mapping ``{<agent-name>: <spec>}``::

        planner-agent:
          description: "..."
          tools: [think_tool]
          system_prompt: |
            ...
        research-agent:
          description: "..."
          tool_bundles: [reasoning, web-search]
          system_prompt: |
            ...

    ``tool_bundles`` is the preferred capability-level declaration. The
    legacy ``tools`` list remains supported for one-off or externally supplied
    tools such as MCP entries.
    """
    prompt_refs = prompt_refs or {}
    tool_bundles = tool_bundles or {}

    # Normalise to a list of directories. Accepting a single Path keeps the
    # original call-sites working unchanged.
    if isinstance(config_path, (str, Path)):
        config_dirs: list[Path] = [Path(config_path)]
    else:
        config_dirs = [Path(p) for p in config_path]

    for d in config_dirs:
        if not d.is_dir():
            raise ValueError(
                f"{d}: sub-agent config must be a directory "
                f"containing one <name>.yaml per agent"
            )

    # Only ``.yaml`` is supported (canonical extension). Skip files starting
    # with ``_`` (private / disabled) or ``.`` (dotfiles, editor swap files
    # like ``.foo.yaml.swp``). ``.yml`` is intentionally not loaded — keeps
    # one canonical extension across the project, simplifies packaging
    # (no need for a parallel ``subagents/*.yml`` entry in ``pyproject.toml``
    # ``package-data``), and matches every existing yaml file in this repo.
    #
    # ``rglob`` makes the scan recursive so bundle sub-directories (core/,
    # solar/, ...) are picked up transparently. Sorting by the path relative
    # to each config dir keeps load order deterministic across platforms.
    config: dict[str, Any] = {}
    for config_dir in config_dirs:
        yaml_files = sorted(
            config_dir.rglob("*.yaml"),
            key=lambda p: p.relative_to(config_dir).as_posix(),
        )
        for yml in yaml_files:
            if yml.name.startswith(".") or yml.name.startswith("_"):
                continue
            # ``bundle.yaml`` is bundle metadata (name/version/agents/skills
            # manifest), NOT a sub-agent definition — skip it so it doesn't
            # get parsed as a ``{agent-name: spec}`` mapping.
            if yml.name == "bundle.yaml":
                continue
            # Skip yaml files living under a disabled/private sub-directory
            # (any path component starting with ``_`` or ``.``). This lets a
            # bundle ship an ``_archive/`` folder without it being loaded.
            rel_parts = yml.relative_to(config_dir).parts
            if any(part.startswith((".", "_")) for part in rel_parts[:-1]):
                continue
            with yml.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                # Empty file — skip silently (matches dotfile/underscore skip behavior)
                continue
            if not isinstance(data, dict):
                raise ValueError(
                    f"{yml}: top-level must be a mapping (one entry per sub-agent)"
                )
            # Detect duplicate keys across files (and across bundle dirs)
            for key in data:
                if key in config:
                    raise ValueError(
                        f"Sub-agent {key!r} defined in multiple files; "
                        f"second occurrence in {yml.name}"
                    )
            for key, spec in data.items():
                if not isinstance(spec, dict):
                    raise ValueError(
                        f"{yml}: sub-agent {key!r} must map to a spec dict, "
                        f"got {type(spec).__name__}"
                    )
            config.update(data)

    if not config:
        raise ValueError(
            f"{config_dirs}: no sub-agent definitions found "
            f"(expected one or more <name>.yaml files)"
        )

    subagents: list[dict[str, Any]] = []

    def _build_one(name: str, spec: dict[str, Any]) -> dict[str, Any]:
        subagent: dict[str, Any] = {
            "name": name,
            "description": spec.get("description", ""),
        }

        if "system_prompt_ref" in spec:
            ref = spec["system_prompt_ref"]
            if ref not in prompt_refs:
                raise ValueError(
                    f"Unknown system_prompt_ref '{ref}' for subagent '{name}'"
                )
            subagent["system_prompt"] = prompt_refs[ref]
        else:
            subagent["system_prompt"] = spec.get("system_prompt", "")

        if "model" in spec:
            subagent["model"] = spec["model"]

        if "skills" in spec:
            subagent["skills"] = spec["skills"]

        if "tool_bundles" in spec or "tools" in spec:
            resolved: list[Any] = []
            bundle_names = spec.get("tool_bundles", [])
            if not isinstance(bundle_names, list) or not all(
                isinstance(bundle_name, str) for bundle_name in bundle_names
            ):
                raise ValueError(
                    f"Subagent {name!r}: 'tool_bundles' must be a list of strings"
                )
            for bundle_name in bundle_names:
                bundle_tools = tool_bundles.get(bundle_name)
                if bundle_tools is None:
                    raise ValueError(
                        f"Subagent {name!r}: unknown tool bundle {bundle_name!r}"
                    )
                resolved.extend(bundle_tools)

            tool_names = spec.get("tools", [])
            if not isinstance(tool_names, list) or not all(
                isinstance(tool_name, str) for tool_name in tool_names
            ):
                raise ValueError(
                    f"Subagent {name!r}: 'tools' must be a list of strings"
                )
            for t in tool_names:
                if t in tool_registry:
                    resolved.append(tool_registry[t])
                else:
                    logger.warning(
                        "Subagent %r: tool %r not in registry, skipping", name, t
                    )
            deduplicated: dict[str, Any] = {}
            for resolved_tool in resolved:
                tool_name = getattr(resolved_tool, "name", None)
                if not isinstance(tool_name, str) or not tool_name:
                    raise ValueError(
                        f"Subagent {name!r}: resolved tool has no valid .name"
                    )
                existing = deduplicated.get(tool_name)
                if existing is not None and existing is not resolved_tool:
                    raise ValueError(
                        f"Subagent {name!r}: tool {tool_name!r} resolves to "
                        "multiple implementations"
                    )
                deduplicated[tool_name] = resolved_tool
            subagent["tools"] = list(deduplicated.values())

        restrict_tools = spec.get("restrict_tools", False)
        if not isinstance(restrict_tools, bool):
            raise ValueError(f"Subagent {name!r}: 'restrict_tools' must be a boolean")
        subagent["_restrict_tools"] = restrict_tools

        model_call_limit = spec.get("model_call_limit")
        if model_call_limit is not None:
            if (
                isinstance(model_call_limit, bool)
                or not isinstance(model_call_limit, int)
                or model_call_limit <= 0
            ):
                raise ValueError(
                    f"Subagent {name!r}: 'model_call_limit' must be a positive integer"
                )
            # Internal field consumed by the sync/async middleware builders.
            # It must never reach DeepAgents' subagent schema.
            subagent["_model_call_limit"] = model_call_limit

        # Internal field: carries the ``async:`` yaml flag through to
        # ``_maybe_swap_async_subagents`` so the swap doesn't need a second
        # yaml pass to discover async-flagged agents. Underscore prefix marks
        # it as internal — must be popped before passing to deepagents.
        async_val = spec.get("async", False)
        if not isinstance(async_val, bool):
            # Reject quoted-string yaml values like ``async: "false"`` —
            # ``bool("false")`` is ``True`` (non-empty string), which silently
            # flips the agent into async mode. Fail loud instead.
            raise ValueError(
                f"Subagent {name!r}: 'async' must be a boolean, "
                f"got {type(async_val).__name__}: {async_val!r}"
            )
        subagent["_async"] = async_val

        return subagent

    for name, spec in config.items():
        subagents.append(_build_one(name, spec))

    return subagents


def load_subagent(
    config_path: Path | list[Path] | tuple[Path, ...],
    name: str,
    *,
    tool_registry: dict[str, Any],
    tool_bundles: Mapping[str, Sequence[Any]] | None = None,
    prompt_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Load a single sub-agent by name from YAML."""
    for agent in load_subagents(
        config_path,
        tool_registry=tool_registry,
        tool_bundles=tool_bundles,
        prompt_refs=prompt_refs,
    ):
        if agent.get("name") == name:
            return agent
    raise KeyError(f"Sub-agent not found: {name}")
