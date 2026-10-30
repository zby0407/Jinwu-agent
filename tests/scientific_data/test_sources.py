"""Discovery and acquisition tests: no network, explicit budgets, real ledger."""

from __future__ import annotations

from pathlib import Path

from jw.scientific_data.acquisition import (
    FetchResult,
    acquire,
    describe_acquisition,
    source_texts,
    verify_sources,
)
from jw.scientific_data.contracts import (
    DataRequirementContract,
    IssueCode,
    ObservationField,
    RetrievalState,
    SourceRole,
)
from jw.scientific_data.domain import get_plugin
from jw.scientific_data.policy import AcquisitionPolicy
from jw.scientific_data.sources import (
    SourceCatalog,
    canonical_url,
    catalog_csv,
    plan_retrieval,
    register_local_sources,
    source_role_for_format,
)

PLUGIN = get_plugin("snia_light_curve")


def contract(**overrides) -> DataRequirementContract:
    fields: dict = {
        "contract_id": "c-sources",
        "research_goal": "Collect light curves of nearby type Ia supernovae",
        "entity_class": "type Ia supernova",
        "observables": (
            ObservationField(name="object", kind="identifier"),
            ObservationField(name="time", kind="time"),
            ObservationField(name="apparent_magnitude", kind="quantity", unit="mag"),
        ),
        "identifier_fields": ("object",),
        "time_field": "time",
        "domain_plugin": "snia_light_curve",
        "budget": {"max_sources": 4, "max_fetches": 2, "max_tables": 10},
    }
    fields.update(overrides)
    return DataRequirementContract(**fields)


class FakeFetcher:
    """Serves canned payloads so the tests never touch the network."""

    def __init__(self, payloads: dict[str, bytes], *, status: int = 200) -> None:
        self.payloads = payloads
        self.status = status
        self.calls: list[str] = []

    def fetch(self, url: str, *, policy: AcquisitionPolicy) -> tuple[int, bytes, str]:
        self.calls.append(url)
        if url not in self.payloads:
            return 404, b"", "text/plain"
        return self.status, self.payloads[url], "text/csv"


def test_canonical_url_removes_tracking_and_fragment_noise():
    first = canonical_url("https://Example.org/a/b/?utm_source=x&table=2#top")
    assert canonical_url("http://example.org/a/b?table=2") == (
        "http://example.org/a/b?table=2"
    )
    assert first == "https://example.org/a/b?table=2"
    assert canonical_url("https://example.org/") == "https://example.org/"
    assert canonical_url("") == ""


def test_canonical_url_keeps_a_windows_file_path_intact():
    path = r"C:\Users\runner\data\table.csv"

    assert canonical_url(f"file://{path}") == f"file://{path}"


def test_acquired_local_filenames_are_safe_on_windows(tmp_path):
    data = tmp_path / "lc.csv"
    data.write_text("object,mag\nSN2005A,16.5\n", encoding="utf-8")
    catalog = SourceCatalog()
    register_local_sources(catalog, [str(data)])

    report = acquire(
        catalog,
        policy=AcquisitionPolicy(),
        workdir=tmp_path / "run",
        contract=contract(),
    )

    stored = Path(report.catalog.to_records()[0].local_path)
    assert ":" not in stored.name
    assert stored.suffix == ".csv"
    assert stored.exists()


def test_retrieval_plan_names_the_source_roles_the_goal_needs():
    steps = plan_retrieval(contract(), plugin=PLUGIN)

    roles = [step.role for step in steps]
    assert roles == [
        SourceRole.AUTHORED_TABLE,
        SourceRole.PRIMARY_PUBLISHED,
        SourceRole.SUPPLEMENT,
        SourceRole.CATALOG,
        SourceRole.FIGURE_DIGITIZED,
    ]
    literature = next(
        step for step in steps if step.role is SourceRole.PRIMARY_PUBLISHED
    )
    assert literature.query.startswith("type Ia supernova")
    assert all(step.rationale for step in steps)
    assert "apparent_magnitude" in " ".join(step.query for step in steps)


def test_plan_can_skip_figures_when_the_user_does_not_want_them():
    steps = plan_retrieval(contract(), plugin=PLUGIN, include_figures=False)

    assert SourceRole.FIGURE_DIGITIZED not in {step.role for step in steps}


def test_catalog_deduplicates_by_canonical_uri():
    catalog = SourceCatalog()

    first = catalog.add(url="https://example.org/a?utm_medium=x", title="A")
    second = catalog.add(url="https://example.org/a", title="A again")

    assert first.source_id == second.source_id
    assert len(catalog) == 1


def test_ranking_prefers_primary_sources_with_high_trust():
    catalog = SourceCatalog()
    figure = catalog.add(
        url="https://example.org/f.png",
        role=SourceRole.FIGURE_DIGITIZED,
        trust=0.3,
        relevance=0.4,
    )
    primary = catalog.add(
        url="https://arxiv.org/abs/1",
        role=SourceRole.PRIMARY_PUBLISHED,
        trust=0.9,
        relevance=0.9,
    )
    catalog_rec = catalog.add(
        url="https://vizier.example/table",
        role=SourceRole.CATALOG,
        trust=0.7,
        relevance=0.6,
    )

    assert [record.source_id for record in catalog.ranked()] == [
        primary.source_id,
        catalog_rec.source_id,
        figure.source_id,
    ]
    assert catalog.coverage_summary()["pending"] == 3


def test_catalog_csv_has_stable_header_and_one_row_per_source():
    catalog = SourceCatalog()
    catalog.add(url="https://example.org/a.csv", role=SourceRole.SUPPLEMENT)
    catalog.add(url="https://example.org/b.csv", role=SourceRole.CATALOG)

    text = catalog_csv(catalog.to_records())
    lines = text.strip().splitlines()

    assert lines[0].startswith("source_id,role,title")
    assert len(lines) == 3
    assert "supplement" in lines[1] or "supplement" in lines[2]


def test_local_sources_are_registered_with_a_trusted_role(tmp_path):
    path = tmp_path / "table.csv"
    path.write_text("object,mag\nSN2005A,16.5\n", encoding="utf-8")
    catalog = SourceCatalog()

    records = register_local_sources(catalog, [str(path)])

    assert records[0].role is SourceRole.AUTHORED_TABLE
    assert records[0].trust > 0.5
    assert records[0].retrieval is RetrievalState.CANDIDATE
    assert str(path) in records[0].canonical_uri


def test_source_role_for_format_keeps_a_known_role():
    assert (
        source_role_for_format("png", fallback=SourceRole.UNKNOWN)
        is SourceRole.FIGURE_DIGITIZED
    )
    assert (
        source_role_for_format("csv", fallback=SourceRole.UNKNOWN)
        is SourceRole.AUTHORED_TABLE
    )
    assert (
        source_role_for_format("csv", fallback=SourceRole.SUPPLEMENT)
        is SourceRole.SUPPLEMENT
    )


def test_acquire_reads_local_files_and_marks_them_acquired(tmp_path):
    data = tmp_path / "lc.csv"
    data.write_text("object,mag\nSN2005A,16.5\n", encoding="utf-8")
    catalog = SourceCatalog()
    register_local_sources(catalog, [str(data)])

    report = acquire(
        catalog,
        policy=AcquisitionPolicy(),
        workdir=tmp_path / "run",
        contract=contract(),
    )

    assert report.stats["acquired"] == 1
    record = report.catalog.to_records()[0]
    assert record.retrieval is RetrievalState.ACQUIRED
    assert record.content_sha256.startswith("sha256:")
    assert Path(record.local_path).read_text(encoding="utf-8").startswith("object,mag")
    assert source_texts(report.catalog) == {record.source_id: record.local_path}
    assert verify_sources(report.catalog) == ()


def test_network_is_refused_by_default_and_leaves_a_blocking_issue(tmp_path):
    catalog = SourceCatalog()
    catalog.add(
        url="https://arxiv.org/abs/1234.5678", role=SourceRole.PRIMARY_PUBLISHED
    )

    report = acquire(
        catalog,
        policy=AcquisitionPolicy(max_fetches=3),
        workdir=tmp_path,
        fetcher=FakeFetcher({}),
        contract=contract(),
    )

    assert report.fetched == ()
    record = report.catalog.to_records()[0]
    assert record.retrieval is RetrievalState.SKIPPED
    assert record.failure_reason == "network_disabled"
    issue = report.issues[0]
    assert issue.code is IssueCode.SOURCE_HOST_DENIED
    assert issue.blocking is True
    assert "network disabled" in issue.detail


def test_allowlisted_hosts_are_fetched_into_a_content_addressed_file(tmp_path):
    catalog = SourceCatalog()
    url = "https://vizier.example/tables/lc.csv"
    catalog.add(url=url, role=SourceRole.CATALOG)
    fetcher = FakeFetcher({url: b"object,mag\nSN2005A,16.5\n"})
    policy = AcquisitionPolicy(allow_network=True, allowed_hosts=("vizier.example",))

    report = acquire(
        catalog,
        policy=policy,
        workdir=tmp_path,
        fetcher=fetcher,
        contract=contract(),
    )

    assert fetcher.calls == [url]
    stored = report.fetched[0]
    assert stored.sha256.startswith("sha256:")
    assert stored.path.endswith(".csv")
    assert Path(stored.path).read_bytes() == b"object,mag\nSN2005A,16.5\n"
    assert report.stats["acquired"] == 1


def test_hosts_outside_the_allowlist_are_denied(tmp_path):
    catalog = SourceCatalog()
    catalog.add(url="https://elsewhere.example/data.csv", role=SourceRole.CATALOG)
    policy = AcquisitionPolicy(allow_network=True, allowed_hosts=("vizier.example",))

    report = acquire(
        catalog,
        policy=policy,
        workdir=tmp_path,
        fetcher=FakeFetcher({}),
        contract=contract(),
    )

    assert report.stats["denied"] == 1
    assert report.issues[0].evidence["reason"] == "host_not_allowlisted"


def test_fetch_errors_become_unreadable_source_issues(tmp_path):
    catalog = SourceCatalog()
    catalog.add(url="https://vizier.example/missing.csv", role=SourceRole.CATALOG)
    policy = AcquisitionPolicy(allow_network=True)

    report = acquire(
        catalog,
        policy=policy,
        workdir=tmp_path,
        fetcher=FakeFetcher({}),
        contract=contract(),
    )

    assert report.stats["failed"] == 1
    assert report.issues[0].code is IssueCode.UNREADABLE_SOURCE
    assert report.issues[0].blocking is True


def test_contract_budget_caps_the_number_of_fetches(tmp_path):
    urls = [f"https://vizier.example/{index}.csv" for index in range(4)]
    catalog = SourceCatalog()
    for url in urls:
        catalog.add(url=url, role=SourceRole.CATALOG)
    fetcher = FakeFetcher(dict.fromkeys(urls, b"a\n1\n"))
    policy = AcquisitionPolicy(allow_network=True, max_fetches=10)

    report = acquire(
        catalog,
        policy=policy,
        workdir=tmp_path,
        fetcher=fetcher,
        contract=contract(),  # budget says 2
    )

    assert len(fetcher.calls) == 2
    assert report.stats["fetch_budget"] == 2
    assert report.stats["skipped_budget"] == 2


def test_identical_payloads_are_stored_once(tmp_path):
    catalog = SourceCatalog()
    first = "https://vizier.example/a.csv"
    second = "https://vizier.example/b.csv"
    catalog.add(url=first, role=SourceRole.CATALOG)
    catalog.add(url=second, role=SourceRole.CATALOG)
    payload = b"object,mag\nSN2005A,16.5\n"
    policy = AcquisitionPolicy(allow_network=True, max_fetches=5)

    report = acquire(
        catalog,
        policy=policy,
        workdir=tmp_path,
        fetcher=FakeFetcher({first: payload, second: payload}),
        contract=contract(budget={"max_sources": 4, "max_fetches": 5}),
    )

    assert report.stats["duplicate_payloads"] == 1
    assert len(list((tmp_path / "sources").iterdir())) == 1


def test_oversized_payloads_are_refused(tmp_path):
    catalog = SourceCatalog()
    url = "https://vizier.example/big.csv"
    catalog.add(url=url, role=SourceRole.CATALOG)
    policy = AcquisitionPolicy(allow_network=True, max_bytes=4)

    report = acquire(
        catalog,
        policy=policy,
        workdir=tmp_path,
        fetcher=FakeFetcher({url: b"0123456789"}),
        contract=contract(),
    )

    assert report.stats["failed"] == 1
    assert "too_large" in report.issues[0].detail


def test_verify_sources_detects_a_changed_file(tmp_path):
    data = tmp_path / "lc.csv"
    data.write_text("object,mag\nSN2005A,16.5\n", encoding="utf-8")
    catalog = SourceCatalog()
    register_local_sources(catalog, [str(data)])
    report = acquire(
        catalog,
        policy=AcquisitionPolicy(),
        workdir=tmp_path / "run",
        contract=contract(),
    )
    stored = Path(report.catalog.to_records()[0].local_path)
    assert verify_sources(report.catalog) == ()

    stored.write_text("object,mag\nSN2005A,99.9\n", encoding="utf-8")

    assert verify_sources(report.catalog) == (report.catalog.to_records()[0].source_id,)


def test_policy_describes_itself_for_the_report():
    offline = AcquisitionPolicy()
    online = AcquisitionPolicy(allow_network=True, allowed_hosts=("arxiv.org",))

    assert "Network access was disabled" in offline.describe()
    assert "arxiv.org" in online.describe()
    assert online.to_dict()["allow_network"] is True


def test_describe_acquisition_returns_one_line_per_attempt(tmp_path):
    catalog = SourceCatalog()
    data = tmp_path / "lc.csv"
    data.write_text("a\n1\n", encoding="utf-8")
    register_local_sources(catalog, [str(data)])
    report = acquire(
        catalog,
        policy=AcquisitionPolicy(),
        workdir=tmp_path / "run",
        contract=contract(),
    )

    lines = describe_acquisition(report)

    assert len(lines) == len(report.results)
    assert "ok" in lines[0]


def test_fetch_result_reports_its_own_state():
    result = FetchResult(source_id="src:1", url="u", status="ok", size=3)

    assert result.ok is True
    assert FetchResult(source_id="src:1", url="u", status="failed").ok is False


def test_local_source_title_is_the_basename_on_windows():
    catalog = SourceCatalog()

    records = register_local_sources(catalog, [r"C:\Users\runner\data\table.csv"])

    assert records[0].title == "table.csv"
