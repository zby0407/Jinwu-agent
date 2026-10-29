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
            json.dumps(dict(OPENALEX_RECORD, id=f"https://openalex.org/W{i}"))
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
    assert any("page=2" in url for url in urls), urls


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
