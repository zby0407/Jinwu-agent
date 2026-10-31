"""Tool surface: the influence pipeline reachable as JSON tools.

No test here touches the network.  The two tools that would make a request must
report ``network_disabled`` and explain the refusal instead of sending anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jw.tools import influence as tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_workspace", lambda config: tmp_path)
    return tmp_path


def _load(text: str) -> dict:
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


def _demo_cohort(tmp_path, name="demo", count=60, seed=3) -> Path:
    payload = _load(
        tools.infl_snapshot_synthetic.invoke(
            {"name": name, "count": count, "seed": seed}
        )
    )
    assert payload["status"] == "ok", payload
    return Path(payload["path"])


def test_capabilities_state_what_is_refused(workspace):
    payload = _load(tools.infl_capabilities.invoke({}))
    assert payload["status"] == "ok"
    assert len(payload["ladder"]) == 10
    refusals = " ".join(payload["refusals"])
    assert "Sci-Hub" in refusals
    assert "names" in refusals
    assert payload["not_attempted"]["author_demographics"]
    assert len(payload["audits"]) == 10
    sources = {item["provider"]: item for item in payload["sources"]}
    assert sources["openalex"]["wired"] is True
    assert sources["crossref"]["wired"] is True
    assert sources["ads"]["credential_env"] == "ADS_API_TOKEN"
    assert "credential_value" not in sources["ads"]


def test_snapshot_synthetic_is_reproducible(workspace):
    first = _load(
        tools.infl_snapshot_synthetic.invoke({"name": "a", "count": 40, "seed": 9})
    )
    second = _load(
        tools.infl_snapshot_synthetic.invoke({"name": "b", "count": 40, "seed": 9})
    )
    assert first["fingerprint"] == second["fingerprint"]
    assert first["papers"] == 40
    assert "pipeline validation only" in first["honesty_note"]


def test_cohorts_lists_what_was_frozen(workspace):
    _demo_cohort(workspace, name="demo", count=40)
    payload = _load(tools.infl_cohorts.invoke({}))
    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert payload["cohorts"][0]["papers"] == 40
    assert payload["cohorts"][0]["synthetic"] is True


def test_cohort_diff_measures_churn_between_two_builds(workspace):
    """A shorter rebuild loses papers: churn becomes a number, not a note."""
    _demo_cohort(workspace, name="older", count=40, seed=3)
    _demo_cohort(workspace, name="newer", count=25, seed=3)

    payload = _load(tools.infl_cohort_diff.invoke({"older": "older", "newer": "newer"}))

    assert payload["status"] == "ok"
    membership = payload["diff"]["membership"]
    assert membership["papers_left"] == 40
    assert membership["papers_right"] == 25
    assert membership["kept"] == 25
    assert membership["removed"] == 15
    assert membership["added"] == 0
    assert membership["jaccard"] == 0.625
    assert payload["diff"]["same_question"] is True
    assert "部分稳定" in payload["diff"]["verdict"]
    assert Path(payload["artifacts"]["json"]).is_file()
    assert "Cohort drift" in Path(payload["artifacts"]["markdown"]).read_text(
        encoding="utf-8"
    )


def test_cohort_diff_separates_value_drift_from_membership_churn(workspace):
    """Same papers, different numbers: the survivors' values moved."""
    _demo_cohort(workspace, name="older", count=30, seed=3)
    _demo_cohort(workspace, name="newer", count=30, seed=11)

    payload = _load(tools.infl_cohort_diff.invoke({"older": "older", "newer": "newer"}))

    diff = payload["diff"]
    assert diff["membership"]["kept"] == 30
    assert diff["membership"]["removed"] == 0
    assert diff["values"]["citations_changed"] > 0


def test_cohort_diff_sees_the_same_cohort_as_unchanged(workspace):
    """The same seed twice is the control case: nothing was lost."""
    _demo_cohort(workspace, name="one", count=40, seed=5)
    _demo_cohort(workspace, name="two", count=40, seed=5)

    payload = _load(tools.infl_cohort_diff.invoke({"older": "one", "newer": "two"}))

    assert payload["diff"]["membership"]["kept"] == 40
    assert payload["diff"]["membership"]["removed"] == 0
    assert "没有换掉任何论文" in payload["diff"]["verdict"]


def test_cohort_diff_refuses_an_unknown_cohort(workspace):
    """A missing snapshot is an error, never an empty diff."""
    _demo_cohort(workspace, name="one", count=10, seed=5)
    payload = _load(tools.infl_cohort_diff.invoke({"older": "one", "newer": "nowhere"}))
    assert payload["status"] == "error"


def test_network_is_refused_by_default(workspace):
    search = _load(tools.infl_snapshot_search.invoke({"query": "supernova cosmology"}))
    assert search["status"] == "error"
    assert "network_disabled" in search["error"]
    fetch = _load(tools.infl_fetch_paper.invoke({"doi": "10.1093/mnras/stv1947"}))
    assert fetch["status"] == "error"
    assert "network_disabled" in fetch["error"]


def test_run_requires_a_reference_population(workspace):
    payload = _load(tools.infl_run.invoke({"cohort": ""}))
    assert payload["status"] == "error"
    assert "reference population" in payload["error"]


def test_run_produces_a_self_describing_bundle(workspace):
    cohort = _demo_cohort(workspace, count=80)
    payload = _load(
        tools.infl_run.invoke(
            {
                "cohort": str(cohort),
                "seed": 1,
                "max_subjects": 3,
            }
        )
    )
    assert payload["status"] == "ok", payload
    directory = Path(payload["package_dir"])
    assert (directory / "report.md").is_file()
    assert payload["papers"] == 80
    assert payload["scored_uploads"] == 0
    # Right-censored papers are excluded from training, never silently labelled.
    assert 0 < payload["metrics_summary"]["labelled"] <= 80
    assert payload["metrics_summary"]["interval_coverage"] is not None
    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "80% 区间" in report
    assert "偏差" in report


def test_status_report_bias_explain_and_package(workspace):
    cohort = _demo_cohort(workspace, count=80)
    run = _load(
        tools.infl_run.invoke({"cohort": str(cohort), "max_subjects": 2, "seed": 2})
    )
    run_id = run["run_id"]

    status = _load(tools.infl_status.invoke({"run_id": run_id}))
    assert status["status"] == "ok"
    assert "model" in status["run"]["stages"]

    report = _load(tools.infl_report.invoke({"run_id": run_id}))
    assert "6. 可复现性" in report["report"]

    bias = _load(tools.infl_bias.invoke({"run_id": run_id}))
    assert bias["status"] == "ok"
    assert bias["count"] == 10
    assert (
        "作者人口学（不做）" in "".join(bias["cannot_assess"]) or bias["cannot_assess"]
    )
    unassessable = _load(
        tools.infl_bias.invoke({"run_id": run_id, "status": "cannot_assess"})
    )
    assert unassessable["count"] >= 1

    explain = _load(tools.infl_explain.invoke({"run_id": run_id}))
    assert explain["status"] == "ok"
    assert explain["factors"]
    assert explain["prediction"]["paper_id"] == explain["paper_id"]
    assert explain["comparators"]

    package = _load(tools.infl_package.invoke({"run_id": run_id}))
    assert package["status"] == "ok"
    roles = {item["role"] for item in package["manifest"]["artifacts"]}
    assert {"papers", "predictions", "factors", "bias_audit", "report"} <= roles


def test_unknown_run_is_an_error(workspace):
    payload = _load(tools.infl_status.invoke({"run_id": "nope"}))
    assert payload["status"] == "error"
    assert "unknown run" in payload["error"]


def test_parse_pdf_refuses_an_empty_list(workspace):
    payload = _load(tools.infl_parse_pdf.invoke({"paths": []}))
    assert payload["status"] == "error"
    assert "at least one PDF" in payload["error"]


def test_acquisition_request_lists_legitimate_routes(workspace):
    payload = _load(
        tools.infl_acquisition_request.invoke({"doi": "10.1093/mnras/stv1947"})
    )
    assert payload["status"] == "ok"
    routes = [step["route"] for step in payload["steps"]["steps"]]
    assert routes[0] == "interlibrary_loan"
    assert "shadow-library mirrors" in payload["refused"]


def test_paths_outside_the_workspace_are_refused(workspace):
    payload = _load(tools.infl_parse_pdf.invoke({"paths": ["/etc/hosts"]}))
    assert payload["status"] == "error"
    assert "path escaped the task workspace" in payload["error"]


OPENALEX_RECORD = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.1093/mnras/stv1947",
    "title": "Photometric calibration of nearby supernovae light curves",
    "publication_year": 2015,
    "cited_by_count": 42,
    "counts_by_year": [{"year": 2016, "cited_by_count": 5}],
    "authorships": [
        {
            "author": {"display_name": "A. Author", "orcid": "0000-0002-1825-0097"},
            "institutions": [],
            "author_position": "first",
        }
    ],
    "primary_location": {
        "source": {
            "display_name": "Monthly Notices of the Royal Astronomical Society",
            "publisher": "Oxford University Press",
            "type": "journal",
            "issn_l": "1365-2966",
        },
        "is_oa": False,
    },
    "abstract_inverted_index": {"We": [0], "measure": [1], "decline": [2]},
}


def _write_records(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_recorded_payload_becomes_a_cohort_without_any_network(workspace):
    """A real snapshot is fetched where there is egress and imported here."""
    path = _write_records(workspace, "openalex.json", {"results": [OPENALEX_RECORD]})
    payload = _load(
        tools.infl_snapshot_import.invoke(
            {"paths": [str(path)], "name": "real", "query": "supernova Ia"}
        )
    )
    assert payload["status"] == "ok", payload
    assert payload["network_used"] is False
    assert payload["papers"] == 1

    from jw.research_influence.cohort import CohortSnapshot

    snapshot = CohortSnapshot.load(Path(payload["path"]))
    paper = snapshot.papers[0]
    # Bibliometrics survive the handover: that is the point of importing.
    assert paper.cited_by_count == 42
    assert paper.venue is not None and paper.venue.name.startswith("Monthly Notices")
    assert paper.authors
    assert paper.authors[0].name or paper.authors[0].family
    assert paper.year == 2015
    assert snapshot.fingerprint() == payload["fingerprint"]


def test_jsonl_payloads_are_accepted_too(workspace):
    """Operators export JSONL as often as JSON; both must work."""
    path = workspace / "works.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(
                {
                    **OPENALEX_RECORD,
                    "id": f"https://openalex.org/W{i}",
                    "doi": f"https://doi.org/10.1000/jsonl{i}",
                }
            )
            for i in (1, 2)
        ),
        encoding="utf-8",
    )
    payload = _load(tools.infl_snapshot_import.invoke({"paths": [str(path)]}))
    assert payload["status"] == "ok", payload
    assert payload["papers"] == 2


def test_importing_nothing_is_an_error(workspace):
    """An empty handover must not look like an empty cohort."""
    assert _load(tools.infl_snapshot_import.invoke({"paths": []}))["status"] == "error"
    empty = _write_records(workspace, "empty.json", [])
    payload = _load(tools.infl_snapshot_import.invoke({"paths": [str(empty)]}))
    assert payload["status"] == "error"
    assert "no works" in payload["error"]


def test_fetch_writes_an_export_and_reports_network_failure_honestly(
    workspace, monkeypatch, capsys
):
    """The export can be produced wherever egress exists, then imported offline."""
    from jw.research_influence import fetch
    from jw.research_influence.providers.base import ProviderPolicy
    from tests.research_influence.conftest import FakeTransport

    out = workspace / "exports" / "openalex.jsonl"
    transport = FakeTransport().add(
        "api.openalex.org/works", {"results": [OPENALEX_RECORD]}
    )
    payload = fetch.fetch_export(
        "supernova Ia",
        out=out,
        limit=10,
        transport=transport,
        policy=ProviderPolicy(allow_network=True),
    )
    assert payload["status"] == "ok", payload
    assert payload["works"] == 1 and payload["has_citations"] == 1
    assert out.exists()

    # The export is what the offline half consumes.
    imported = _load(tools.infl_snapshot_import.invoke({"paths": [str(out)]}))
    assert imported["status"] == "ok", imported
    from jw.research_influence.cohort import CohortSnapshot

    snapshot = CohortSnapshot.load(Path(imported["path"]))
    assert snapshot.papers[0].cited_by_count == 42

    # A refused request must not look like an empty result set.
    refused = fetch.fetch_export(
        "supernova Ia",
        out=workspace / "never.jsonl",
        transport=FakeTransport({}),
        policy=ProviderPolicy(allow_network=True),
    )
    assert refused["status"] == "error"
    assert not (workspace / "never.jsonl").exists()

    class DeadTransport:
        def get(self, url, *, headers=None, timeout=20.0, max_bytes=0):
            from jw.research_influence.transport import HttpResponse

            return HttpResponse(status=0, url=url, error="network_disabled")

        def post_json(self, url, payload, *, headers=None, timeout=20.0):
            return self.get(url)

    monkeypatch.setattr(fetch, "UrllibTransport", lambda *a, **k: DeadTransport())
    code = fetch.main(["--query", "x", "--out", str(workspace / "cli.jsonl")])
    assert code == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "error"


def test_fetch_paper_builds_an_access_decision_and_acquisition_line(
    workspace, monkeypatch
):
    """One DOI must survive the whole tool: no crash and a usable route.

    The three calls this tool makes into the access module used to disagree
    about their argument shapes, so the tool raised ``AttributeError`` before
    returning anything.  A fake transport keeps the test offline.
    """
    from tests.research_influence.conftest import FakeTransport

    record = dict(OPENALEX_RECORD)
    record["best_oa_location"] = {
        "pdf_url": "https://example.org/paper.pdf",
        "landing_page_url": "https://doi.org/10.1093/mnras/stv1947",
        "is_oa": True,
        "license": "cc-by",
        "version": "publishedVersion",
        "source": {
            "display_name": "Monthly Notices of the Royal Astronomical Society",
            "type": "journal",
            "host_organization_name": "Oxford University Press",
        },
    }
    transport = FakeTransport().add("api.openalex.org/works", record)
    monkeypatch.setattr(tools, "_transport", lambda *a, **k: transport)

    payload = _load(
        tools.infl_fetch_paper.invoke(
            {
                "doi": "10.1093/mnras/stv1947",
                "name": "one",
                "allow_network": True,
            }
        )
    )
    assert payload["status"] == "ok", payload
    assert payload["access"]["route"] == "open_access_gold", payload["access"]
    assert payload["coverage"] >= 0.8
    assert "sections" in payload["missing"]
    assert "Photometric calibration" in payload["acquisition_request"]["citation"]


def test_snapshot_search_passes_a_historical_filter(workspace, monkeypatch):
    """A prediction cohort needs a closed citation window, not the newest page."""
    from tests.research_influence.conftest import FakeTransport

    transport = FakeTransport().add(
        "api.openalex.org/works", {"results": [OPENALEX_RECORD]}
    )
    monkeypatch.setattr(tools, "_transport", lambda *a, **k: transport)

    payload = _load(
        tools.infl_snapshot_search.invoke(
            {
                "query": "supernova",
                "name": "historical",
                "limit": 1,
                "from_year": 2015,
                "to_year": 2015,
                "sort": "cited",
                "allow_network": True,
            }
        )
    )
    assert payload["status"] == "ok", payload
    assert payload["filters"]["from_year"] == 2015
    assert payload["filters"]["to_year"] == 2015
    url = transport.calls[0][1]
    assert "from_publication_date%3A2015-01-01" in url
    assert "to_publication_date%3A2015-12-31" in url
    assert "sort=cited_by_count:desc" in url


def test_snapshot_search_rejects_an_inverted_year_window(workspace):
    payload = _load(
        tools.infl_snapshot_search.invoke(
            {"query": "x", "from_year": 2020, "to_year": 2010}
        )
    )
    assert payload["status"] == "error"
    assert "from_year" in payload["error"]


def test_snapshot_search_pages_instead_of_keeping_the_first_page(
    workspace, monkeypatch
):
    """A 400-paper cohort cannot be the top 200 results and still be a sample.

    The fake transport answers two pages with distinct work ids; the tool must
    ask for the second page rather than silently returning a truncated cohort.
    """
    from tests.research_influence.conftest import FakeTransport

    def work(number: int) -> dict:
        record = dict(OPENALEX_RECORD)
        record["id"] = f"https://openalex.org/W{number}"
        record["doi"] = f"https://doi.org/10.1000/page{number}"
        record["title"] = f"Page work {number}"
        return record

    transport = FakeTransport()
    transport.add("page=2", {"results": [work(201), work(202)]})
    transport.add("api.openalex.org/works", {"results": [work(101), work(102)]})
    monkeypatch.setattr(tools, "_transport", lambda *a, **k: transport)

    payload = _load(
        tools.infl_snapshot_search.invoke(
            {"query": "supernova", "name": "paged", "limit": 4, "allow_network": True}
        )
    )
    assert payload["status"] == "ok", payload
    assert payload["pages_fetched"] == 2
    assert payload["papers"] == 4
    urls = [url for _, url in transport.calls]
    # "&page=" not "page=": per-page would match the latter.
    assert any("&page=2" in url for url in urls), urls
    # Without a key the tool cannot use cursors, and the snapshot must record
    # that instead of leaving the reader to guess how the sample was walked.
    assert payload["paging"] == "page"


def test_snapshot_search_prefers_cursors_when_a_key_is_configured(
    workspace, monkeypatch
):
    """A key unlocks cursor paging, which is the form OpenAlex recommends."""
    from tests.research_influence.conftest import FakeTransport

    def work(number: int) -> dict:
        record = dict(OPENALEX_RECORD)
        record["id"] = f"https://openalex.org/W{number}"
        record["doi"] = f"https://doi.org/10.1000/cur{number}"
        record["title"] = f"Cursor work {number}"
        return record

    transport = FakeTransport()
    transport.add(
        "cursor=IlszNC",
        {"results": [work(201)], "meta": {"next_cursor": None}},
    )
    transport.add(
        "cursor=%2A",
        {"results": [work(101)], "meta": {"next_cursor": "IlszNC4yNDUxMSw"}},
    )
    monkeypatch.setattr(tools, "_transport", lambda *a, **k: transport)

    from jw.research_influence.providers.base import ProviderPolicy

    monkeypatch.setattr(
        tools,
        "ProviderPolicy",
        type(
            "PatchedPolicy",
            (ProviderPolicy,),
            {
                "from_environment": classmethod(
                    lambda cls, **kw: cls(overrides={"openalex": "KEY"})
                )
            },
        ),
    )

    payload = _load(
        tools.infl_snapshot_search.invoke(
            {
                "query": "supernova",
                "name": "cursored",
                "limit": 2,
                "allow_network": True,
            }
        )
    )
    assert payload["status"] == "ok", payload
    urls = [url for _, url in transport.calls]
    assert any("cursor=" in url for url in urls), urls
    assert not any("&page=" in url for url in urls), urls
    assert payload["paging"] == "cursor"


def test_verify_reports_drift_instead_of_trusting_the_manifest(workspace):
    """`infl_verify` re-reads the files; the manifest alone proves nothing."""
    _demo = _load(
        tools.infl_snapshot_synthetic.invoke({"name": "demo", "count": 60, "seed": 3})
    )
    run = _load(
        tools.infl_run.invoke(
            {"cohort": "demo", "importance_repeats": 1, "importance_seeds": [0]}
        )
    )
    assert run["status"] == "ok", run
    run_id = run["run_id"]

    clean = _load(tools.infl_verify.invoke({"run_id": run_id}))
    assert clean["status"] == "ok", clean
    assert clean["verification"]["ok"] is True
    assert clean["verification"]["failed"] == []

    package = Path(clean["verification"]["directory"])
    target = package / "papers.csv"
    target.write_text(
        target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8"
    )
    drifted = _load(tools.infl_verify.invoke({"run_id": run_id}))
    assert drifted["status"] == "drift"
    assert "papers" in drifted["verification"]["failed"]


def test_enrich_references_writes_a_new_snapshot_and_reuses_the_cache(
    workspace, monkeypatch
):
    """Crossref deposits replace id-only refs; the input snapshot stays frozen."""
    from tests.research_influence.conftest import FakeTransport

    source = _write_records(workspace, "openalex.json", {"results": [OPENALEX_RECORD]})
    imported = _load(
        tools.infl_snapshot_import.invoke(
            {"paths": [str(source)], "name": "before", "query": "supernova"}
        )
    )
    assert imported["status"] == "ok", imported
    transport = FakeTransport().add(
        "api.crossref.org/works",
        {
            "message": {
                "DOI": "10.1093/mnras/stv1947",
                "title": ["Photometric calibration of nearby supernovae light curves"],
                "reference": [
                    {
                        "unstructured": "A. Author 2010, ApJ, 1, 1",
                        "DOI": "10.1234/reference",
                        "article-title": "A cited paper",
                        "year": 2010,
                        "journal-title": "ApJ",
                    }
                ],
            }
        },
    )
    monkeypatch.setattr(tools, "_transport", lambda *a, **k: transport)

    disabled = _load(
        tools.infl_enrich_references.invoke({"cohort": "before"})
    )
    assert disabled["status"] == "error"
    assert "network_disabled" in disabled["error"]

    payload = _load(
        tools.infl_enrich_references.invoke(
            {"cohort": "before", "name": "after", "workers": 1, "allow_network": True}
        )
    )
    assert payload["status"] == "ok", payload
    assert payload["enrichment"]["enriched"] == 1
    assert payload["enrichment"]["readable_after"] >= 1
    assert Path(payload["path"]).is_file()
    assert payload["cache"]

    from jw.research_influence.cohort import CohortSnapshot

    before = CohortSnapshot.load(Path(imported["path"]))
    after = CohortSnapshot.load(Path(payload["path"]))
    assert before.papers[0].references == ()
    assert after.papers[0].references[0].doi == "10.1234/reference"
    assert after.papers[0].references[0].title == "A cited paper"
    assert after.fingerprint() != before.fingerprint()

    calls_before = len(transport.calls)
    again = _load(
        tools.infl_enrich_references.invoke(
            {"cohort": "before", "name": "after2", "workers": 1, "allow_network": True}
        )
    )
    assert again["status"] == "ok", again
    assert len(transport.calls) == calls_before


def test_enrich_references_replaces_ids_with_crossref_deposits(workspace, monkeypatch):
    """An id-only reference list becomes readable, and the cache avoids a refetch."""
    from tests.research_influence.conftest import FakeTransport

    record = dict(OPENALEX_RECORD)
    record["referenced_works"] = [
        "https://openalex.org/W111",
        "https://openalex.org/W222",
    ]
    source = _write_records(workspace, "openalex_ids.json", {"results": [record]})
    imported = _load(
        tools.infl_snapshot_import.invoke(
            {"paths": [str(source)], "name": "ids", "query": "supernova"}
        )
    )
    assert imported["status"] == "ok", imported

    deposit = {
        "message": {
            "DOI": "10.1093/mnras/stv1947",
            "title": ["A title"],
            "reference": [
                {"index": 1, "DOI": "10.1/a", "article-title": "Ref one"},
                {"index": 2, "article-title": "Ref two", "journal-title": "ApJ"},
            ],
        }
    }
    transport = FakeTransport().add("api.crossref.org/works", deposit)
    monkeypatch.setattr(tools, "_transport", lambda *a, **k: transport)

    first = _load(
        tools.infl_enrich_references.invoke(
            {"cohort": "ids", "allow_network": True, "workers": 1}
        )
    )
    assert first["status"] == "ok", first
    enrichment = first["enrichment"]
    assert enrichment["enriched"] == 1
    assert enrichment["references_before"] == 2
    assert enrichment["readable_before"] == 0
    assert enrichment["readable_after"] == 2

    from jw.research_influence.cohort import CohortSnapshot

    enriched = CohortSnapshot.load(Path(first["path"]))
    assert [ref.title for ref in enriched.papers[0].references] == ["Ref one", "Ref two"]
    assert enriched.papers[0].references[0].doi == "10.1/a"

    second = _load(
        tools.infl_enrich_references.invoke(
            {"cohort": "ids", "name": "ids_again", "allow_network": True}
        )
    )
    assert second["status"] == "ok", second
    assert second["enrichment"]["cached"] == 1
    assert second["enrichment"]["fetched"] == 0


def test_enrich_references_refuses_network_without_consent(workspace):
    payload = _load(
        tools.infl_enrich_references.invoke({"cohort": "missing", "allow_network": False})
    )
    assert payload["status"] == "error"
    assert "network_disabled" in payload["error"]


def _pdf_with_text(path: Path) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(8.5, 11))
    figure.text(0.1, 0.95, "Abstract", fontsize=12)
    figure.text(0.1, 0.90, "We present photometry of a nearby supernova.", fontsize=10)
    figure.text(0.1, 0.70, "1. Introduction", fontsize=12)
    figure.text(0.1, 0.65, "The light curve spans sixty nights.", fontsize=10)
    figure.text(0.1, 0.40, "Data availability", fontsize=12)
    figure.text(0.1, 0.35, "Data at https://zenodo.org/record/1234567", fontsize=10)
    figure.savefig(path)
    plt.close(figure)
    return path.read_bytes()


def test_fetch_fulltext_attaches_sections_to_an_indexed_paper(
    workspace, monkeypatch, tmp_path
):
    """An index record plus a legal open PDF becomes a readable paper."""
    from tests.research_influence.conftest import FakeTransport
    from jw.research_influence.cohort import CohortSnapshot

    record = dict(OPENALEX_RECORD)
    record["best_oa_location"] = {
        "pdf_url": "https://example.org/paper.pdf",
        "landing_page_url": "https://doi.org/10.1093/mnras/stv1947",
        "is_oa": True,
        "license": "cc-by",
        "version": "publishedVersion",
        "source": {"display_name": "MNRAS", "type": "journal"},
    }
    records = workspace / "records.json"
    records.write_text(json.dumps([record]), encoding="utf-8")
    imported = _load(tools.infl_snapshot_import.invoke({"paths": [str(records)], "name": "ft"}))
    assert imported["status"] == "ok", imported
    paper_id = CohortSnapshot.load(Path(imported["path"])).papers[0].paper_id

    transport = FakeTransport().add(
        "example.org/paper.pdf",
        _pdf_with_text(tmp_path / "paper.pdf"),
        content_type="application/pdf",
    ).add("api.openalex.org/works", record)
    monkeypatch.setattr(tools, "_transport", lambda *a, **k: transport)

    payload = _load(
        tools.infl_fetch_fulltext.invoke(
            {
                "cohort": "ft",
                "paper_id": paper_id,
                "name": "ft_read",
                "allow_network": True,
            }
        )
    )
    assert payload["status"] == "ok", payload
    assert payload["pages"] == 1
    assert "abstract" in payload["sections"]
    assert payload["access"]["route"] == "open_access_gold"
    assert Path(payload["file"]).is_file()
    assert payload["open_data"] == ["https://zenodo.org/record/1234567"]

    original = CohortSnapshot.load(Path(imported["path"]))
    assert original.papers[0].fulltext_path == ""
    assert original.papers[0].sections == {}


def test_fetch_fulltext_refuses_without_network(workspace):
    payload = _load(
        tools.infl_fetch_fulltext.invoke(
            {"cohort": "whatever", "paper_id": "paper:x", "allow_network": False}
        )
    )
    assert payload["status"] == "error"
    assert "network_disabled" in payload["error"]
