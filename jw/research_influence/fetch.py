"""Fetch a real cohort where egress exists, and export it for offline import.

A snapshot is meant to be taken once and reused, so the two halves of the job are
split on purpose:

    # on a machine with network
    python -m jw.research_influence.fetch --query "supernova Ia light curve" \
        --out exports/openalex_snia.jsonl --limit 200

    # anywhere, offline, including inside a sandbox with no egress
    infl_snapshot_import(paths=["exports/openalex_snia.jsonl"], name="snia_real")

This module never decides anything about access: it records what OpenAlex returns
and stops.  Anything the fetch could not obtain stays missing, which is what makes
the downstream metrics worth reading.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .providers.base import ProviderPolicy
from .providers.openalex import search_works
from .transport import PoliteTransport, Transport, UrllibTransport

#: OpenAlex caps a page at 200 works.
MAX_PER_PAGE = 200


def fetch_export(
    query: str,
    *,
    out: str | Path,
    limit: int = MAX_PER_PAGE,
    filter_expression: str = "",
    transport: Transport | None = None,
    policy: ProviderPolicy | None = None,
) -> dict[str, Any]:
    """Search OpenAlex once and write the raw records to a JSONL export.

    Args:
        query: Topic or title query, e.g. ``"supernova Ia light curve"``.
        out: Destination ``.jsonl`` path; parents are created as needed.
        limit: Maximum works to keep (OpenAlex caps a page at 200).
        filter_expression: Optional OpenAlex ``filter=`` expression.
        transport: Transport override; tests inject a fake and never touch the net.
        policy: Provider policy override, e.g. to add a contact email.

    Returns:
        A dict with ``status``, ``path``, ``works`` and the failing reason when the
        provider did not answer.  It never raises on a network failure, because a
        caller that cannot tell "no results" from "no network" will invent data.
    """
    active = transport or PoliteTransport(UrllibTransport())
    settings = policy or ProviderPolicy.from_environment()
    result = search_works(
        query,
        transport=active,
        policy=settings,
        per_page=max(1, min(int(limit or 1), MAX_PER_PAGE)),
        filter_expression=filter_expression,
    )
    if not result.ok:
        return {
            "status": "error",
            "error": f"openalex_{result.status.value}",
            "detail": result.note,
            "url": result.url,
            "http_status": result.http_status,
        }
    works = list((result.payload or {}).get("results") or [])
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(work, ensure_ascii=False, sort_keys=True) for work in works
        )
        + ("\n" if works else ""),
        encoding="utf-8",
    )
    return {
        "status": "ok",
        "path": str(path),
        "works": len(works),
        "query": query,
        "filter": filter_expression,
        "has_citations": sum(
            1 for work in works if work.get("cited_by_count") is not None
        ),
        "next_step": (
            f"Import it offline: infl_snapshot_import(paths=['{path}'], name='...')"
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; prints one JSON object."""
    parser = argparse.ArgumentParser(
        prog="python -m jw.research_influence.fetch",
        description="Search OpenAlex once and export the raw records as JSONL.",
    )
    parser.add_argument("--query", required=True, help="Topic or title query.")
    parser.add_argument("--out", required=True, help="Destination .jsonl path.")
    parser.add_argument("--limit", type=int, default=MAX_PER_PAGE, help="Max works.")
    parser.add_argument("--filter", default="", help="OpenAlex filter expression.")
    args = parser.parse_args(argv)
    payload = fetch_export(
        args.query,
        out=args.out,
        limit=args.limit,
        filter_expression=args.filter,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":  # pragma: no cover - exercised through main() in tests
    sys.exit(main())
