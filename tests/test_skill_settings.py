"""Tests for persistent skill on/off settings (``jw/skill_settings.py``)."""

import json
import logging
from pathlib import Path

import pytest

from jw import paths
from jw.skill_settings import (
    SKILL_SETTINGS_FILENAME,
    disabled_skill_names,
    filter_enabled,
    set_skill_enabled,
    skill_settings_path,
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point ``paths.DATA_DIR`` at a temp dir; the settings path resolves late."""

    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    return data


def _read_settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_settings_path_is_resolved_in_data_dir(data_dir):
    assert skill_settings_path() == data_dir / SKILL_SETTINGS_FILENAME
    assert SKILL_SETTINGS_FILENAME == "skill_settings.json"


def test_missing_file_means_everything_enabled(data_dir):
    assert not (data_dir / SKILL_SETTINGS_FILENAME).exists()
    assert disabled_skill_names() == set()
    assert filter_enabled(["solar-flare-forecasting", "find-skills"]) == [
        "solar-flare-forecasting",
        "find-skills",
    ]


def test_disable_then_read_back(data_dir):
    set_skill_enabled("solar-flare-forecasting", False)

    assert disabled_skill_names() == {"solar-flare-forecasting"}
    payload = _read_settings(data_dir / SKILL_SETTINGS_FILENAME)
    assert payload == {"version": 1, "disabled": ["solar-flare-forecasting"]}


def test_reenabling_removes_the_name(data_dir):
    set_skill_enabled("solar-flare-forecasting", False)
    set_skill_enabled("find-skills", False)
    assert disabled_skill_names() == {"solar-flare-forecasting", "find-skills"}

    set_skill_enabled("solar-flare-forecasting", True)
    assert disabled_skill_names() == {"find-skills"}

    set_skill_enabled("find-skills", True)
    assert disabled_skill_names() == set()
    assert _read_settings(data_dir / SKILL_SETTINGS_FILENAME)["disabled"] == []


def test_disabled_list_is_sorted_and_deduplicated(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = data_dir / SKILL_SETTINGS_FILENAME
    settings.write_text(
        json.dumps({"version": 1, "disabled": ["zeta", "alpha", "alpha"]}),
        encoding="utf-8",
    )

    set_skill_enabled("mid", False)

    assert _read_settings(settings)["disabled"] == ["alpha", "mid", "zeta"]


def test_corrupt_json_is_tolerated(data_dir, caplog):
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = data_dir / SKILL_SETTINGS_FILENAME
    settings.write_text("{ this is not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="jw.skill_settings"):
        assert disabled_skill_names() == set()
    assert any("corrupt skill settings" in record.message for record in caplog.records)


def test_wrong_disabled_type_is_tolerated(data_dir, caplog):
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = data_dir / SKILL_SETTINGS_FILENAME
    settings.write_text(
        json.dumps({"version": 1, "disabled": "solar-flare-forecasting"}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="jw.skill_settings"):
        assert disabled_skill_names() == set()
    assert any("list of strings" in record.message for record in caplog.records)


def test_non_object_json_and_non_string_entries_are_tolerated(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = data_dir / SKILL_SETTINGS_FILENAME

    settings.write_text("[]", encoding="utf-8")
    assert disabled_skill_names() == set()

    settings.write_text(
        json.dumps({"version": 1, "disabled": ["find-skills", 7, None]}),
        encoding="utf-8",
    )
    assert disabled_skill_names() == {"find-skills"}


def test_unknown_fields_are_preserved(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = data_dir / SKILL_SETTINGS_FILENAME
    settings.write_text(
        json.dumps(
            {
                "version": 99,
                "disabled": ["find-skills"],
                "ui": {"density": "compact"},
            }
        ),
        encoding="utf-8",
    )

    set_skill_enabled("solar-flare-forecasting", False)

    payload = _read_settings(settings)
    assert payload["ui"] == {"density": "compact"}
    assert payload["version"] == 1
    assert payload["disabled"] == ["find-skills", "solar-flare-forecasting"]


def test_write_is_atomic_and_leaves_a_valid_json_file(data_dir):
    set_skill_enabled("find-skills", False)

    settings = data_dir / SKILL_SETTINGS_FILENAME
    text = settings.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == {"version": 1, "disabled": ["find-skills"]}
    # Atomic write must not leave temp siblings behind.
    assert sorted(entry.name for entry in data_dir.iterdir()) == [
        SKILL_SETTINGS_FILENAME
    ]


def test_filter_enabled_preserves_order_and_ignores_disabled(data_dir):
    set_skill_enabled("b", False)

    assert filter_enabled(["a", "b", "c", "a"]) == ["a", "c", "a"]
    assert filter_enabled(["x", "y"], disabled={"x"}) == ["y"]
    assert filter_enabled(iter(["b"])) == []


def test_write_failures_are_allowed_to_raise(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")

    # The parent path is a regular file, so the write cannot be prepared.
    with pytest.raises((FileExistsError, NotADirectoryError)):
        set_skill_enabled("find-skills", False, path=blocker / "skill_settings.json")
