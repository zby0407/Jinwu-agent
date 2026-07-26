from __future__ import annotations

from dataclasses import dataclass

import pytest

from jw.tools import registry


@dataclass
class _Tool:
    name: str


def _isolated_registry(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_TOOL_BUNDLES", {})
    monkeypatch.setattr(registry, "_plugins_loaded", True)
    monkeypatch.setattr(registry, "_loading_thread_id", None)


def test_bundle_registration_is_additive_and_latest_tool_wins(monkeypatch) -> None:
    _isolated_registry(monkeypatch)
    first = _Tool("shared")
    second = _Tool("other")
    replacement = _Tool("shared")

    registry.register_tool_bundle("example", [first, second])
    registry.register_tool_bundle("example", [replacement])

    assert registry.resolve_tool_bundles(["example"]) == [replacement, second]


def test_cross_bundle_name_collision_fails_loud(monkeypatch) -> None:
    _isolated_registry(monkeypatch)
    registry.register_tool_bundle("one", [_Tool("duplicate")])
    registry.register_tool_bundle("two", [_Tool("duplicate")])

    with pytest.raises(ValueError, match="provided by both"):
        registry.resolve_tool_bundles(["one", "two"])


def test_entry_point_plugin_registers_lazily(monkeypatch) -> None:
    monkeypatch.setattr(registry, "_TOOL_BUNDLES", {})
    monkeypatch.setattr(registry, "_plugins_loaded", False)
    monkeypatch.setattr(registry, "_loading_thread_id", None)
    plugin_tool = _Tool("plugin_tool")

    class _EntryPoint:
        name = "test-plugin"
        value = "test_plugin:register"
        dist = None

        @staticmethod
        def load():
            def register() -> None:
                registry.register_tool_bundle(
                    "plugin-capability",
                    [plugin_tool],
                    include_in_main=False,
                )

            return register

    monkeypatch.setattr(
        registry,
        "entry_points",
        lambda *, group: (
            [_EntryPoint()] if group == registry.TOOL_BUNDLE_ENTRY_POINT_GROUP else []
        ),
    )

    bundles = registry.get_tool_bundles()

    assert bundles["plugin-capability"] == (plugin_tool,)
