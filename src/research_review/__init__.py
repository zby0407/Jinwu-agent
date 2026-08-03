"""Research review 2.0 contracts used by the integrated solar harness."""

from .adapters import adapt_v1_producer_output
from .contracts import (
    ARTIFACT_VERSION,
    CLAIM_VERSION,
    RUN_STATE_VERSION,
    VERDICT_VERSION,
    ContractError,
    build_research_artifact,
    build_review_verdict,
    canonical_json_sha256,
    issue_fingerprint,
    validate_research_artifact,
    validate_review_verdict,
    validate_run_state,
)
from .policies import policy_registry

__all__ = [
    "ARTIFACT_VERSION",
    "CLAIM_VERSION",
    "RUN_STATE_VERSION",
    "VERDICT_VERSION",
    "ContractError",
    "adapt_v1_producer_output",
    "build_research_artifact",
    "build_review_verdict",
    "canonical_json_sha256",
    "issue_fingerprint",
    "policy_registry",
    "validate_research_artifact",
    "validate_review_verdict",
    "validate_run_state",
]
