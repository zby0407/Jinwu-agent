"""科学假设 Agent 1.0 的确定性合同与 Pi Tool 后端。"""

from .contracts import (
    OUTCOME_VERSION,
    PORTFOLIO_VERSION,
    REQUEST_VERSION,
    RESPONSE_VERSION,
    ContractError,
    canonical_json_sha256,
    validate_evidence_bind,
    validate_hypothesis_portfolio,
    validate_hypothesis_request,
    validate_hypothesis_response,
)
from .harness import (
    EvidenceRegister,
    build_hypothesis_brief,
    build_natural_hypothesis_request,
    collect_hypothesis_semantic_errors,
    compile_hypothesis_portfolio,
    freeze_hypothesis_portfolio,
    preflight_hypothesis_response,
    render_hypothesis_portfolio_markdown,
    render_nonportfolio_response_markdown,
)
from .reader_view import render_hypothesis_reader_markdown
from .tail_search import (
    TAIL_REVIEW_VERSION,
    candidate_pool_sha256,
    tail_review_is_current,
    validate_and_select_tail_review,
)

__all__ = [
    "OUTCOME_VERSION",
    "PORTFOLIO_VERSION",
    "REQUEST_VERSION",
    "RESPONSE_VERSION",
    "TAIL_REVIEW_VERSION",
    "ContractError",
    "EvidenceRegister",
    "build_hypothesis_brief",
    "build_natural_hypothesis_request",
    "candidate_pool_sha256",
    "canonical_json_sha256",
    "collect_hypothesis_semantic_errors",
    "compile_hypothesis_portfolio",
    "freeze_hypothesis_portfolio",
    "preflight_hypothesis_response",
    "render_hypothesis_portfolio_markdown",
    "render_hypothesis_reader_markdown",
    "render_nonportfolio_response_markdown",
    "tail_review_is_current",
    "validate_and_select_tail_review",
    "validate_evidence_bind",
    "validate_hypothesis_portfolio",
    "validate_hypothesis_request",
    "validate_hypothesis_response",
]
