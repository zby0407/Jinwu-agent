"""Tests for cross-source entity resolution."""

from __future__ import annotations

import pytest

from jw.scientific_data.contracts import ConfidenceBand, IssueCode, RepairMode
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.entities import (
    AliasClaim,
    EntityResolver,
    coordinate_separation_arcsec,
    parse_coordinates,
)

SNIA = get_plugin("snia_light_curve")


def resolver() -> EntityResolver:
    return EntityResolver(plugin=SNIA)


def test_decimal_degrees_and_sexagesimal_pairs_agree():
    sexagesimal = parse_coordinates("14:03:05.8", "+54:16:25", ra_unit="hour")
    decimal = parse_coordinates("210.77417", "54.27361")
    assert sexagesimal is not None and decimal is not None
    assert sexagesimal[0] == pytest.approx(decimal[0], abs=1e-4)
    assert sexagesimal[1] == pytest.approx(decimal[1], abs=1e-4)


def test_declared_degree_ra_is_not_multiplied_by_fifteen():
    value = parse_coordinates("14:03:05.8", "54:16:25", ra_unit="deg")
    assert value is not None
    assert value[0] == pytest.approx(14.0516, abs=1e-4)


@pytest.mark.parametrize(
    ("ra", "dec"),
    [("", "54:00:00"), ("210.0", "abc"), ("210.0", "95.0"), ("400.0", "10.0")],
)
def test_impossible_coordinates_are_refused(ra, dec):
    assert parse_coordinates(ra, dec) is None


def test_separation_is_great_circle_arcseconds():
    first = {"ra": 210.0, "dec": 54.0}
    second = {"ra": 210.0, "dec": 54.001}
    assert coordinate_separation_arcsec(first, second) == pytest.approx(3.6, abs=1e-4)
    assert coordinate_separation_arcsec(first, first) == pytest.approx(0.0)


def test_same_name_across_sources_lands_on_one_entity():
    entities = resolver()
    first = entities.add(AliasClaim(label="SN 2011fe", source_id="src-a"))
    second = entities.add(AliasClaim(label="sn2011fe", source_id="src-b"))
    assert first.entity_id == second.entity_id
    assert len(entities.entities()) == 1
    assert entities.issues() == ()


def test_known_aliases_join_the_same_entity():
    entities = resolver()
    entity = entities.add_aliases(
        "SN 2011fe", ("PTF11kly", "SN2011FE"), source_id="src-a"
    )
    assert entities.entity_id_for("PTF11kly") == entity.entity_id
    assert entities.entity_id_for("SN 2011fe") == entity.entity_id
    ref = entities.resolve("PTF11kly")
    assert ref is not None
    # Surface forms differ only by spacing/case, so they share one identity key.
    assert {alias.normalised for alias in ref.aliases} == {"sn2011fe", "ptf11kly"}


def test_coordinates_link_different_names_within_tolerance():
    entities = resolver()
    host = entities.add(
        AliasClaim(
            label="PTF11kly",
            source_id="src-a",
            coordinates={"ra": 210.7742, "dec": 54.2736},
        )
    )
    linked = entities.add(
        AliasClaim(
            label="SN 2011fe",
            source_id="src-b",
            coordinates={"ra": 210.7743, "dec": 54.2736},
        )
    )
    assert linked.entity_id == host.entity_id
    assert any("coordinates_agree" in item for item in linked.link_evidence)


def test_positions_far_apart_never_merge_and_ask_a_human():
    entities = resolver()
    entities.add(
        AliasClaim(
            label="SN 2011fe",
            source_id="src-a",
            coordinates={"ra": 210.7742, "dec": 54.2736},
        )
    )
    entities.add(
        AliasClaim(
            label="SN 2011fe", source_id="src-b", coordinates={"ra": 210.9, "dec": 54.4}
        )
    )
    issues = entities.issues()
    assert len(issues) == 1
    assert issues[0].code is IssueCode.ENTITY_IDENTITY_CONFLICT
    assert issues[0].repair_mode is RepairMode.HUMAN_REQUIRED
    tolerance = SNIA.entity.coord_tolerance_arcsec
    assert issues[0].evidence["separation_arcsec"] > tolerance
    entity = entities.resolve("SN 2011fe")
    assert entity is not None
    assert entity.link_confidence is ConfidenceBand.LOW


def test_shared_identifier_marks_both_entities_ambiguous():
    entities = resolver()
    first = entities.add(AliasClaim(label="SN 2011fe", source_id="src-a"))
    second = entities.add(
        AliasClaim(
            label="SN 2012cg", source_id="src-b", coordinates={"ra": 100.0, "dec": 10.0}
        )
    )
    entities.add_aliases("SN 2012cg", ("SN 2011fe",), source_id="src-b")
    issues = entities.issues()
    assert first.entity_id != second.entity_id
    assert any(issue.code is IssueCode.ENTITY_IDENTITY_CONFLICT for issue in issues)
    resolved = entities.resolve("SN 2012cg")
    assert resolved is not None
    assert resolved.ambiguous_with


def test_entities_are_sorted_and_alias_index_is_reversible():
    entities = resolver()
    entities.add(AliasClaim(label="SN 2012cg", source_id="src-a"))
    entities.add(AliasClaim(label="SN 2011fe", source_id="src-a"))
    ids = [entity.entity_id for entity in entities.entities()]
    assert ids == sorted(ids)
    for entity in entities.entities():
        assert entities.resolve(entity.label).entity_id == entity.entity_id


def test_empty_label_is_rejected():
    with pytest.raises(ValueError):
        resolver().add(AliasClaim(label="   "))
