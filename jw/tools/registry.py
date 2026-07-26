"""Extensible tool-bundle registry.

The bootstrap shape follows Deep Agents' profile registry: built-ins register
from their owning modules, third-party packages register through a lazy Python
entry point, and consumers resolve immutable snapshots.  This keeps tool
ownership next to the implementation instead of maintaining a second central
list that every runtime surface must remember to update.
"""

from __future__ import annotations

import logging
import threading
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from types import MappingProxyType

logger = logging.getLogger(__name__)

TOOL_BUNDLE_ENTRY_POINT_GROUP = "jw.tool_bundles"


def _tool_name(tool: object) -> str:
    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name.strip():
        raise TypeError("registered tools must expose a non-empty string .name")
    return name


def _validate_bundle_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("tool bundle name must not be empty")
    if any(character.isspace() for character in normalized):
        raise ValueError(f"tool bundle name must not contain whitespace: {name!r}")
    return normalized


@dataclass(frozen=True, slots=True)
class ToolBundle:
    """A named capability whose tools are registered by their owning module."""

    name: str
    tools: tuple[object, ...]
    include_in_main: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _validate_bundle_name(self.name))
        deduplicated: dict[str, object] = {}
        for registered_tool in self.tools:
            deduplicated[_tool_name(registered_tool)] = registered_tool
        object.__setattr__(self, "tools", tuple(deduplicated.values()))


_TOOL_BUNDLES: dict[str, ToolBundle] = {}
_REGISTRY_LOCK = threading.RLock()
_BOOTSTRAP_CONDITION = threading.Condition(_REGISTRY_LOCK)
_plugins_loaded = False
_loading_thread_id: int | None = None


def register_tool_bundle(
    name: str,
    tools: Sequence[object],
    *,
    include_in_main: bool = True,
) -> None:
    """Register or extend one tool capability.

    Registration is additive, matching the upstream Deep Agents profile
    registry.  Re-registering a tool name in the same bundle replaces that
    entry while preserving the original order of all other tools.
    """

    incoming = ToolBundle(name, tuple(tools), include_in_main)
    with _REGISTRY_LOCK:
        existing = _TOOL_BUNDLES.get(incoming.name)
        if existing is None:
            _TOOL_BUNDLES[incoming.name] = incoming
            return

        merged = {_tool_name(tool): tool for tool in existing.tools}
        for registered_tool in incoming.tools:
            merged[_tool_name(registered_tool)] = registered_tool
        _TOOL_BUNDLES[incoming.name] = ToolBundle(
            incoming.name,
            tuple(merged.values()),
            include_in_main=existing.include_in_main or incoming.include_in_main,
        )


def _plugin_label(entry_point: EntryPoint) -> str:
    distribution = getattr(entry_point, "dist", None)
    distribution_name = (
        getattr(distribution, "name", None) if distribution is not None else None
    )
    if isinstance(distribution_name, str) and distribution_name:
        return f"{entry_point.name!r} (dist={distribution_name!r})"
    return repr(entry_point.name)


def _load_entry_point_plugins() -> None:
    try:
        discovered = entry_points(group=TOOL_BUNDLE_ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001
        message = (
            f"Failed to enumerate {TOOL_BUNDLE_ENTRY_POINT_GROUP} entry points; "
            f"third-party tool bundles were skipped: {type(exc).__name__}: {exc}"
        )
        logger.warning(message, exc_info=True)
        warnings.warn(message, stacklevel=2)
        return

    for entry_point in discovered:
        label = _plugin_label(entry_point)
        try:
            register = entry_point.load()
        except Exception as exc:  # noqa: BLE001
            message = (
                f"Skipping tool-bundle plugin {label}: failed to load "
                f"{entry_point.value!r}: {type(exc).__name__}: {exc}"
            )
            logger.exception(message)
            warnings.warn(message, stacklevel=2)
            continue
        if not callable(register):
            message = (
                f"Skipping tool-bundle plugin {label}: {entry_point.value!r} "
                "did not resolve to a callable."
            )
            logger.error(message)
            warnings.warn(message, stacklevel=2)
            continue
        try:
            register()
        except Exception as exc:  # noqa: BLE001
            message = (
                f"Skipping tool-bundle plugin {label}: registration raised "
                f"{type(exc).__name__}: {exc}"
            )
            logger.exception(message)
            warnings.warn(message, stacklevel=2)


def _ensure_plugins_loaded() -> None:
    """Load third-party registrations exactly once and never expose half-loads."""

    global _plugins_loaded, _loading_thread_id  # noqa: PLW0603
    thread_id = threading.get_ident()
    with _BOOTSTRAP_CONDITION:
        if _plugins_loaded:
            return
        if _loading_thread_id == thread_id:
            return
        while _loading_thread_id is not None:
            _BOOTSTRAP_CONDITION.wait()
            if _plugins_loaded:
                return
        _loading_thread_id = thread_id

    saved = dict(_TOOL_BUNDLES)
    try:
        _load_entry_point_plugins()
    except Exception:
        with _BOOTSTRAP_CONDITION:
            _TOOL_BUNDLES.clear()
            _TOOL_BUNDLES.update(saved)
            _loading_thread_id = None
            _BOOTSTRAP_CONDITION.notify_all()
        raise

    with _BOOTSTRAP_CONDITION:
        _plugins_loaded = True
        _loading_thread_id = None
        _BOOTSTRAP_CONDITION.notify_all()


def get_tool_bundles() -> Mapping[str, tuple[object, ...]]:
    """Return an immutable snapshot of every discovered capability bundle."""

    _ensure_plugins_loaded()
    with _REGISTRY_LOCK:
        return MappingProxyType(
            {name: bundle.tools for name, bundle in _TOOL_BUNDLES.items()}
        )


def resolve_tool_bundles(names: Iterable[str] | None = None) -> list[object]:
    """Resolve bundles in declaration order and reject cross-bundle collisions."""

    _ensure_plugins_loaded()
    with _REGISTRY_LOCK:
        selected_names = list(_TOOL_BUNDLES) if names is None else list(names)
        resolved: dict[str, object] = {}
        owners: dict[str, str] = {}
        for bundle_name in selected_names:
            bundle = _TOOL_BUNDLES.get(bundle_name)
            if bundle is None:
                raise KeyError(f"unknown tool bundle: {bundle_name}")
            for registered_tool in bundle.tools:
                name = _tool_name(registered_tool)
                previous = resolved.get(name)
                if previous is not None and previous is not registered_tool:
                    raise ValueError(
                        f"tool {name!r} is provided by both "
                        f"{owners[name]!r} and {bundle_name!r}"
                    )
                resolved[name] = registered_tool
                owners[name] = bundle_name
        return list(resolved.values())


def get_builtin_tool_registry() -> dict[str, object]:
    """Return the discovered tool catalog used by every runtime surface."""

    return {_tool_name(tool): tool for tool in resolve_tool_bundles()}


def get_main_agent_tools() -> list[object]:
    """Return tools from bundles that opt into the main JW agent."""

    _ensure_plugins_loaded()
    with _REGISTRY_LOCK:
        main_bundles = [
            name for name, bundle in _TOOL_BUNDLES.items() if bundle.include_in_main
        ]
    return resolve_tool_bundles(main_bundles)
