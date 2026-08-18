"""知识库 contract 测试：条目 schema 与生命周期状态机。只用标准库 unittest。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from knowledge_base.contracts import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    CONTENT_FIELDS,
    ENTRY_TYPES,
    STATUSES,
    ContractError,
    check_status_transition,
    validate_content,
    validate_entry,
)


def make_entry(**overrides):
    entry = {
        "id": "kb_concept_solar_cycle_001",
        "type": "concept",
        "title": "太阳活动周",
        "content": {"definition": "太阳磁场活动约 11 年的准周期变化。"},
        "source_type": "textbook",
        "source_ref": "Charbonneau 2010, LRSP 7, 3",
        "confidence": "high",
        "status": "candidate",
        "valid_range": "all observed cycles",
        "related_ids": [],
        "provenance": {},
        "version": 1,
    }
    entry.update(overrides)
    return entry


class TestEntrySchema(unittest.TestCase):
    def test_valid_entries_for_all_types(self):
        valid_content = {
            "concept": {"definition": "d", "physical_notes": "p", "see_also": ["kb_concept_x_001"]},
            "mechanism": {
                "claim": "c",
                "supporting_evidence": "s",
                "counter_evidence": "ce",
                "controversy": "cv",
                "testable_predictions": ["p1", "p2"],
            },
            "data_source": {
                "collection_method": "m",
                "known_biases": "b",
                "calibration_history": "h",
                "coverage": "c",
            },
            "experiment_paradigm": {"design": "d", "metrics": "m", "pitfalls": "p"},
            "hypothesis_template": {"structure": "s", "example": "e", "applicable_when": "a"},
            "finding": {"statement": "s", "run_id": "run_1", "effect_size": "e", "uncertainty": "u"},
            "counterexample": {"statement": "s", "run_id": "run_1"},
        }
        for entry_type in sorted(ENTRY_TYPES):
            with self.subTest(entry_type=entry_type):
                slug = entry_type.replace("_", "-")
                entry = make_entry(
                    id=f"kb_{entry_type}_{slug}_001",
                    type=entry_type,
                    content=valid_content[entry_type],
                )
                normalized = validate_entry(entry)
                self.assertEqual(normalized["type"], entry_type)

    def test_missing_required_content_field_rejected(self):
        for entry_type, spec in CONTENT_FIELDS.items():
            for field in spec["required"]:
                with self.subTest(entry_type=entry_type, field=field):
                    content = {required: "x" for required in spec["required"] if required != field}
                    with self.assertRaises(ContractError) as ctx:
                        validate_content(entry_type, content)
                    self.assertEqual(ctx.exception.error_code, "content_required_field_missing")
                    self.assertEqual(ctx.exception.field_path, f"content.{field}")

    def test_unknown_content_field_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            validate_content("concept", {"definition": "d", "bogus": "x"})
        self.assertEqual(ctx.exception.error_code, "unknown_content_field")

    def test_list_field_items_coerced_to_strings(self):
        normalized = validate_content(
            "mechanism", {"claim": "c", "testable_predictions": ["p1", 2]}
        )
        self.assertEqual(normalized["testable_predictions"], ["p1", "2"])

    def test_string_coerced_to_list_for_list_fields(self):
        normalized = validate_content(
            "mechanism", {"claim": "c", "testable_predictions": "single prediction"}
        )
        self.assertEqual(normalized["testable_predictions"], ["single prediction"])

    def test_empty_optional_fields_dropped(self):
        normalized = validate_content(
            "concept", {"definition": "d", "physical_notes": "", "see_also": []}
        )
        self.assertNotIn("physical_notes", normalized)
        self.assertNotIn("see_also", normalized)

    def test_invalid_enum_fields_rejected(self):
        cases = [
            ({"type": "wiki"}, "type"),
            ({"source_type": "web"}, "source_type"),
            ({"confidence": "very-high"}, "confidence"),
            ({"status": "published"}, "status"),
        ]
        for overrides, field in cases:
            with self.subTest(field=field):
                with self.assertRaises(ContractError) as ctx:
                    validate_entry(make_entry(**overrides))
                self.assertEqual(ctx.exception.field_path, field)

    def test_invalid_id_rejected(self):
        for bad_id in ["", "concept_x", "kb_thing_x_001", "kb_concept_x_1", "kb_concept__001"]:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ContractError) as ctx:
                    validate_entry(make_entry(id=bad_id))
                self.assertEqual(ctx.exception.error_code, "invalid_entry_id")

    def test_id_type_must_match_entry_type(self):
        with self.assertRaises(ContractError) as ctx:
            validate_entry(make_entry(id="kb_mechanism_solar_cycle_001", type="concept"))
        self.assertEqual(ctx.exception.error_code, "entry_id_type_mismatch")

    def test_empty_title_or_source_ref_rejected(self):
        with self.assertRaises(ContractError):
            validate_entry(make_entry(title="  "))
        with self.assertRaises(ContractError):
            validate_entry(make_entry(source_ref=""))

    def test_error_metadata_shape(self):
        try:
            validate_entry(make_entry(title=""))
        except ContractError as exc:
            self.assertTrue(exc.error_code)
            self.assertEqual(exc.field_path, "title")
            self.assertTrue(exc.suggestion)
        else:
            self.fail("expected ContractError")


class TestStatusMachine(unittest.TestCase):
    def test_legal_transitions_complete_set(self):
        legal = [
            ("candidate", "canonical"),
            ("candidate", "deprecated"),
            ("candidate", "superseded"),
            ("canonical", "deprecated"),
            ("canonical", "superseded"),
            ("deprecated", "superseded"),
            ("superseded", "deprecated"),
        ]
        for from_status, to_status in legal:
            with self.subTest(f"{from_status}->{to_status}"):
                check_status_transition(from_status, to_status)  # no raise
        self.assertEqual(
            {frozenset(v) for v in ALLOWED_TRANSITIONS.values()}.__len__(), 4
        )

    def test_illegal_transitions_complete_set(self):
        legal = {
            ("candidate", "canonical"),
            ("candidate", "deprecated"),
            ("candidate", "superseded"),
            ("canonical", "deprecated"),
            ("canonical", "superseded"),
            ("deprecated", "superseded"),
            ("superseded", "deprecated"),
        }
        for from_status in sorted(STATUSES):
            for to_status in sorted(STATUSES):
                if (from_status, to_status) in legal:
                    continue
                with self.subTest(f"{from_status}->{to_status}"):
                    with self.assertRaises(ContractError):
                        check_status_transition(from_status, to_status)

    def test_canonical_never_returns_to_candidate(self):
        with self.assertRaises(ContractError) as ctx:
            check_status_transition("canonical", "candidate")
        self.assertEqual(ctx.exception.error_code, "transition_to_candidate_forbidden")

    def test_unknown_status_rejected(self):
        with self.assertRaises(ContractError):
            check_status_transition("archived", "canonical")
        with self.assertRaises(ContractError):
            check_status_transition("candidate", "archived")


if __name__ == "__main__":
    unittest.main()
