"""Tests for the source-preserving SILSO cycle reproduction helper."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[1]
    / "jw/subagents/solar/skills/solar-cycle/scripts/reproduce_silso_cycles.py"
)
SPEC = importlib.util.spec_from_file_location("reproduce_silso_cycles", SCRIPT)
assert SPEC
assert SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_cycle_selector_supports_ranges_and_lists():
    assert MODULE.parse_cycle_selector("21-23,25") == [21, 22, 23, 25]
    with pytest.raises(ValueError, match="descending"):
        MODULE.parse_cycle_selector("24-21")


def test_official_and_recomputed_results_remain_separate():
    official = MODULE.parse_official_cycles(
        """Cycle Minimum Maximum Duration in Nb
20 1964 10 14.3 1968 11 156.6 11 05
21 1976 03 17.8 1979 12 232.9 10 06
22 1986 09 13.5 1989 11 212.5 9 11
"""
    )
    series = MODULE.parse_smoothed_series(
        """1968;11;1968.9;156.6;0;0;0
1976;03;1976.2;17.8;0;0;0
1979;12;1979.9;231.0;0;0;0
1986;08;1986.6;13.4;0;0;0
1986;09;1986.7;13.5;0;0;0
"""
    )

    rows = MODULE.build_comparison([21], official, series)

    assert rows[0]["official_maximum"]["sunspot_number"] == 232.9
    assert rows[0]["recomputed_maximum"]["sunspot_number"] == 231.0
    assert rows[0]["maximum_matches_official"] is False
    assert rows[0]["minimum_matches_official"] is True
    assert rows[0]["official_rise_months"] == 45
    assert rows[0]["recomputed_rise_months"] == 45


def test_fetch_text_retries_transient_transport_failure(monkeypatch):
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        assert timeout == 60
        if calls < 3:
            raise OSError("transient TLS reset")
        return io.BytesIO(b"ok")

    monkeypatch.setattr(MODULE.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    assert MODULE.fetch_text("https://example.test", attempts=3) == "ok"
    assert calls == 3
