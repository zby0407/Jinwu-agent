"""Alignment tests: canonical records, missing reasons, conflicts, provenance."""

from __future__ import annotations

import pytest

from jw.scientific_data.alignment import align_tables
from jw.scientific_data.contracts import (
    ConfidenceBand,
    DataRequirementContract,
    ExtractedTable,
    IssueCode,
    MissingReason,
    ObservationField,
    ValueKind,
)
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.entities import AliasClaim, EntityResolver


def contract(**overrides) -> DataRequirementContract:
    fields: dict = {
        "contract_id": "c-snia",
        "research_goal": "Collect light curve points for type Ia supernovae",
        "entity_class": "type Ia supernova",
        "observables": (
            ObservationField(name="object", kind="identifier"),
            ObservationField(name="time", kind="time"),
            ObservationField(name="apparent_magnitude", kind="quantity", unit="mag"),
            ObservationField(name="magnitude_uncertainty", kind="quality", unit="mag"),
            ObservationField(name="band", kind="label"),
        ),
        "identifier_fields": ("object",),
        "time_field": "time",
        "domain_plugin": "snia_light_curve",
    }
    fields.update(overrides)
    return DataRequirementContract(**fields)


def table(
    rows,
    *,
    table_id="t1",
    source_id="src-a",
    columns=("object", "time", "magnitude", "e_mag", "band"),
    confidence=ConfidenceBand.HIGH,
) -> ExtractedTable:
    return ExtractedTable(
        table_id=table_id,
        source_id=source_id,
        columns=tuple(columns),
        rows=tuple(dict(zip(columns, row, strict=True)) for row in rows),
        confidence=confidence,
    )


PLUGIN = get_plugin("snia_light_curve")


def test_single_table_rows_become_canonical_records():
    result = align_tables(
        [
            table(
                [
                    ("SN2005A", "51544.5", "16.5", "0.1", "B"),
                    ("SN2005A", "51546.5", "15.8", "0.1", "B"),
                ]
            )
        ],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert len(result.records) == 2
    first = result.records[0]
    assert first.observable == "apparent_magnitude"
    assert first.value == pytest.approx(16.5)
    assert first.unit == "mag"
    assert first.value_raw == "16.5"
    assert first.time.endswith("2000-01-01") or first.time.startswith("2000-01-01")
    assert first.time_system == "mjd"
    assert first.uncertainty == pytest.approx(0.1)
    assert first.attributes["band"] == "B"
    assert first.entity_id.startswith("obj:")
    assert result.stats["records"] == 2


def test_provenance_records_row_and_column():
    result = align_tables(
        [table([("SN2005A", "51544.5", "16.5", "0.1", "B")])],
        contract=contract(),
        plugin=PLUGIN,
    )

    record = result.records[0]
    assert record.source_ids == ("src-a",)
    reference = record.provenance[0]
    assert reference.source_id == "src-a"
    assert reference.locator["column"] == "magnitude"
    assert reference.locator["table_id"] == "t1"
    assert reference.raw_excerpt == "16.5"


def test_exact_unit_conversion_is_applied_and_flagged():
    snia = table(
        [("SN2005A", "51544.5", "16.5", "0.1", "B")],
        columns=("object", "time", "flux", "e_flux", "band"),
    )
    flux_contract = contract(
        observables=(
            ObservationField(name="object", kind="identifier"),
            ObservationField(name="time", kind="time"),
            ObservationField(name="flux_density", kind="quantity", unit="mJy"),
        )
    )
    # The raw unit is declared on the mapping, so provide it via the header.
    rows = list(snia.rows)
    snia = ExtractedTable(
        table_id=snia.table_id,
        source_id=snia.source_id,
        columns=("object", "time", "flux [Jy]", "band"),
        rows=tuple({**row, "flux [Jy]": row["flux"]} for row in rows),
        confidence=ConfidenceBand.HIGH,
    )

    result = align_tables([snia], contract=flux_contract, plugin=PLUGIN)
    flux = [record for record in result.records if record.observable == "flux_density"]
    assert flux, "flux column should be mapped"
    record = flux[0]
    assert record.unit == "mJy"
    assert record.value == pytest.approx(16500.0)
    assert record.value_raw == "16.5"
    assert any(flag.startswith("unit_converted:Jy->mJy") for flag in record.flags)


def test_unconvertible_unit_is_reported_and_raw_unit_kept():
    snia = ExtractedTable(
        table_id="t1",
        source_id="src-a",
        columns=("object", "time", "flux [erg/s/cm2/A]", "band"),
        rows=(
            {
                "object": "SN2005A",
                "time": "51544.5",
                "flux [erg/s/cm2/A]": "1.2e-15",
                "band": "B",
            },
        ),
        confidence=ConfidenceBand.HIGH,
    )
    flux_contract = contract(
        observables=(
            ObservationField(name="object", kind="identifier"),
            ObservationField(name="time", kind="time"),
            ObservationField(name="flux_density", kind="quantity", unit="mJy"),
        )
    )

    result = align_tables([snia], contract=flux_contract, plugin=PLUGIN)
    record = next(r for r in result.records if r.observable == "flux_density")
    assert record.unit.startswith("erg")
    assert any(flag.startswith("unit_not_converted:") for flag in record.flags)
    codes = {issue.code for issue in result.issues}
    assert IssueCode.UNIT_UNKNOWN in codes or IssueCode.UNIT_INCONSISTENCY in codes


@pytest.mark.parametrize(
    ("cell", "reason"),
    [
        ("--", MissingReason.NOT_REPORTED),
        ("n/a", MissingReason.NOT_APPLICABLE),
        ("not applicable", MissingReason.NOT_APPLICABLE),
        ("bright", MissingReason.NOT_PARSED),
    ],
)
def test_missing_cells_keep_a_reason(cell, reason):
    """An absent number is still a record: it says why the value is missing."""
    result = align_tables(
        [table([("SN2005A", "51544.5", cell, "0.1", "B")])],
        contract=contract(),
        plugin=PLUGIN,
    )

    record = result.records[0]
    assert record.value is None
    assert record.missing_reason is reason
    assert record.value_raw == cell
    assert result.stats["missing_cells"] == 1


def test_blank_cell_in_a_populated_column_is_recorded_as_not_reported():
    result = align_tables(
        [
            table(
                [
                    ("SN2005A", "51544.5", "16.5", "0.1", "B"),
                    ("SN2005A", "51546.5", "", "0.1", "B"),
                ]
            )
        ],
        contract=contract(),
        plugin=PLUGIN,
    )

    blank = [record for record in result.records if record.value is None]
    assert len(blank) == 1
    assert blank[0].missing_reason is MissingReason.NOT_REPORTED
    assert blank[0].time  # the row still carries its time and entity


def test_limit_markers_become_value_kinds():
    result = align_tables(
        [table([("SN2005A", "51544.5", ">19.2", "0.1", "B")])],
        contract=contract(),
        plugin=PLUGIN,
    )

    record = result.records[0]
    assert record.value == pytest.approx(19.2)
    assert record.value_kind == ValueKind.LOWER_LIMIT


def test_upper_limit_marker_is_recorded():
    result = align_tables(
        [table([("SN2005A", "51544.5", "<14.0", "0.1", "B")])],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert result.records[0].value_kind == ValueKind.UPPER_LIMIT


def test_cross_source_conflict_keeps_both_values_and_never_averages():
    result = align_tables(
        [
            table([("SN2005A", "51544.5", "16.5", "0.1", "B")]),
            table(
                [("SN2005A", "51544.5", "16.9", "0.1", "B")],
                table_id="t2",
                source_id="src-b",
            ),
        ],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert len(result.records) == 1
    record = result.records[0]
    assert record.conflict is True
    assert set(record.source_ids) == {"src-a", "src-b"}
    assert record.value == pytest.approx(16.5), "first value is kept, not averaged"
    assert "16.5" in record.value_raw and "16.9" in record.value_raw
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.values == (16.5, 16.9)
    assert IssueCode.CROSS_SOURCE_CONFLICT in {issue.code for issue in result.issues}
    assert result.stats["conflicts"] == 1


def test_identical_duplicate_merges_sources_and_flags():
    result = align_tables(
        [
            table([("SN2005A", "51544.5", "16.5", "0.1", "B")]),
            table(
                [("SN2005A", "51544.5", "16.5", "0.1", "B")],
                table_id="t2",
                source_id="src-b",
            ),
        ],
        contract=contract(),
        plugin=PLUGIN,
    )

    record = result.records[0]
    assert record.conflict is False
    assert set(record.source_ids) == {"src-a", "src-b"}
    assert "duplicate_key" in record.flags
    assert len(record.provenance) == 2
    assert IssueCode.DUPLICATE_KEY in {issue.code for issue in result.issues}
    assert result.conflicts == ()


def test_table_without_entity_column_is_rejected_with_a_blocking_code():
    result = align_tables(
        [
            table(
                [("51544.5", "16.5", "0.1", "B")],
                columns=("mjd", "magnitude", "e_mag", "band"),
            )
        ],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert result.records == ()
    assert result.stats["skipped_rows"] == 1
    blocking = [
        issue
        for issue in result.issues
        if issue.code is IssueCode.MERGE_KEY_ABSENT and issue.blocking
    ]
    assert blocking, "missing entity column must be a blocking issue"


def test_band_keeps_points_apart_under_the_plugin_merge_keys():
    result = align_tables(
        [
            table(
                [
                    ("SN2005A", "51544.5", "16.5", "0.1", "B"),
                    ("SN2005A", "51544.5", "17.9", "0.1", "V"),
                ]
            )
        ],
        contract=contract(),
        plugin=PLUGIN,
    )

    assert len(result.records) == 2
    assert {record.attributes["band"] for record in result.records} == {"B", "V"}
    assert result.conflicts == ()


def test_out_of_range_value_raises_plausibility_issue():
    result = align_tables(
        [table([("SN2005A", "51544.5", "99.0", "0.1", "B")])],
        contract=contract(),
        plugin=PLUGIN,
    )

    codes = {issue.code for issue in result.issues}
    assert IssueCode.OUT_OF_RANGE in codes


def test_aliases_and_coordinates_share_one_entity():
    resolver = EntityResolver(plugin=PLUGIN)
    resolver.add(
        AliasClaim(
            label="SN 2005A",
            source_id="src-a",
            coordinates={"ra": 10.0, "dec": 20.0},
        )
    )
    result = align_tables(
        [
            table([("SN2005A", "51544.5", "16.5", "0.1", "B")]),
            table(
                [("sn 2005a", "51546.5", "15.8", "0.1", "B")],
                table_id="t2",
                source_id="src-b",
            ),
        ],
        contract=contract(),
        plugin=PLUGIN,
        resolver=resolver,
    )

    entity_ids = {record.entity_id for record in result.records}
    assert len(entity_ids) == 1
    assert result.stats["entities"] == 1


def test_alignment_stats_are_reported():
    result = align_tables(
        [table([("SN2005A", "51544.5", "16.5", "0.1", "B")])],
        contract=contract(),
        plugin=PLUGIN,
    )

    stats = result.stats
    assert stats["tables"] == 1
    assert stats["rows_seen"] == 1
    assert stats["records"] == 1
    assert stats["skipped_rows"] == 0
    payload = result.to_dict()
    assert payload["stats"]["records"] == 1
