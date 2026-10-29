"""The citation network must be measured, never invented."""

from __future__ import annotations

from pathlib import Path

from jw.research_influence.citation import (
    bare_work_id,
    citing_works,
    citation_profile,
    cohort_pagerank,
    pagerank,
    percentile_of,
    reference_quality,
    resolve_works,
    self_citation_count,
)
from jw.research_influence.contracts import AuthorRecord, PaperRecord, ReferenceRecord
from jw.research_influence.providers.base import ProviderPolicy
from tests.research_influence.conftest import FakeTransport


def _work(
    work_id: str,
    *,
    citations: int = 10,
    references: tuple[str, ...] = (),
    authors: tuple[str, ...] = ("A Author",),
    doi: str = "",
) -> dict:
    """One OpenAlex-shaped work payload."""
    return {
        "id": f"https://openalex.org/{work_id}",
        "doi": doi,
        "title": f"Work {work_id}",
        "publication_year": 2015,
        "cited_by_count": citations,
        "referenced_works": [f"https://openalex.org/{item}" for item in references],
        "authorships": [{"author": {"display_name": name}} for name in authors],
    }


def _page(works: list[dict], count: int | None = None) -> dict:
    return {
        "meta": {"count": count if count is not None else len(works)},
        "results": works,
    }


def _policy() -> ProviderPolicy:
    return ProviderPolicy(allow_network=True, email="operator@example.org")


def test_bare_work_id_accepts_iris_and_rejects_junk():
    """The API returns full IRIs; everything downstream wants the bare id."""
    assert bare_work_id("https://openalex.org/W123") == "W123"
    assert bare_work_id("W9") == "W9"
    assert bare_work_id("10.1093/mnras/stv1947") == ""
    assert bare_work_id("") == ""


def test_resolve_works_batches_ids_and_dois():
    """A mix of work ids and DOIs resolves in one call, keyed by work id."""
    transport = FakeTransport(
        {
            "filter=ids.openalex:W2|W1": (
                200,
                _page([_work("W1"), _work("W2")]),
                "application/json",
            ),
            "filter=doi:10.1093/mnras/stv1947": (
                200,
                _page([_work("W3", doi="10.1093/mnras/stv1947")]),
                "application/json",
            ),
        }
    )
    resolved = resolve_works(
        ["W2", "https://openalex.org/W1", "10.1093/MNRAS/STV1947"],
        transport=transport,
        policy=_policy(),
    )
    assert set(resolved) == {"W1", "W2", "W3"}
    assert resolved["W3"]["doi"] == "10.1093/mnras/stv1947"


def test_unresolved_works_are_absent_not_zero():
    """A failed lookup is missing data; it must not become a 0-citation work."""
    transport = FakeTransport(
        {"filter=ids.openalex:W1": (503, {"error": "down"}, "application/json")}
    )
    assert resolve_works(["W1"], transport=transport, policy=_policy()) == {}


def test_citing_works_reports_the_total_and_a_status():
    """The neighbourhood is a sample; the total count is what the reader needs."""
    transport = FakeTransport(
        {
            "filter=cites:W7": (
                200,
                _page([_work("W8", citations=4), _work("W9", citations=40)], count=2),
                "application/json",
            )
        }
    )
    works, total, status = citing_works(
        "https://openalex.org/W7", transport=transport, policy=_policy()
    )
    assert status == "ok"
    assert total == 2
    assert [work["work_id"] for work in works] == ["W8", "W9"]


def test_citing_works_without_a_work_id_is_not_a_network():
    """Nothing to look up is a status, not an empty result with a count of zero."""
    transport = FakeTransport({})
    works, total, status = citing_works("10.1/x", transport=transport, policy=_policy())
    assert (works, total, status) == ((), 0, "no_work_id")
    assert transport.calls == []


def test_pagerank_puts_mass_on_the_cited_paper():
    """A paper everything cites must outrank the papers citing it."""
    ranks = pagerank(["a", "b", "c"], [("a", "c"), ("b", "c")])
    assert ranks["c"] > ranks["a"]
    assert ranks["c"] > ranks["b"]
    assert abs(sum(ranks.values()) - 1.0) < 1e-9


def test_pagerank_of_an_empty_graph_is_empty():
    """No nodes is not a division by zero."""
    assert pagerank([], []) == {}


def test_percentile_of_uses_the_share_at_or_below():
    """The percentile is a position in the pool, not a rescaled raw value."""
    assert percentile_of([1, 2, 3, 4], 3) == 75.0
    assert percentile_of([], 3) == 0.0


def test_reference_quality_counts_the_top_decile_share():
    """A paper citing well-cited work is not the same as one citing obscurity."""
    mean, median, share = reference_quality(
        [{"cited_by_count": 100}, {"cited_by_count": 10}, {"cited_by_count": 1}],
        percentile_pool=[1, 10, 100, 1000],
    )
    assert round(mean, 2) == 37.0
    assert median == 10.0
    assert share == 1 / 3


def test_self_citation_is_a_lower_bound_from_surnames():
    """Only surnames are compared, so the count can only understate self-citation."""
    paper = _paper("p1", authors=("Jane Kowalski", "Bo Li"))
    citing = [
        {"work_id": "W2", "authors": ("J. Kowalski",)},
        {"work_id": "W3", "authors": ("Someone Else",)},
    ]
    assert self_citation_count(paper, citing) == 1
    assert self_citation_count(_paper("p2", authors=()), citing) == 0


def test_cohort_pagerank_ignores_papers_whose_references_are_unresolved():
    """A paper with no resolved references has no outgoing edges, so no rank."""
    papers = [
        _paper("p1", work_id="W1"),
        _paper("p2", work_id="W2"),
    ]
    resolved = {"W1": {"referenced_works": ("W3",)}, "W3": {"referenced_works": ()}}
    ranks = cohort_pagerank(papers, resolved)
    assert set(ranks) == {"W1", "W3"}
    assert ranks["W3"] > ranks["W1"]


def test_profile_reports_partial_reference_resolution():
    """The share of references we could not resolve is stated, not hidden."""
    paper = _paper("p1", work_id="W1", references=("W4", "W5"))
    profile = citation_profile(
        paper,
        resolved={
            "W1": {"referenced_works": ("W4",), "cited_by_count": 3},
            "W4": {"cited_by_count": 50},
        },
        citing=[{"work_id": "W6", "cited_by_count": 7}],
        citing_total=1,
        rank=0.1,
        rank_pool=[0.1, 0.2],
    )
    assert profile.reference_count == 2
    assert profile.resolved_references == 1
    assert profile.median_referenced_citations == 50.0
    assert profile.citing_count == 1
    assert profile.mean_citing_citations == 7.0
    assert profile.pagerank_percentile == 50.0
    assert profile.usable
    assert "reference_resolution_partial" in profile.notes


def test_profile_without_data_says_so():
    """An empty profile must be readable as "not measured"."""
    profile = citation_profile(_paper("p1"), resolved={}, status="skipped_offline")
    assert not profile.usable
    assert "skipped_offline" in profile.summary_lines()[0]
    assert profile.to_dict()["status"] == "skipped_offline"


def _paper(
    paper_id: str,
    *,
    work_id: str = "",
    authors: tuple[str, ...] = ("Jane Kowalski",),
    references: tuple[str, ...] = (),
) -> PaperRecord:
    return PaperRecord(
        paper_id=paper_id,
        title=f"Paper {paper_id}",
        work_id=work_id,
        authors=tuple(
            AuthorRecord(name=name, family=name.split()[-1]) for name in authors
        ),
        references=tuple(
            ReferenceRecord(index=index, raw=value, resolved_work_id=value)
            for index, value in enumerate(references, start=1)
        ),
    )


def _run_with_network(tmp_path, transport, subject):
    """Run the pipeline over a synthetic cohort with a fake index."""
    from jw.research_influence.cohort import synthetic_cohort
    from jw.research_influence.workflow import RunOptions, run_influence

    snapshot = synthetic_cohort(count=60, seed=3, reference_year=2024)
    options = RunOptions(
        reference_year=2024,
        allow_network=True,
        transport=transport,
        subject_ids=(subject.paper_id,),
        citation_network=True,
    )
    return run_influence(workspace=tmp_path, snapshot=snapshot, options=options)


def test_the_run_measures_the_network_when_the_index_answers(tmp_path):
    """A run with a reachable index stores the measurements and prints them."""
    subject = None
    from jw.research_influence.cohort import synthetic_cohort

    cohort = synthetic_cohort(count=60, seed=3, reference_year=2024)
    subject = sorted(
        (paper for paper in cohort.papers if paper.work_id),
        key=lambda paper: (-(paper.cited_by_count or 0), paper.paper_id),
    )[0]
    payload = {
        "filter=cites:": (
            200,
            _page(
                [{"id": "https://openalex.org/W9999", "cited_by_count": 12}], count=1
            ),
            "application/json",
        ),
        "per-page=": (200, _page([]), "application/json"),
    }
    run = _run_with_network(tmp_path, FakeTransport(payload), subject)
    assert run.citations, run.stages.get("citation")
    profile = next(item for item in run.citations if item.paper_id == subject.paper_id)
    assert run.stages["citation"]["state"] == "complete"
    assert profile.status in {"ok", "unavailable"}
    csv_text = (Path(run.package_dir) / "citation.csv").read_text(encoding="utf-8")
    assert "pagerank_percentile" in csv_text.splitlines()[0]
    report = (Path(run.package_dir) / "report.md").read_text(encoding="utf-8")
    assert "在引用网络里的位置" in report


def test_the_run_records_an_offline_network_instead_of_a_zero(tmp_path):
    """Offline must read as "not measured", never as "no citations"."""
    from jw.research_influence.cohort import synthetic_cohort
    from jw.research_influence.workflow import RunOptions, run_influence

    snapshot = synthetic_cohort(count=40, seed=2, reference_year=2024)
    run = run_influence(
        workspace=tmp_path,
        snapshot=snapshot,
        options=RunOptions(reference_year=2024, allow_network=False),
    )
    assert run.stages["citation"]["reason"] == "network_disabled"
    assert all(item.status == "skipped_offline" for item in run.citations)
    report = (Path(run.package_dir) / "report.md").read_text(encoding="utf-8")
    assert "未取得数据" in report


def test_the_network_can_be_switched_off(tmp_path):
    """An operator who does not want network work gets a receipt saying so."""
    from jw.research_influence.cohort import synthetic_cohort
    from jw.research_influence.workflow import RunOptions, run_influence

    snapshot = synthetic_cohort(count=40, seed=2, reference_year=2024)
    run = run_influence(
        workspace=tmp_path,
        snapshot=snapshot,
        options=RunOptions(reference_year=2024, citation_network=False),
    )
    assert run.stages["citation"] == {"state": "skipped", "reason": "disabled"}
    assert run.citations == ()
