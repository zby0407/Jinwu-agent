"""Discoverable routing rules for the existing Evidence verdict validators.

This describes required structure only. It never selects a scientific decision,
an issue owner, acceptance, or a revision on behalf of the independent reviewer.
"""

from __future__ import annotations

from typing import Any, Literal

from jw.research_review import ALL_REVIEW_MODES, REVISION_OWNERS_FOR_MODE
from research_review.contracts import DECISIONS, ISSUE_OWNERS

# The public tool enum shares the established issue-owner vocabulary. Empty is
# retained for non-revision submissions; stage-specific routing stays in the
# original validator and the mode-specific authoring contract below.
ReviewNextOwner = Literal[("", *sorted(ISSUE_OWNERS))]


def verdict_authoring_contract(review_mode: str) -> dict[str, Any]:
    """Return fresh metadata from the same enums used by actual persistence."""
    if review_mode not in ALL_REVIEW_MODES:
        raise ValueError(f"unsupported review_mode: {review_mode}")
    owners = sorted(REVISION_OWNERS_FOR_MODE[review_mode])
    return {
        "version": "evidence-verdict-authoring-v1",
        "review_mode": review_mode,
        "decisions": sorted(DECISIONS),
        "next_owner": {
            "required_when_decision": "revise",
            "allowed_values_when_revise": owners,
            "must_own_at_least_one_issue": True,
            "empty_value_is_not_a_revision_owner": True,
        },
        "issues": {
            "minimum_when_revise": 1,
            "unique_identity_fields": ["rule_id", "claim_ref", "owner"],
            "claim_ref_rule": (
                "Use a stable field-level claim_ref for every distinct defect. "
                "Changing issue_id does not create a distinct issue identity."
            ),
            "owner_values": sorted(ISSUE_OWNERS),
        },
        "routing_schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": sorted(DECISIONS)},
                "issues": {"type": "array"},
                "next_owner": {"type": "string", "enum": ["", *sorted(ISSUE_OWNERS)]},
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"decision": {"const": "revise"}},
                        "required": ["decision"],
                    },
                    "then": {
                        "required": ["issues", "next_owner"],
                        "properties": {
                            "issues": {"minItems": 1},
                            "next_owner": {"enum": owners},
                        },
                    },
                }
            ],
        },
        "instruction": (
            "For revise, supply at least one producer-fixable issue and a nonempty "
            "next_owner from this mode's allowed values; that owner must own at "
            "least one issue. Keep every rule_id/claim_ref/owner identity unique. "
            "The routing schema supplements the complete tool and verdict schemas; "
            "the established validators still decide whether a submission is valid. "
            "All decisions, owners, scientific claims, and limitations remain the "
            "reviewer's explicit choices; none are filled by this guide."
        ),
    }
