"""The ladder decides routes, refuses shadow libraries and records refusals."""

from __future__ import annotations

from jw.research_influence.access import (
    DENIED_HOSTS,
    AccessCandidate,
    AccessPolicy,
    acquisition_request,
    candidates_from_arxiv,
    candidates_from_openalex,
    candidates_from_unpaywall,
    classify_location,
    decide_access,
    rank_candidates,
    request_acquisition,
    with_attempt,
)
from jw.research_influence.contracts import (
    AccessAttempt,
    AccessRoute,
    ProviderStatus,
)


def test_gold_when_publisher_serves_a_licensed_version():
    """Publisher host + open licence is the top rung."""
    candidate = classify_location(
        {
            "pdf_url": "https://publisher.example/x.pdf",
            "is_oa": True,
            "license": "cc-by",
            "version": "publishedVersion",
            "host_type": "publisher",
        },
        provider="openalex",
    )
    assert candidate is not None
    assert candidate.route is AccessRoute.OPEN_ACCESS_GOLD


def test_bronze_free_to_read_when_publisher_has_no_licence():
    """Readable-but-unlicensed is its own rung, not 'open access'."""
    candidate = classify_location(
        {
            "landing_url": "https://publisher.example/x",
            "is_oa": True,
            "license": "",
            "version": "publishedVersion",
            "host_type": "publisher",
        },
        provider="openalex",
    )
    assert candidate is not None
    assert candidate.route is AccessRoute.FREE_TO_READ


def test_repository_accepted_version_is_green():
    """An accepted manuscript in a repository is green, not gold."""
    candidate = classify_location(
        {
            "pdf_url": "https://repo.example/x.pdf",
            "is_oa": True,
            "version": "acceptedVersion",
            "host_type": "repository",
        },
        provider="unpaywall",
    )
    assert candidate is not None
    assert candidate.route is AccessRoute.OPEN_ACCESS_GREEN


def test_submitted_version_is_labelled_preprint():
    """A submitted draft is a preprint, and says so."""
    candidate = classify_location(
        {
            "pdf_url": "https://repo.example/x.pdf",
            "is_oa": True,
            "version": "submittedVersion",
            "host_type": "repository",
        },
        provider="openalex",
    )
    assert candidate is not None
    assert candidate.route is AccessRoute.PREPRINT


def test_arxiv_candidates_require_a_title_match():
    """A mismatched preprint would silently substitute another paper."""
    entries = [
        {
            "pdf_url": "https://arxiv.org/pdf/1",
            "title": "A completely different study of galaxies",
            "arxiv_id": "1",
        },
        {
            "pdf_url": "https://arxiv.org/pdf/2",
            "title": "The light curve of SN 2005cf from Swift",
            "arxiv_id": "2",
        },
    ]
    candidates = candidates_from_arxiv(entries, title="The light curve of SN 2005cf")
    assert [item.url for item in candidates] == ["https://arxiv.org/pdf/2"]


def test_shadow_library_hosts_are_never_fetched():
    """A caller-supplied Sci-Hub URL is refused by policy, with a reason."""
    candidate = AccessCandidate(
        url="https://sci-hub.se/10.1093/mnras/stv1947",
        route=AccessRoute.OPEN_ACCESS_GREEN,
        provider="user",
    )
    ranked = rank_candidates([candidate])
    assert ranked == ()
    policy = AccessPolicy()
    assert "shadow_library_refused" in policy.refuse_reason(candidate.url)
    assert "sci-hub.se" in DENIED_HOSTS


def test_ranking_prefers_the_earlier_rung_and_dedupes_urls():
    """Gold beats green beats preprint, and duplicates collapse."""
    candidates = [
        AccessCandidate(
            url="https://repo.example/x.pdf",
            route=AccessRoute.OPEN_ACCESS_GREEN,
            provider="a",
        ),
        AccessCandidate(
            url="https://arxiv.org/pdf/9", route=AccessRoute.PREPRINT, provider="b"
        ),
        AccessCandidate(
            url="https://repo.example/x.pdf",
            route=AccessRoute.OPEN_ACCESS_GREEN,
            provider="c",
        ),
        AccessCandidate(
            url="https://publisher.example/x.pdf",
            route=AccessRoute.OPEN_ACCESS_GOLD,
            provider="d",
        ),
    ]
    ranked = rank_candidates(candidates)
    assert [item.route for item in ranked] == [
        AccessRoute.OPEN_ACCESS_GOLD,
        AccessRoute.OPEN_ACCESS_GREEN,
        AccessRoute.PREPRINT,
    ]


def test_decision_without_candidates_is_abstract_only_and_explains_why():
    """No open copy is a typed outcome, not an empty result."""
    decision = decide_access(
        doi="10.1093/mnras/stv1947", candidates=(), has_abstract=True
    )
    assert decision.route is AccessRoute.ABSTRACT_ONLY
    assert decision.has_full_text is False
    assert "abstract-level analysis only" in decision.note


def test_decision_marks_preprints_as_not_the_version_of_record():
    """Choosing a preprint must carry the warning on the decision."""
    candidate = AccessCandidate(
        url="https://arxiv.org/pdf/1509.00001",
        route=AccessRoute.PREPRINT,
        provider="arxiv",
    )
    decision = decide_access(doi="10.1/x", candidates=[candidate], has_abstract=True)
    assert decision.route is AccessRoute.PREPRINT
    assert "preprint_is_not_the_version_of_record" in decision.note


def test_refused_candidates_are_reported_in_the_note():
    """The note counts what policy blocked."""
    candidate = AccessCandidate(
        url="https://libgen.is/book/1",
        route=AccessRoute.OPEN_ACCESS_GREEN,
        provider="user",
    )
    decision = decide_access(doi="10.1/x", candidates=[candidate], has_abstract=True)
    assert "refused:" in decision.note
    assert "shadow_library_refused" in decision.note


def test_network_disabled_policy_refuses_http_but_allows_files():
    """Offline operation keeps working for local files only."""
    policy = AccessPolicy(allow_network=False)
    assert policy.refuse_reason("https://arxiv.org/pdf/1") == "network_disabled"
    assert policy.refuse_reason("file:///tmp/x.pdf") == ""


def test_allowlist_blocks_unlisted_hosts():
    """An allowlist turns the network into a closed set."""
    policy = AccessPolicy(allow_hosts=frozenset({"arxiv.org"}))
    assert policy.refuse_reason("https://arxiv.org/pdf/1") == ""
    assert policy.refuse_reason("https://other.example/x").startswith(
        "host_not_allowlisted"
    )


def test_attempt_recording_keeps_provenance_of_the_ladder():
    """A successful attempt carries the path, a failed one does not."""
    decision = decide_access(doi="10.1/x", candidates=(), has_abstract=True)
    failed = with_attempt(
        decision,
        AccessAttempt(
            route=AccessRoute.OPEN_ACCESS_GREEN,
            provider="unpaywall",
            status=ProviderStatus.FORBIDDEN,
            http_status=403,
        ),
    )
    assert failed.fulltext_path == ""
    assert len(failed.attempts) == 1
    ok = with_attempt(
        failed,
        AccessAttempt(
            route=AccessRoute.PREPRINT,
            provider="arxiv",
            status=ProviderStatus.OK,
            path="/tmp/x.pdf",
        ),
    )
    assert ok.fulltext_path == "/tmp/x.pdf"
    assert ok.route is AccessRoute.PREPRINT
    assert len(ok.attempted_routes) == 2


def test_acquisition_request_is_actionable():
    """The last rung produces something a human can act on."""
    decision = request_acquisition(
        decide_access(doi="10.1093/mnras/stv1947", candidates=(), has_abstract=True)
    )
    assert decision.route is AccessRoute.ACQUISITION_REQUESTED
    request = acquisition_request(
        {
            "title": "A light curve",
            "doi": "10.1093/mnras/stv1947",
            "year": 2015,
            "authors": [{"name": "Jane Astro"}],
            "venue": {"name": "MNRAS"},
        }
    )
    assert "Jane Astro, 2015" in request["citation"]
    assert "doi:10.1093/mnras/stv1947" in request["citation"]
    assert "interlibrary_loan" in request["steps"]


def test_provider_candidate_extractors_cover_every_source():
    """OpenAlex, Unpaywall and arXiv payloads all feed the same ladder."""
    openalex_parsed = {
        "locations": [
            {
                "pdf_url": "https://arxiv.org/pdf/1",
                "is_oa": True,
                "version": "acceptedVersion",
                "host_type": "repository",
            }
        ]
    }
    unpaywall_parsed = {
        "locations": [
            {
                "landing_url": "https://publisher.example/x",
                "is_oa": True,
                "version": "publishedVersion",
                "host_type": "publisher",
                "license": "",
            }
        ]
    }
    assert (
        candidates_from_openalex(openalex_parsed)[0].route
        is AccessRoute.OPEN_ACCESS_GREEN
    )
    assert (
        candidates_from_unpaywall(unpaywall_parsed)[0].route is AccessRoute.FREE_TO_READ
    )
