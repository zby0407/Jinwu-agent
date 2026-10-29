"""Scholarly source adapters.

Each adapter is a thin, testable translation between one provider's wire format
and the kernel's typed records.  Adapters never decide policy: whether a route
is allowed, whether a credential exists and how long to wait are all inputs.
"""

from __future__ import annotations

from . import arxiv, crossref, europepmc, openalex, optional, unpaywall

__all__ = ["arxiv", "crossref", "europepmc", "openalex", "optional", "unpaywall"]
