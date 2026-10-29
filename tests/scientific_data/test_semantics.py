"""Tests for column profiling and canonical field mapping."""

from __future__ import annotations

from jw.scientific_data.contracts import (
    ConfidenceBand,
    DataRequirementContract,
    ExtractedTable,
    FieldMapping,
    IssueCode,
    IssueSeverity,
    ObservationField,
    RepairMode,
)
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.semantics import (
    AMBIGUITY_MARGIN,
    MAPPING_MIN_SCORE,
    column_profiles,
    described_mapping,
    map_table,
    profile_column,
    propose_mappings,
    score_column,
)


def table(columns, rows, *, table_id="t1", source_id="src1"):
    return ExtractedTable(
        table_id=table_id,
        source_id=source_id,
        columns=tuple(columns),
        rows=[dict(zip(columns, row, strict=True)) for row in rows],
        header_row=1,
    )


def contract(**kwargs):
    payload = {
        "contract_id": "c1",
        "research_goal": "Ia 型超新星光变曲线",
        "domain_plugin": "snia_light_curve",
    }
    payload.update(kwargs)
    return DataRequirementContract(**payload)


# -- profiling --------------------------------------------------------------


def test_profile_counts_present_missing_and_numeric_share():
    profile = profile_column("mag", ["15.4", "", "15.1", "n/a", "16.0"])
    assert profile.present == 4
    assert profile.missing == 1
    assert profile.numeric == 3
    assert profile.numeric_share == 3 / 4
    assert profile.distinct == 4
    assert profile.value_shape == "mixed"


def test_profile_reads_unit_from_bracketed_header():
    profile = profile_column("flux (mJy)", ["0.21", "0.35"])
    assert profile.inferred_unit == "mJy"
    assert profile.unit_source == "header"
    assert profile.value_shape == "number"


def test_profile_reads_unit_from_values():
    profile = profile_column("value", ["1.5 mag", "2.5 mag"])
    assert profile.inferred_unit == "mag"
    assert profile.unit_source == "values"


def test_profile_handles_cjk_header_and_unit():
    profile = profile_column("星等 (mag)", ["15.4", "15.1"])
    assert profile.inferred_unit == "mag"
    assert profile.value_shape == "number"


def test_profile_detects_iso_and_monthly_date_columns():
    assert profile_column("date", ["2020-01-02"]).value_shape == "date"
    assert profile_column("month", ["2020-01", "2020-02"]).value_shape == "date"
    assert profile_column("month", ["2020-01"]).time_system == "iso"


def test_profile_keeps_only_requested_samples():
    profile = profile_column("band", [f"b{i}" for i in range(20)], limit=3)
    assert profile.samples == ("b0", "b1", "b2")
    assert profile.distinct == 20


def test_uncertainty_marker_ignores_plain_words():
    assert profile_column("e_mag", ["0.03"]).has_uncertainty_marker
    assert profile_column("err_mag", ["0.03"]).has_uncertainty_marker
    assert profile_column("sigma", ["0.03"]).has_uncertainty_marker
    assert not profile_column("date", ["2020-01-02"]).has_uncertainty_marker
    assert not profile_column("exposure", ["30"]).has_uncertainty_marker


def test_column_profiles_covers_every_column():
    data = table(["mjd", "mag"], [["58000.1", "15.4"], ["58001.1", "15.2"]])
    profiles = column_profiles(data)
    assert [profile.column for profile in profiles] == ["mjd", "mag"]
    assert all(profile.present == 2 for profile in profiles)


# -- scoring ----------------------------------------------------------------


def test_exact_header_beats_hint():
    plugin = get_plugin("snia_light_curve")
    spec = plugin.observable("apparent_magnitude")
    exact_score, exact_reasons = score_column(
        profile_column("apparent_magnitude", ["15.4"]), spec, plugin=plugin
    )
    hint_score, hint_reasons = score_column(
        profile_column("mag", ["15.4"]), spec, plugin=plugin
    )
    assert exact_score > hint_score
    assert "match:exact_head" in exact_reasons
    assert "match:listed_hint" in hint_reasons


def test_uncertainty_column_is_penalised_for_value_fields():
    plugin = get_plugin("snia_light_curve")
    spec = plugin.observable("apparent_magnitude")
    profile = profile_column("e_mag", ["0.03", "0.02"])
    score, reasons = score_column(profile, spec, plugin=plugin)
    assert "uncertainty_column_not_value" in reasons
    assert score < 0.8


def test_unit_dimension_mismatch_is_recorded():
    plugin = get_plugin("solar_cycle")
    spec = plugin.observable("f107_flux")
    profile = profile_column("F10.7 (gauss)", ["98.2", "101.4"])
    score, reasons = score_column(profile, spec, plugin=plugin)
    assert "unit_dimension_mismatch:gauss!=sfu" in reasons
    assert score < MAPPING_MIN_SCORE + 0.3


def test_unreadable_time_column_loses_score():
    plugin = get_plugin("solar_cycle")
    spec = plugin.observable("time")
    profile = profile_column("time", ["soon", "later"])
    _, reasons = score_column(profile, spec, plugin=plugin)
    assert "time_system_unreadable" in reasons


# -- mapping ----------------------------------------------------------------


def test_map_table_assigns_standard_light_curve_columns():
    data = table(
        ["MJD", "mag", "e_mag", "Filter"],
        [["58000.1", "15.42", "0.03", "B"], ["58001.2", "15.01", "0.02", "V"]],
    )
    mappings, issues = map_table(
        data, contract=contract(), plugin=get_plugin("snia_light_curve")
    )
    assigned = {mapping.source_column: mapping.observable for mapping in mappings}
    assert assigned == {
        "MJD": "time",
        "mag": "apparent_magnitude",
        "e_mag": "magnitude_uncertainty",
        "Filter": "band",
    }
    assert all(mapping.applied for mapping in mappings)
    assert all(mapping.confidence is ConfidenceBand.HIGH for mapping in mappings)
    assert issues == ()


def test_map_table_records_unit_transformation():
    data = table(["flux (Jy)"], [["2.3e-4"], ["3.1e-4"]])
    mappings, _ = map_table(
        data, contract=contract(), plugin=get_plugin("snia_light_curve")
    )
    flux = mappings[0]
    assert flux.observable == "flux_density"
    assert flux.source_unit == "Jy"
    assert flux.target_unit == "mJy"
    assert flux.transformation == "unit_scale:Jy->mJy"


def test_near_tie_between_fields_escalates_to_human():
    request = contract(
        observables=(
            ObservationField(name="apparent_magnitude"),
            ObservationField(name="abs_magnitude", aliases=("mag",)),
        )
    )
    data = table(["mag"], [["15.4"], ["15.1"]])
    plugin = get_plugin("snia_light_curve")
    proposals = propose_mappings(data, contract=request, plugin=plugin)
    assert proposals[0].ambiguous
    assert proposals[0].alternative in {"abs_magnitude", "apparent_magnitude"}
    assert abs(proposals[0].score - proposals[0].alternative_score) <= AMBIGUITY_MARGIN

    mappings, issues = map_table(data, contract=request, plugin=plugin)
    assert mappings[0].applied is False
    assert mappings[0].confidence is ConfidenceBand.LOW
    ambiguous = [
        issue for issue in issues if issue.code is IssueCode.FIELD_MAPPING_AMBIGUOUS
    ]
    assert len(ambiguous) == 1
    assert ambiguous[0].severity is IssueSeverity.ERROR
    assert ambiguous[0].repair_mode is RepairMode.HUMAN_REQUIRED
    assert ambiguous[0].blocking is False
    assert ambiguous[0].evidence["candidates"] == [
        proposals[0].observable,
        proposals[0].alternative,
    ]


def test_weak_match_is_reported_as_unmapped_not_forced():
    request = contract(
        observables=(ObservationField(name="magnitude_uncertainty"),),
    )
    data = table(["mag (gauss)"], [["0.03"], ["0.02"]])
    mappings, issues = map_table(
        data, contract=request, plugin=get_plugin("snia_light_curve")
    )
    assert all(not mapping.applied for mapping in mappings)
    weak = [issue for issue in issues if issue.code is IssueCode.FIELD_UNMAPPED]
    assert weak and weak[0].severity is IssueSeverity.WARNING
    assert weak[0].repair_mode is RepairMode.SUGGEST_ONLY


def test_column_matching_nothing_is_preserved_but_unused():
    data = table(["mjd", "observer notes"], [["58000.1", "cloudy"]])
    mappings, issues = map_table(
        data, contract=contract(), plugin=get_plugin("snia_light_curve")
    )
    assert [mapping.source_column for mapping in mappings] == ["mjd"]
    unmapped = [issue for issue in issues if issue.code is IssueCode.FIELD_UNMAPPED]
    assert len(unmapped) == 1
    assert unmapped[0].subject["field"] == "observer notes"
    assert unmapped[0].severity is IssueSeverity.INFO


def test_metadata_and_padding_columns_are_skipped():
    data = table(["mjd", "_row_index", "extra_1"], [["58000.1", "2", "junk"]])
    mappings, issues = map_table(
        data, contract=contract(), plugin=get_plugin("snia_light_curve")
    )
    assert [mapping.source_column for mapping in mappings] == ["mjd"]
    assert issues == ()


def test_map_table_accepts_contract_fields_unknown_to_plugin():
    request = contract(
        observables=(ObservationField(name="abs_magnitude", aliases=("AbsMag",)),)
    )
    data = table(["AbsMag"], [["-17.8"]])
    mappings, _ = map_table(
        data, contract=request, plugin=get_plugin("snia_light_curve")
    )
    assert mappings[0].observable == "abs_magnitude"


def test_mapping_ids_are_stable_and_unique():
    data = table(["mjd", "mag (mag)"], [["58000.1", "15.4"]])
    first, _ = map_table(
        data, contract=contract(), plugin=get_plugin("snia_light_curve")
    )
    second, _ = map_table(
        data, contract=contract(), plugin=get_plugin("snia_light_curve")
    )
    assert [mapping.mapping_id for mapping in first] == [
        mapping.mapping_id for mapping in second
    ]
    assert len({mapping.mapping_id for mapping in first}) == len(first)
    assert first[1].mapping_id == "map:t1:mag_mag"


def test_described_mapping_is_csv_ready():
    mapping = FieldMapping(
        mapping_id="map:t1:mag",
        source_id="src1",
        table_id="t1",
        source_column="mag",
        observable="apparent_magnitude",
        target_unit="mag",
        source_unit="mag",
        confidence=ConfidenceBand.HIGH,
        evidence="match:exact_head",
        applied=True,
    )
    row = described_mapping(mapping)
    assert row["observable"] == "apparent_magnitude"
    assert row["applied"] is True
    assert row["confidence"] == "high"
    assert row["source"] == "src1"


def test_empty_column_is_not_proposed():
    data = table(["mjd", "mag"], [["58000.1", ""], ["58001.1", "  "]])
    proposals = propose_mappings(
        data, contract=contract(), plugin=get_plugin("snia_light_curve")
    )
    assert [proposal.column for proposal in proposals] == ["mjd"]
