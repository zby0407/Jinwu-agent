"""Policy: what this run is allowed to touch, and why.

Acquisition is the only stage that leaves the machine, so it is the only stage
that needs a gate. The gate is deny-by-default: no network unless a policy says
so, no host outside the allowlist, no fetch beyond the budget.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .contracts import DataRequirementContract

__all__ = ["AcquisitionPolicy", "PolicyDecision"]

_LOCAL_SCHEMES = ("file", "")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The verdict for one URL."""

    url: str
    allowed: bool
    reason: str = ""
    host: str = ""

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "allowed": self.allowed,
            "reason": self.reason,
            "host": self.host,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    """Allowlist-first network policy with hard budgets."""

    allow_network: bool = False
    allowed_hosts: tuple[str, ...] = ()
    denied_hosts: tuple[str, ...] = ()
    allowed_schemes: tuple[str, ...] = ("https", "http", "file")
    max_sources: int = 40
    max_fetches: int = 60
    max_bytes: int = 50_000_000
    max_table_rows: int = 200_000
    user_agent: str = "jw-scientific-data/1.0"
    timeout_seconds: float = 30.0
    notes: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_contract(
        cls,
        contract: DataRequirementContract | None = None,
        **overrides: Any,
    ) -> AcquisitionPolicy:
        """Start from the contract budget so a run cannot exceed what was asked."""
        budget = (contract.budget if contract is not None else {}) or {}
        defaults: dict[str, Any] = {
            "max_sources": int(budget.get("max_sources", 40)),
            "max_fetches": int(budget.get("max_fetches", 60)),
            "max_table_rows": int(budget.get("max_tables", 200_000)),
        }
        defaults.update(overrides)
        return cls(**defaults)

    def host_of(self, url: str) -> str:
        try:
            return (urlsplit(url).hostname or "").lower()
        except ValueError:
            return ""

    def scheme_of(self, url: str) -> str:
        try:
            return (urlsplit(url).scheme or "").lower()
        except ValueError:
            return ""

    def allows(self, url: str) -> PolicyDecision:
        """Decide whether a single URL may be fetched."""
        host = self.host_of(url)
        scheme = self.scheme_of(url)
        if any(
            host == denied or host.endswith(f".{denied}")
            for denied in self.denied_hosts
        ):
            return PolicyDecision(url, False, "denied_host", host)
        if scheme not in self.allowed_schemes:
            return PolicyDecision(url, False, "scheme_not_allowed", host)
        if scheme in _LOCAL_SCHEMES:
            return PolicyDecision(url, True, "local_file", host)
        if not self.allow_network:
            return PolicyDecision(url, False, "network_disabled", host)
        if self.allowed_hosts and not any(
            host == allowed or host.endswith(f".{allowed}")
            for allowed in self.allowed_hosts
        ):
            return PolicyDecision(url, False, "host_not_allowlisted", host)
        return PolicyDecision(url, True, "ok", host)

    def filter(self, urls: Iterable[str]) -> tuple[list[str], list[PolicyDecision]]:
        """Split URLs into the ones allowed and the ones refused."""
        allowed: list[str] = []
        refused: list[PolicyDecision] = []
        for url in urls:
            decision = self.allows(url)
            if decision.allowed:
                allowed.append(url)
            else:
                refused.append(decision)
        return allowed, refused

    def fetch_budget_left(self, used: int) -> int:
        return max(0, self.max_fetches - used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_network": self.allow_network,
            "allowed_hosts": list(self.allowed_hosts),
            "denied_hosts": list(self.denied_hosts),
            "allowed_schemes": list(self.allowed_schemes),
            "max_sources": self.max_sources,
            "max_fetches": self.max_fetches,
            "max_bytes": self.max_bytes,
            "max_table_rows": self.max_table_rows,
            "user_agent": self.user_agent,
            "timeout_seconds": self.timeout_seconds,
            "notes": dict(self.notes),
        }

    def describe(self) -> str:
        """One-paragraph explanation for the data quality report."""
        if not self.allow_network:
            return (
                "Network access was disabled for this run, so only local files "
                "and already cached copies were read."
            )
        hosts = ", ".join(self.allowed_hosts) if self.allowed_hosts else "any host"
        return (
            f"Network access was limited to {hosts} with at most "
            f"{self.max_fetches} fetches and {self.max_bytes} bytes per file."
        )
