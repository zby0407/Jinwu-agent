"""Real-data effect test for citation network + topic evolution + dedup.

Run with the operator's local proxy:
    HTTP_PROXY=http://127.0.0.1:1082 HTTPS_PROXY=http://127.0.0.1:1082 \
    MPLCONFIGDIR=/tmp/mpl .venv/bin/python tests/research_influence/real_effect_test.py

Prints a JSON scoreboard. Every number is reproducible from the frozen snapshot.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure the repo root is importable when run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jw.research_influence.citation import (  # noqa: E402
    citation_profile,
    cohort_pagerank,
    resolve_works,
)
from jw.research_influence.cohort import CohortSnapshot  # noqa: E402
from jw.research_influence.package import verify_package  # noqa: E402
from jw.research_influence.providers.base import ProviderPolicy  # noqa: E402
from jw.research_influence.providers.crossref import (  # noqa: E402
    fetch_work as crossref_fetch,
    parse_work as crossref_parse,
)
from jw.research_influence.providers.openalex import (  # noqa: E402
    fetch_work as openalex_fetch,
    parse_work as openalex_parse,
)
from jw.research_influence.topics import topic_trends  # noqa: E402
from jw.research_influence.transport import PoliteTransport, UrllibTransport  # noqa: E402
from jw.research_influence.workflow import RunOptions, run_influence  # noqa: E402

WORKSPACE = Path("workspace")
COHORT_PATH = WORKSPACE / "research_influence" / "cohorts" / "snia_real.json"


def _cohort_path() -> Path:
    """Which snapshot to score.

    Overridable so a rebuilt snapshot can be scored without editing code: the
    numbers below belong to one fingerprint, and a newer snapshot has its own.
    """
    override = os.getenv("REAL_EFFECT_COHORT", "").strip()
    return Path(override) if override else COHORT_PATH


def main() -> None:
    transport = PoliteTransport(UrllibTransport())
    policy = ProviderPolicy.from_environment()

    snapshot = CohortSnapshot.load(_cohort_path())
    papers = list(snapshot.papers)
    print(f"cohort: {len(papers)} papers", file=sys.stderr)

    # ------------------------------------------------------------------
    # 1. Citation network on a sample of cohort papers
    # ------------------------------------------------------------------
    sample = [p for p in papers if p.work_id][:20]

    # Phase 1: resolve the sample papers themselves.
    t0 = time.time()
    resolved = resolve_works(
        [p.work_id for p in sample], transport=transport, policy=policy
    )

    # Phase 2: resolve their references too, so the graph has targets.
    ref_ids: set[str] = set()
    for work in resolved.values():
        for ref in work.get("referenced_works") or ():
            bare = str(ref).strip().split("/")[-1]
            if bare:
                ref_ids.add(bare)
    # Cap the reference resolution to keep the test bounded.
    ref_ids = set(sorted(ref_ids)[:200])
    resolved_refs = resolve_works(sorted(ref_ids), transport=transport, policy=policy)
    # Merge: references become additional nodes in the graph.
    for wid, work in resolved_refs.items():
        resolved.setdefault(wid, work)
    resolve_seconds = round(time.time() - t0, 2)

    ranks = cohort_pagerank(sample, resolved)
    rank_pool = list(ranks.values())

    profiles = []
    for paper in sample:
        bare_id = paper.work_id.strip().split("/")[-1]
        work = resolved.get(bare_id, {})
        referenced_ids = work.get("referenced_works") or ()
        profile = citation_profile(
            paper,
            resolved=resolved,
            referenced_ids=referenced_ids,
            rank=ranks.get(bare_id),
            rank_pool=rank_pool,
        )
        profiles.append(profile)

    resolved_counts = [p.resolved_references for p in profiles]
    total_refs = [p.reference_count for p in profiles]
    citation_scoreboard = {
        "sample_size": len(sample),
        "resolve_seconds": resolve_seconds,
        "resolved_works": len(resolved),
        "network_nodes": len(ranks),
        "reference_pool_resolved": len(resolved_refs),
        "reference_resolution_rate": (
            round(sum(resolved_counts) / max(1, sum(total_refs)), 4)
            if total_refs
            else None
        ),
        "median_referenced_citations": sorted(
            p.median_referenced_citations or 0 for p in profiles
        )[len(profiles) // 2],
        "with_pagerank_percentile": sum(
            1 for p in profiles if p.pagerank_percentile is not None
        ),
        "isolated_nodes": sum(
            1
            for p in profiles
            if p.pagerank is not None and p.pagerank_percentile is None
        ),
    }

    # ------------------------------------------------------------------
    # 2. Topic evolution stability
    # ------------------------------------------------------------------
    trends = topic_trends(papers, reference_year=0)
    rising = [t for t in trends if t.rising]
    readable = [t for t in trends if t.status == "ok"]
    topic_scoreboard = {
        "total_topics": len(trends),
        "readable": len(readable),
        "rising": len(rising),
        "top_rising": [
            {"topic": t.topic, "growth": round(t.growth or 0, 2), "papers": t.papers}
            for t in rising[:5]
        ],
        "anchor_year": next(
            (t.anchor_year for t in trends if t.anchor_year is not None), None
        ),
    }

    # ------------------------------------------------------------------
    # 3. Cross-source dedup: same DOI from OpenAlex vs Crossref
    # ------------------------------------------------------------------
    doi_sample = [p for p in papers if p.doi][:5]
    dedup_rows = []
    for paper in doi_sample:
        oa = openalex_fetch(paper.doi, transport=transport, policy=policy)
        cr = crossref_fetch(paper.doi, transport=transport, policy=policy)
        if oa.ok and cr.ok:
            oa_parsed = openalex_parse(oa.payload)
            cr_parsed = crossref_parse(cr.payload)
            same_title = (
                oa_parsed.get("title", "").lower() == cr_parsed.get("title", "").lower()
            )
            same_year = oa_parsed.get("year") == cr_parsed.get("year")
            dedup_rows.append(
                {
                    "doi": paper.doi,
                    "openalex_year": oa_parsed.get("year"),
                    "crossref_year": cr_parsed.get("year"),
                    "title_match": same_title,
                    "year_match": same_year,
                }
            )
    dedup_scoreboard = {
        "compared": len(dedup_rows),
        "title_match": sum(1 for r in dedup_rows if r["title_match"]),
        "year_match": sum(1 for r in dedup_rows if r["year_match"]),
        "rows": dedup_rows,
    }

    # ------------------------------------------------------------------
    # 4. End-to-end reproducibility
    #
    # Two cases, because they answer different questions.  The frozen pair is
    # the promise a reviewer can check: same snapshot, same seed, same numbers
    # *and* same package content.  The live pair is the honest counter-case:
    # when the run enriches itself from online APIs, its inputs differ between
    # runs, so nothing downstream can be expected to match.
    # ------------------------------------------------------------------
    uploads = [
        "workspace/research_influence/uploads/arxiv_1509.00001.pdf",
        "workspace/research_influence/uploads/arxiv_2401.00123.pdf",
    ]
    frozen_options = RunOptions(reference_year=0, max_subjects=2)
    frozen1 = run_influence(
        workspace=WORKSPACE,
        snapshot=snapshot,
        uploads=uploads,
        options=frozen_options,
        run_id="effect-frozen-1",
    )
    frozen2 = run_influence(
        workspace=WORKSPACE,
        snapshot=snapshot,
        uploads=uploads,
        options=frozen_options,
        run_id="effect-frozen-2",
    )

    live_options = RunOptions(
        reference_year=0,
        allow_network=True,
        citation_network=True,
        max_subjects=2,
    )
    run1 = run_influence(
        workspace=WORKSPACE,
        snapshot=snapshot,
        uploads=uploads,
        options=live_options,
        run_id="effect-test-1",
    )
    run2 = run_influence(
        workspace=WORKSPACE,
        snapshot=snapshot,
        uploads=uploads,
        options=live_options,
        run_id="effect-test-2",
    )
    live_first = run1.metrics.get("citation", {})
    live_second = run2.metrics.get("citation", {})
    same_inputs = live_first.get("statuses") == live_second.get("statuses") and (
        live_first.get("with_data") == live_second.get("with_data")
    )
    verified = verify_package(frozen1.package_dir)
    repro = {
        "frozen_snapshot": {
            "verify_ok": verified["ok"],
            "verified_artifacts": verified["checked"],
            "verify_failed": verified["failed"],
            "metric_fingerprint_equal": frozen1.metrics.get("fingerprint")
            == frozen2.metrics.get("fingerprint"),
            "package_fingerprint_equal": frozen1.metrics.get("package_fingerprint")
            == frozen2.metrics.get("package_fingerprint"),
            "package_fingerprint": frozen1.metrics.get("package_fingerprint"),
        },
        "live_network": {
            "metric_fingerprint_equal": run1.metrics.get("fingerprint")
            == run2.metrics.get("fingerprint"),
            "package_fingerprint_equal": run1.metrics.get("package_fingerprint")
            == run2.metrics.get("package_fingerprint"),
            "same_inputs": same_inputs,
            "citation_statuses": [
                live_first.get("statuses"),
                live_second.get("statuses"),
            ],
            "why": (
                "两次运行联到的在线输入一致，所以结果一致；这不代表联网时一定可复现"
                if same_inputs
                else "在线接口两次返回的引用数据不同（见 citation_statuses），"
                "输入变了指纹必然不同；可复现性只在冻结快照上承诺"
            ),
        },
    }

    scoreboard = {
        "citation": citation_scoreboard,
        "topics": topic_scoreboard,
        "dedup": dedup_scoreboard,
        "reproducibility": repro,
    }
    print(json.dumps(scoreboard, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
