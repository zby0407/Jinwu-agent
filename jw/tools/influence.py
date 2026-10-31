"""Tool surface for research influence analysis and bias explanation.

The agent gets a research question and a set of papers; these tools build or load
a frozen cohort, parse the papers it can legally obtain, fit an interval model,
explain each factor with a delta and a confidence interval, audit ten bias routes,
and write a bundle in which every number can be traced back to a file.

Two things are refused on purpose: a prediction without a reference population,
and a demographic inference from author names.  Both refusals are part of the
result, not an error the caller should work around.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from jw.research_influence.access import (
    AccessPolicy,
    acquisition_request,
    candidates_from_openalex,
    classify_location,
    decide_access,
    rank_candidates,
    with_attempt,
)
from jw.research_influence.bias import AUDIT_LABELS, AUDITS
from jw.research_influence.cohort import (
    CohortSnapshot,
    merge_duplicate_papers,
    openalex_snapshot,
    synthetic_cohort,
)
from jw.research_influence.contracts import (
    AccessAttempt,
    ProviderStatus,
    normalise_doi,
    utc_now,
)
from jw.research_influence.drift import diff_snapshots, drift_markdown
from jw.research_influence.fulltext import fetch_fulltext
from jw.research_influence.package import verify_package
from jw.research_influence.paper import apply_fulltext, build_paper
from jw.research_influence.providers.base import ProviderPolicy, credential
from jw.research_influence.providers.openalex import fetch_work, search_works
from jw.research_influence.references import enrich_references
from jw.research_influence.transport import PoliteTransport, UrllibTransport
from jw.research_influence.workflow import (
    RunOptions,
    load_run,
    paper_from_upload,
    run_influence,
)
from jw.workspaces import workspace_root_from_config

from .registry import register_tool_bundle

COHORT_DIR = "cohorts"
_MAX_LISTED = 25


def _result(**payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _workspace(config: RunnableConfig | None) -> Path:
    return workspace_root_from_config(config)


def _root(workspace: Path) -> Path:
    path = workspace / "research_influence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cohort_dir(workspace: Path) -> Path:
    path = _root(workspace) / COHORT_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve(value: str, workspace: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    target = candidate.resolve()
    root = workspace.resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"path escaped the task workspace: {value}")
    return target


def _snapshot_path(workspace: Path, name: str) -> Path:
    cohort_root = _cohort_dir(workspace)
    candidate = Path(name)
    if candidate.suffix == ".json":
        if candidate.is_absolute():
            return _resolve(str(candidate), workspace)
        inside = cohort_root / candidate.name
        if inside.is_file():
            return inside
        return _resolve(str(candidate), workspace)
    target = cohort_root / f"{name}.json"
    if target.is_file():
        return target
    return _resolve(name, workspace)


def _apply_reference_year(
    snapshot: CohortSnapshot, reference_year: int
) -> CohortSnapshot:
    if not reference_year or reference_year == snapshot.reference_year:
        return snapshot
    return CohortSnapshot(
        cohort_id=snapshot.cohort_id,
        papers=snapshot.papers,
        query=snapshot.query,
        source=snapshot.source,
        created_at=snapshot.created_at,
        reference_year=reference_year,
        filters=dict(snapshot.filters),
        notes=snapshot.notes,
    )


def _safe_stem(value: str) -> str:
    """Turn a cohort id into a filename without losing which cohort it was."""
    cleaned = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in str(value)
    )
    return cleaned.strip("_") or "cohort"


def _run_dir(workspace: Path, run_id: str) -> Path:
    directory = _root(workspace) / run_id
    if not (directory / "run.json").is_file():
        raise ValueError(f"unknown run: {run_id}")
    return directory


def _transport(allow_network: bool, *, user_agent: str = "") -> UrllibTransport | None:
    if not allow_network:
        return None
    inner = UrllibTransport(**({"user_agent": user_agent} if user_agent else {}))
    return PoliteTransport(inner, min_interval=1.0)


def _transport_or_refuse(allow_network: bool) -> tuple[Any, str]:
    transport = _transport(allow_network)
    if transport is None:
        return None, (
            "network_disabled: no request was sent. Pass allow_network=true only "
            "when the operator has agreed to online lookups."
        )
    return transport, ""


def _source_status() -> list[dict[str, object]]:
    """Which literature sources are wired, and whether their credential is set.

    This reports presence, never the secret.  A keyless source is usable but
    slower; a keyed source without its key is honestly listed as unavailable
    rather than silently skipped.
    """
    policy = ProviderPolicy.from_environment()
    specs = (
        ("openalex", "OPENALEX_API_KEY", True, "keyless search works; a key enables the polite pool and cursor paging"),
        ("crossref", "CROSSREF_MAILTO", True, "keyless DOI lookup; a contact email joins the polite pool"),
        ("arxiv", "", True, "keyless Atom API; only id lookup and title search are implemented"),
        ("europepmc", "", True, "keyless, biomedical coverage"),
        ("unpaywall", "UNPAYWALL_EMAIL", False, "requires an email; it is never invented"),
        ("semantic_scholar", "SEMANTIC_SCHOLAR_API_KEY", False, "unauthenticated access is heavily rate-limited"),
        ("core", "CORE_API_KEY", False, "requires an API key"),
        ("ads", "ADS_API_TOKEN", False, "requires a NASA ADS token"),
        ("elsevier_tdm", "ELSEVIER_API_KEY", False, "publisher TDM needs the operator's own licence"),
        ("wiley_tdm", "WILEY_TDM_TOKEN", False, "publisher TDM needs the operator's own licence"),
        ("springer_tdm", "SPRINGER_API_KEY", False, "publisher TDM needs the operator's own licence"),
    )
    wired = {"openalex", "crossref"}
    out: list[dict[str, object]] = []
    for provider, env_name, keyless, note in specs:
        configured = bool(policy.credential(provider))
        out.append(
            {
                "provider": provider,
                "wired": provider in wired,
                "keyless_usable": keyless,
                "credential_env": env_name,
                "credential_configured": configured,
                "usable_now": provider in wired and (keyless or configured),
                "note": note,
            }
        )
    return out


@tool(parse_docstring=True)
def infl_capabilities(config: RunnableConfig = None) -> str:
    """Describe what the influence pipeline can and cannot do.

    Use this first. It lists the acquisition ladder, the model, the explanation
    methods, the ten bias audits (including the two that are deliberately not
    attempted) and the files the bundle contains.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON describing the ladder, the model, the audits and the outputs.
    """
    try:
        del config
        return _result(
            status="ok",
            stages=[
                "load",
                "features",
                "topics",
                "model",
                "explain",
                "citation",
                "bias",
                "report",
            ],
            ladder=[
                "user_supplied",
                "open_access_gold",
                "open_access_green",
                "free_to_read",
                "preprint",
                "publisher_tdm",
                "institutional_proxy",
                "abstract_only",
                "metadata_only",
                "acquisition_requested",
            ],
            refusals=[
                "No shadow-library mirrors (Sci-Hub, LibGen, Z-Library) are ever "
                "contacted; the host list is refused by policy before any request.",
                "Credentials are read from the operator's environment for their own "
                "subscriptions; they are never shared, guessed or stored.",
                "Gender, ethnicity and career age are never inferred from names.",
                "A prediction without a frozen reference cohort is refused.",
            ],
            model={
                "target": "citations within N years of publication (default 3)",
                "kind": "three quantile regressors (0.1/0.5/0.9)",
                "output": "80% interval plus median, never a single score",
                "guards": [
                    "right-censored papers are excluded from training",
                    "quantile crossings are repaired and counted",
                    "training-set range is reported so extrapolation is visible",
                ],
            },
            explanations=[
                "ablation delta vs cohort reference value, with bootstrap 95% CI",
                "permutation importance (rank correlation drop) over several seeds",
                "partial dependence on a quantile grid",
                "nearest-neighbour comparators in feature space",
                "tail calibration by observed-count bucket",
            ],
            audits=[
                {"name": name, "label": AUDIT_LABELS.get(name, name)} for name in AUDITS
            ],
            not_attempted={
                "author_demographics": "name-based inference is unreliable",
                "self_citation": "needs author-level reference ownership, absent here",
            },
            sources=_source_status(),
            output_files=[
                "papers.csv",
                "predictions.csv",
                "factors.csv",
                "comparators.csv",
                "importance.csv",
                "bias_audit.csv",
                "bias_audit.md",
                "citation.csv",
                "topics.csv",
                "model_card.json",
                "metrics.json",
                "provenance.jsonl",
                "report.md",
                "README.md",
                "manifest.json",
            ],
            suggested_flow=[
                "infl_cohorts / infl_snapshot_synthetic / infl_snapshot_search: get a cohort",
                "infl_enrich_references: replace id-only OpenAlex references with Crossref deposits",
                "infl_fetch_paper or infl_parse_pdf: add papers the operator supplied",
                "infl_fetch_fulltext: download a legal open copy and attach its sections",
                "infl_run: fit, explain, audit and write the bundle",
                "infl_status / infl_report / infl_bias / infl_explain: report back",
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_cohorts(config: RunnableConfig = None) -> str:
    """List the cohort snapshots stored in this workspace.

    Args:
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of snapshot files with paper counts, source and fingerprint.
    """
    try:
        workspace = _workspace(config)
        directory = _cohort_dir(workspace)
        items = []
        for path in sorted(directory.glob("*.json")):
            try:
                snapshot = CohortSnapshot.load(path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                items.append({"name": path.stem, "error": str(exc)})
                continue
            items.append(
                {
                    "name": path.stem,
                    "path": str(path),
                    "papers": len(snapshot.papers),
                    "source": snapshot.source,
                    "query": snapshot.query,
                    "reference_year": snapshot.reference_year,
                    "synthetic": snapshot.is_synthetic,
                    "fingerprint": snapshot.fingerprint(),
                }
            )
        return _result(status="ok", cohorts=items, count=len(items))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_snapshot_synthetic(
    name: str = "demo",
    count: int = 150,
    seed: int = 4,
    reference_year: int = 0,
    config: RunnableConfig = None,
) -> str:
    """Freeze a reproducible synthetic cohort for testing the pipeline offline.

    The generator encodes known effects (latent quality driving venue, authors,
    references, abstract length and data statements). Metrics computed on it
    measure the pipeline, not the astrophysics; say that when reporting.

    Args:
        name: Snapshot name stored under ``cohorts/<name>.json``.
        count: Number of papers to generate.
        seed: Generator seed; identical seed gives an identical snapshot.
        reference_year: Year the citation window is measured at (0 = current).
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the snapshot path, fingerprint and paper count.
    """
    try:
        workspace = _workspace(config)
        snapshot = synthetic_cohort(
            count=int(count), seed=int(seed), reference_year=int(reference_year or 0)
        )
        path = _cohort_dir(workspace) / f"{name.strip() or 'demo'}.json"
        snapshot.write(path)
        return _result(
            status="ok",
            path=str(path),
            name=path.stem,
            papers=len(snapshot.papers),
            reference_year=snapshot.reference_year,
            fingerprint=snapshot.fingerprint(),
            honesty_note=(
                "Synthetic cohort: it has no field-level provenance and no real "
                "bibliometrics. Report its numbers as pipeline validation only."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_snapshot_search(
    query: str,
    name: str = "",
    limit: int = 40,
    reference_year: int = 0,
    from_year: int = 0,
    to_year: int = 0,
    filter_expression: str = "",
    sort: str = "relevance",
    allow_network: bool = False,
    config: RunnableConfig = None,
) -> str:
    """Search OpenAlex and freeze the result as a cohort snapshot.

    Needs network access, which is off by default. Every paper keeps its route
    (metadata only here: no full text is downloaded) and its field provenance.
    A cohort meant for prediction needs a historical window: use ``from_year``
    and ``to_year`` so the citation horizon has actually elapsed for enough
    papers, instead of taking the relevance-ranked top of a recent query.

    Args:
        query: Topic or title query, e.g. "supernova Ia light curve cosmology".
        name: Snapshot name (defaults to a slug of the query).
        limit: Maximum papers to freeze.
        reference_year: Year the citation window is measured at (0 = current).
        from_year: Earliest publication year (0 means no filter).
        to_year: Latest publication year (0 means no filter).
        filter_expression: Additional OpenAlex filter expression.
        sort: ``relevance``, ``recent``, or ``cited``.
        allow_network: Must be true for this tool to make any request.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the snapshot path or the reason no request was made.
    """
    try:
        workspace = _workspace(config)
        start_year = max(0, int(from_year or 0))
        end_year = max(0, int(to_year or 0))
        if start_year and end_year and start_year > end_year:
            return _result(
                status="error",
                error="from_year cannot be later than to_year",
            )
        sort_value = {
            "": "",
            "relevance": "",
            "recent": "publication_date:desc",
            "cited": "cited_by_count:desc",
        }.get(str(sort or "relevance").strip().lower())
        if sort_value is None:
            return _result(
                status="error",
                error="sort must be one of: relevance, recent, cited",
            )
        transport, refusal = _transport_or_refuse(bool(allow_network))
        if transport is None:
            return _result(status="error", error=refusal)
        policy = ProviderPolicy.from_environment()
        filter_parts: list[str] = []
        if start_year:
            filter_parts.append(f"from_publication_date:{start_year}-01-01")
        if end_year:
            filter_parts.append(f"to_publication_date:{end_year}-12-31")
        extra_filter = str(filter_expression or "").strip()
        if extra_filter:
            filter_parts.append(extra_filter)
        filter_value = ",".join(filter_parts)
        wanted = max(1, int(limit))
        records: list[dict[str, Any]] = []
        statuses: dict[str, int] = {}
        page = 1
        pages_fetched = 0
        # Cursor paging is the right way to walk a large result set, but it
        # answers "401 API key required" without a key. So: use a cursor when a
        # key is configured, otherwise page by number and say so in the notes.
        key, _ = credential("openalex", policy)
        cursor = "*" if key else ""
        paging = "cursor" if cursor else "page"
        while len(records) < wanted:
            result = search_works(
                query,
                transport=transport,
                policy=policy,
                per_page=min(200, wanted - len(records)),
                filter_expression=filter_value,
                page=page,
                cursor=cursor,
                sort=sort_value,
            )
            statuses[str(result.status.value)] = (
                statuses.get(result.status.value, 0) + 1
            )
            if not result.ok:
                if records:
                    break
                return _result(
                    status="error",
                    error=f"openalex_{result.status.value}",
                    detail=result.note,
                    url=result.url,
                )
            payload = result.payload or {}
            page_records = [dict(item) for item in (payload.get("results") or [])]
            if not page_records:
                break
            records.extend(page_records[: wanted - len(records)])
            pages_fetched += 1
            page += 1
            meta = payload.get("meta")
            if cursor:
                next_cursor = str((meta or {}).get("next_cursor") or "")
                if not next_cursor:
                    break
                cursor = next_cursor
        papers = []
        for parsed in records:
            work = _parse_work(parsed)
            if not work:
                continue
            doi = str(work.get("doi") or "")
            access = decide_access(
                doi=doi,
                # The parsed work carries normalized locations; the classifier
                # needs those, not the flattened raw fields.
                candidates=candidates_from_openalex(work),
                has_abstract=bool(work.get("abstract")),
                has_metadata=True,
            )
            work_id = str(work.get("work_id") or "")
            paper_id = (
                f"paper:{doi}" if doi else f"paper:openalex:{work_id.rsplit('/', 1)[-1]}"
            )
            papers.append(
                build_paper(paper_id=paper_id, doi=doi, openalex=work, access=access)
            )
        papers, collapsed = merge_duplicate_papers(papers)
        notes = [
            "metadata_only_snapshot",
            (
                "cursor_paging"
                if paging == "cursor"
                else "page_number_paging_needs_api_key_for_cursor"
            ),
            "relevance_ranked_sample_is_a_moment_not_a_fixed_cohort",
        ]
        if collapsed:
            notes.append(f"merged_{collapsed}_duplicate_records")
        snapshot = CohortSnapshot(
            cohort_id=f"cohort:{name.strip() or _slug(query)}",
            papers=tuple(sorted(papers, key=lambda paper: paper.paper_id)),
            query=query,
            source="openalex",
            reference_year=int(reference_year or 0),
            filters={
                "limit": int(limit),
                "from_year": start_year,
                "to_year": end_year,
                "filter": filter_value,
                "sort": sort,
                "pages_fetched": pages_fetched,
                "paging": paging,
            },
            notes=tuple(notes),
        )
        path = _cohort_dir(workspace) / f"{name.strip() or _slug(query)}.json"
        snapshot.write(path)
        return _result(
            status="ok",
            path=str(path),
            papers=len(snapshot.papers),
            reference_year=snapshot.reference_year,
            fingerprint=snapshot.fingerprint(),
            pages_fetched=pages_fetched,
            paging=paging,
            filters=dict(snapshot.filters),
            provider_statuses=statuses,
            missing={
                field: sum(1 for paper in snapshot.papers if paper.missing.get(field))
                for field in ("title", "abstract", "year", "doi", "venue", "authors")
            },
            honesty_note="Abstracts and metadata only: no full text was fetched.",
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_snapshot_import(
    paths: list[str] | None = None,
    name: str = "",
    query: str = "",
    reference_year: int = 0,
    config: RunnableConfig = None,
) -> str:
    """Freeze a cohort from recorded OpenAlex payloads, with no network at all.

    A snapshot is supposed to be taken once and reused, so the fetch can happen
    wherever the operator has egress and the model run never depends on a live
    API. This tool is that handover: it accepts JSON or JSONL files of raw
    ``/works`` records and writes the same snapshot the search tool would.

    Args:
        paths: Recorded payload files (JSON array, ``{"results": [...]}`` or JSONL).
        name: Snapshot name (defaults to a slug of the query, else "imported").
        query: Query the payloads came from, kept for provenance.
        reference_year: Year the citation window is measured at (0 = current).
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the snapshot path, paper count and fingerprint.
    """
    try:
        workspace = _workspace(config)
        resolved = [_resolve(value, workspace) for value in (paths or [])]
        if not resolved:
            return _result(
                status="error", error="at least one payload file is required"
            )
        payloads: list[dict[str, Any]] = []
        for path in resolved:
            payloads.extend(_read_payloads(path))
        if not payloads:
            return _result(status="error", error="the payload files contain no works")
        cohort_id = f"cohort:{name.strip() or _slug(query) or 'imported'}"
        snapshot = openalex_snapshot(
            payloads,
            cohort_id=cohort_id,
            query=query,
            reference_year=int(reference_year or 0),
            filters={"imported_files": [path.name for path in resolved]},
        )
        path = (
            _cohort_dir(workspace)
            / f"{name.strip() or _slug(query) or 'imported'}.json"
        )
        snapshot.write(path)
        return _result(
            status="ok",
            path=str(path),
            name=path.stem,
            papers=len(snapshot.papers),
            reference_year=snapshot.reference_year,
            fingerprint=snapshot.fingerprint(),
            network_used=False,
            honesty_note=(
                "Imported snapshot: provenance is whatever the recorded payload "
                "carried, and no full text was fetched."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_fetch_paper(
    doi: str,
    name: str = "",
    allow_network: bool = False,
    config: RunnableConfig = None,
) -> str:
    """Resolve one DOI through the ladder and append it to a cohort.

    Tries OpenAlex for bibliometrics and records the access decision: which rungs
    were tried, what was refused and what remains. When nothing readable is
    obtainable it returns the human acquisition steps instead of pretending.

    Args:
        doi: DOI of the paper to add.
        name: Cohort snapshot to append to (created when it does not exist).
        allow_network: Must be true for this tool to make any request.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the access decision, the stored paper id and any refusal notes.
    """
    try:
        workspace = _workspace(config)
        transport, refusal = _transport_or_refuse(bool(allow_network))
        if transport is None:
            return _result(status="error", error=refusal)
        policy = ProviderPolicy.from_environment()
        result = fetch_work(doi, transport=transport, policy=policy)
        if not result.ok:
            return _result(
                status="error",
                error=f"openalex_{result.status.value}",
                detail=result.note,
                acquisition=acquisition_request_placeholder(doi),
            )
        work = _parse_work(result.payload or {})
        # Deciding which route exists is metadata work; only fetching it needs
        # network permission, and that permission is checked by the transport.
        access = decide_access(
            doi=doi,
            candidates=candidates_from_openalex(work),
            has_abstract=bool(work.get("abstract")),
            has_metadata=True,
        )
        paper = build_paper(
            paper_id=f"paper:{work.get('doi') or doi}",
            doi=doi,
            openalex=work,
            access=access,
        )
        cohort_name = name.strip() or "manual"
        path = _cohort_dir(workspace) / f"{cohort_name}.json"
        existing = CohortSnapshot.load(path) if path.is_file() else None
        papers = list(existing.papers) if existing is not None else []
        papers = [item for item in papers if item.paper_id != paper.paper_id]
        papers.append(paper)
        papers, collapsed = merge_duplicate_papers(papers)
        notes = ["manual_additions"]
        if collapsed:
            notes.append(f"merged_{collapsed}_duplicate_records")
        snapshot = CohortSnapshot(
            cohort_id=(existing.cohort_id if existing else f"cohort:{cohort_name}"),
            papers=tuple(sorted(papers, key=lambda item: item.paper_id)),
            query=(existing.query if existing else ""),
            source="manual",
            reference_year=(existing.reference_year if existing else 0),
            filters=dict(existing.filters) if existing else {},
            notes=tuple(notes),
        )
        snapshot.write(path)
        return _result(
            status="ok",
            paper_id=paper.paper_id,
            cohort=path.stem,
            papers=len(snapshot.papers),
            access=access.to_dict(),
            coverage=paper.coverage(),
            missing=paper.missing_fields(),
            full_text_note=(
                "No readable full text: full-text level features (sections, data "
                "statement, readability) are absent and recorded as such."
                if not paper.has_full_text
                else "Full text is available."
            ),
            acquisition_request=acquisition_request(paper.to_dict()),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_fetch_fulltext(
    cohort: str,
    paper_id: str = "",
    doi: str = "",
    url: str = "",
    name: str = "",
    max_candidates: int = 3,
    allow_network: bool = False,
    config: RunnableConfig = None,
) -> str:
    """Download one paper's legal open copy and attach its text layer.

    The access ladder can say that a readable copy exists; this tool is the rung
    that actually fetches it. It tries OpenAlex's posted locations in preference
    order, verifies the bytes are a PDF, extracts sections and the data
    statement, and writes a new snapshot so the input one stays frozen.

    Args:
        cohort: Existing cohort snapshot to take the paper from.
        paper_id: Paper id inside that snapshot (optional when ``doi`` is given).
        doi: DOI to look up inside the snapshot (optional when ``paper_id`` is given).
        url: Explicit legal URL to fetch instead of asking OpenAlex.
        name: New snapshot name (defaults to ``<cohort>_fulltext``).
        max_candidates: How many OpenAlex locations to try before giving up.
        allow_network: Must be true for this tool to make any request.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the stored file, sections found, and any refused/failed rungs.
    """
    try:
        workspace = _workspace(config)
        transport, refusal = _transport_or_refuse(bool(allow_network))
        if transport is None:
            return _result(status="error", error=refusal)
        path = _snapshot_path(workspace, cohort)
        if not path.is_file():
            return _result(status="error", error=f"cohort not found: {cohort}")
        snapshot = CohortSnapshot.load(path)
        wanted = normalise_doi(doi)
        selected = None
        for paper in snapshot.papers:
            if paper_id and paper.paper_id == paper_id:
                selected = paper
                break
            if wanted and normalise_doi(paper.doi) == wanted:
                selected = paper
                break
        if selected is None:
            return _result(
                status="error",
                error="paper not found in cohort",
                hint="pass paper_id from infl_status or a DOI present in the snapshot",
            )
        canonical = normalise_doi(wanted or selected.doi)
        if not canonical and not url:
            return _result(status="error", error="paper has no DOI and no url was given")

        policy = AccessPolicy(allow_network=True)
        candidates = []
        if url:
            candidate = classify_location(
                {"pdf_url": url, "is_oa": True, "source_name": "operator"},
                provider="operator",
            )
            if candidate is None:
                return _result(status="error", error="the url could not be classified")
            candidates = [candidate]
        else:
            policy_meta = ProviderPolicy.from_environment()
            resolved = fetch_work(canonical, transport=transport, policy=policy_meta)
            if not resolved.ok:
                return _result(
                    status="error",
                    error=f"openalex_{resolved.status.value}",
                    detail=resolved.note,
                    acquisition=acquisition_request(selected.to_dict()),
                )
            work = _parse_work(resolved.payload or {})
            candidates = list(rank_candidates(candidates_from_openalex(work), policy=policy))
            candidates = [item for item in candidates if item.route.has_full_text]

        store = _root(workspace) / "fulltext"
        attempts = []
        chosen = None
        result = None
        for candidate in candidates[: max(1, int(max_candidates or 1))]:
            result = fetch_fulltext(
                candidate.url,
                transport=transport,
                policy=policy,
                directory=store,
                doi=canonical,
                timeout=30.0,
            )
            attempt = AccessAttempt(
                route=candidate.route,
                provider=candidate.provider,
                status=(
                    ProviderStatus.OK
                    if result.available
                    else ProviderStatus.INVALID_PAYLOAD
                ),
                url=candidate.url,
                byte_count=result.byte_count,
                sha256=result.sha256,
                path=result.path,
                license=candidate.license,
                note=",".join(result.warnings),
                retrieved_at=utc_now(),
            )
            attempts.append(attempt)
            if result.available:
                chosen = candidate
                break

        if result is None or not result.available or chosen is None:
            return _result(
                status="error",
                error="full_text_unavailable",
                paper_id=selected.paper_id,
                tried=[attempt.to_dict() for attempt in attempts],
                acquisition=acquisition_request(selected.to_dict()),
                honesty_note=(
                    "No legal open copy could be read. The paper stays at its "
                    "metadata/abstract level and no text-level features were invented."
                ),
            )

        decision = decide_access(
            doi=canonical,
            candidates=(chosen,),
            has_abstract=bool(selected.abstract),
            has_metadata=True,
        )
        decision = with_attempt(decision, attempts[-1], route=chosen.route)
        updated = apply_fulltext(selected, result, access=decision)
        papers = [updated if item.paper_id == selected.paper_id else item for item in snapshot.papers]
        label = _safe_stem(name.strip() or f"{path.stem}_fulltext")
        output = CohortSnapshot(
            cohort_id=f"cohort:{label}",
            papers=tuple(papers),
            query=snapshot.query,
            source=f"{snapshot.source}+fulltext",
            reference_year=snapshot.reference_year,
            filters={**dict(snapshot.filters), "fulltext_fetch": "legal_open_copy"},
            notes=("full_text_fetched", "input_snapshot_unchanged"),
        )
        output.write(_cohort_dir(workspace) / f"{label}.json")
        return _result(
            status="ok",
            paper_id=updated.paper_id,
            cohort=label,
            path=str(_cohort_dir(workspace) / f"{label}.json"),
            papers=len(output.papers),
            file=result.path,
            pages=result.page_count,
            sections=sorted(updated.sections),
            open_data=[record.url for record in updated.open_data],
            references=len(updated.references),
            missing=updated.missing_fields(),
            access=decision.to_dict(),
            tried=[attempt.to_dict() for attempt in attempts],
            honesty_note=(
                "Only text recovered from the downloaded PDF was added; index "
                "bibliometrics were not replaced."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_parse_pdf(
    paths: list[str] | None = None,
    name: str = "",
    config: RunnableConfig = None,
) -> str:
    """Parse operator-supplied PDFs and append them to a cohort snapshot.

    This is the top rung of the ladder: the operator already has the file. The
    text layer supplies sections, the data statement and reference candidates;
    nothing is invented for fields the PDF does not contain.

    Args:
        paths: PDF paths inside the task workspace.
        name: Cohort snapshot to append to (defaults to ``uploads``).
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with each paper's route, sections found and missing fields.
    """
    try:
        workspace = _workspace(config)
        if not paths:
            raise ValueError("paths must list at least one PDF")
        cohort_name = name.strip() or "uploads"
        path = _cohort_dir(workspace) / f"{cohort_name}.json"
        existing = CohortSnapshot.load(path) if path.is_file() else None
        store = _root(workspace) / "store"
        papers = list(existing.papers) if existing is not None else []
        parsed = []
        for value in paths:
            target = _resolve(value, workspace)
            paper = paper_from_upload(target, directory=store)
            papers = [item for item in papers if item.paper_id != paper.paper_id]
            papers.append(paper)
            parsed.append(
                {
                    "paper_id": paper.paper_id,
                    "path": str(target),
                    "pages": paper.fulltext_pages,
                    "sections": sorted(paper.sections),
                    "open_data": [record.url for record in paper.open_data],
                    "references": len(paper.references),
                    "missing": paper.missing_fields(),
                }
            )
        papers, collapsed = merge_duplicate_papers(papers)
        notes = ["user_supplied_full_text"]
        if collapsed:
            notes.append(f"merged_{collapsed}_duplicate_records")
        snapshot = CohortSnapshot(
            cohort_id=(existing.cohort_id if existing else f"cohort:{cohort_name}"),
            papers=tuple(sorted(papers, key=lambda item: item.paper_id)),
            query=(existing.query if existing else ""),
            source="user_supplied",
            reference_year=(existing.reference_year if existing else 0),
            filters=dict(existing.filters) if existing else {},
            notes=tuple(notes),
        )
        snapshot.write(path)
        return _result(
            status="ok",
            cohort=path.stem,
            path=str(path),
            papers=len(snapshot.papers),
            parsed=parsed,
            honesty_note=(
                "A user-supplied PDF has no citation history until it is matched to "
                "an indexed record, so it can be explained but not scored. Pass "
                "allow_network=true to infl_run, or infl_fetch_paper for its DOI, "
                "to match it before scoring."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_enrich_references(
    cohort: str,
    name: str = "",
    limit: int = 0,
    workers: int = 4,
    allow_network: bool = False,
    config: RunnableConfig = None,
) -> str:
    """Replace opaque OpenAlex reference ids with Crossref deposit metadata.

    OpenAlex lists which works a paper cites but often only as ids. Crossref
    publishes the reference list the publisher deposited, including titles and
    DOIs. This pass fetches those deposits, caches them by DOI, and writes a new
    frozen cohort; the input snapshot is never modified. Run it once, then run
    the model offline against the enriched cohort.

    Args:
        cohort: Cohort name or snapshot path (see infl_cohorts).
        name: Name for the enriched snapshot; defaults to <cohort>_refs.
        limit: Maximum eligible papers to fetch (0 means all of them).
        workers: Concurrent Crossref requests (clamped to 1..8).
        allow_network: Must be true; the pass contacts api.crossref.org.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the new snapshot path, before/after readable-reference counts,
        and a status breakdown (enriched, cached, no_references, not_found).
    """
    try:
        if not allow_network:
            return _result(
                status="error",
                error=(
                    "network_disabled: no request was sent. Pass "
                    "allow_network=true only when the operator has agreed to "
                    "online lookups."
                ),
            )
        workspace = _workspace(config)
        path = _snapshot_path(workspace, cohort.strip())
        snapshot = CohortSnapshot.load(path)
        cache_file = _root(workspace) / "crossref_references.json"
        policy = ProviderPolicy.from_environment()
        papers, report = enrich_references(
            snapshot.papers,
            transport_factory=lambda: _transport(True),
            policy=policy,
            cache_path=cache_file,
            max_papers=max(0, int(limit)),
            workers=max(1, min(8, int(workers))),
        )
        papers, collapsed = merge_duplicate_papers(papers)
        label = _safe_stem(name.strip()) if name.strip() else f"{path.stem}_refs"
        output = _cohort_dir(workspace) / f"{label}.json"
        enriched = CohortSnapshot(
            cohort_id=f"cohort:{label}",
            papers=tuple(sorted(papers, key=lambda paper: paper.paper_id)),
            query=snapshot.query,
            source=f"{snapshot.source}+crossref",
            reference_year=snapshot.reference_year,
            filters={
                **dict(snapshot.filters),
                "reference_enrichment": "crossref_deposit",
            },
            notes=tuple(snapshot.notes)
            + (
                "references_enriched_from_crossref_deposit",
                "input_snapshot_unchanged",
            ),
        )
        enriched.write(output)
        return _result(
            status="ok",
            cohort=label,
            path=str(output),
            papers=len(enriched.papers),
            merged_duplicates=collapsed,
            fingerprint=enriched.fingerprint(),
            enrichment=report.to_dict(),
            cache=str(cache_file),
            honesty_note=(
                "A Crossref deposit can be incomplete or stale; references that "
                "remain id-only stay visible in the counts rather than being "
                "invented."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_run(
    cohort: str = "",
    uploads: list[str] | None = None,
    reference_year: int = 0,
    seed: int = 0,
    subject_ids: list[str] | None = None,
    audits: list[str] | None = None,
    horizon: int = 3,
    max_subjects: int = 5,
    run_id: str = "",
    allow_network: bool = False,
    enrich_uploads: bool = True,
    config: RunnableConfig = None,
) -> str:
    """Fit, explain, audit and write the bundle for one cohort.

    Args:
        cohort: Cohort name or snapshot path (see infl_cohorts).
        uploads: Extra PDF paths to parse and explain in this run.
        reference_year: Year the citation window is measured at (0 = snapshot's).
        seed: Random seed for the model and the bootstrap.
        subject_ids: Papers to explain; defaults to the most-cited ones.
        audits: Subset of audit names (defaults to all ten).
        horizon: Citation window length in years.
        max_subjects: Maximum papers to explain in one run.
        run_id: Explicit run id (defaults to a timestamped id).
        allow_network: Let uploads be matched to OpenAlex and let the citation
            stage query reference neighbourhoods. Off by default; without it an
            uploaded PDF has no citation history and cannot be scored.
        enrich_uploads: Try to match uploaded PDFs to an indexed record. True by
            default, but it only contacts the network when allow_network is true.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON summary: metrics, audit counts, package directory and notes.
    """
    try:
        workspace = _workspace(config)
        if not cohort.strip():
            raise ValueError(
                "cohort is required: a prediction needs a frozen reference "
                "population, otherwise the number is not comparable to anything."
            )
        snapshot = CohortSnapshot.load(_snapshot_path(workspace, cohort.strip()))
        snapshot = _apply_reference_year(snapshot, int(reference_year or 0))
        options = RunOptions(
            reference_year=int(reference_year or 0),
            horizon=int(horizon),
            seed=int(seed),
            max_subjects=int(max_subjects),
            subject_ids=tuple(subject_ids or ()),
            audits=tuple(audits) if audits else AUDITS,
            allow_network=bool(allow_network),
            enrich_uploads=bool(enrich_uploads),
        )
        paths = tuple(_resolve(value, workspace) for value in (uploads or ()))
        result = run_influence(
            workspace=workspace,
            snapshot=snapshot,
            uploads=paths,
            options=options,
            run_id=run_id.strip(),
        )
        return _result(
            status="ok",
            **result.summary(),
            package_dir=result.package_dir,
            report=str(Path(result.package_dir) / "report.md"),
            metrics_summary={
                key: result.metrics.get(key)
                for key in (
                    "spearman",
                    "top_decile_lift",
                    "interval_coverage",
                    "median_absolute_error_log",
                    "quantile_crossings",
                    "labelled",
                )
                if key in result.metrics
            },
            calibration=result.metrics.get("decile_calibration", []),
            next_steps=[
                "Read report.md for the interval, the factors and the audit.",
                "Use infl_explain for one paper's factors and comparators.",
                "Quote the interval width, not just the median.",
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_status(run_id: str, config: RunnableConfig = None) -> str:
    """Read a finished run's stage receipts.

    Args:
        run_id: Run identifier returned by infl_run.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the stages, their counts and the run's package directory.
    """
    try:
        workspace = _workspace(config)
        record = load_run(_run_dir(workspace, run_id))
        return _result(status="ok", run=record)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_report(run_id: str, config: RunnableConfig = None) -> str:
    """Return the written report of a run.

    Args:
        run_id: Run identifier returned by infl_run.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the report path and its full text.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        path = directory / "package" / "report.md"
        if not path.is_file():
            raise ValueError(f"run {run_id} has no report.md")
        return _result(
            status="ok", path=str(path), report=path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_bias(run_id: str, status: str = "", config: RunnableConfig = None) -> str:
    """List the bias audit findings of a run, optionally filtered by status.

    Args:
        run_id: Run identifier returned by infl_run.
        status: One of detected / not_detected / insufficient_evidence / cannot_assess.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON list of findings with method, evidence, conclusion and severity.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        path = directory / "package" / "bias_audit.csv"
        if not path.is_file():
            raise ValueError(f"run {run_id} has no bias audit")
        wanted = status.strip().casefold()
        rows = []
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if wanted and row.get("status") != wanted:
                    continue
                rows.append(row)
        return _result(
            status="ok",
            findings=rows[:_MAX_LISTED],
            count=len(rows),
            detected=[row["label"] for row in rows if row.get("status") == "detected"],
            cannot_assess=[
                row["label"] for row in rows if row.get("status") == "cannot_assess"
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_explain(run_id: str, paper_id: str = "", config: RunnableConfig = None) -> str:
    """Return one paper's factor breakdown and comparators from a run.

    Args:
        run_id: Run identifier returned by infl_run.
        paper_id: Paper to read; defaults to the first explained paper.
        config: Runtime-injected task workspace configuration.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        package = directory / "package"
        factor_path = package / "factors.csv"
        if not factor_path.is_file():
            raise ValueError(f"run {run_id} has no explanations")
        rows = _read_csv(factor_path)
        subject = paper_id.strip() or (rows[0]["paper_id"] if rows else "")
        factors = [row for row in rows if row["paper_id"] == subject]
        comparators = [
            row
            for row in _read_csv(package / "comparators.csv")
            if row.get("paper_id") == subject
        ]
        prediction = next(
            (
                row
                for row in _read_csv(package / "predictions.csv")
                if row.get("paper_id") == subject
            ),
            {},
        )
        return _result(
            status="ok",
            paper_id=subject,
            prediction=prediction,
            factors=factors,
            comparators=comparators[:_MAX_LISTED],
            reading_hint=(
                "A factor is a delta: set it to the cohort reference value and see "
                "how the estimate moves. When the 95% CI crosses zero, the factor "
                "is not a usable explanation."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_package(run_id: str, config: RunnableConfig = None) -> str:
    """Return the bundle manifest of a run: files and their sha256 digests.

    Args:
        run_id: Run identifier returned by infl_run.
        config: Runtime-injected task workspace configuration.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        path = directory / "package" / "manifest.json"
        if not path.is_file():
            raise ValueError(f"run {run_id} has no manifest")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        return _result(
            status="ok",
            directory=str(path.parent),
            manifest=manifest,
            verify_hint=(
                "Each artefact's sha256 is in the manifest, so a modified CSV is "
                "detectable without trusting the run."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_verify(run_id: str, config: RunnableConfig = None) -> str:
    """Recompute a run's artefact digests and report any drift.

    The manifest is written by the producer, so trusting it proves nothing. This
    re-reads every file and checks the byte digest (any edit, including a
    rewritten timestamp) and the stable digest (an edit that changes a finding),
    then recomputes the package fingerprint from what it verified.

    Args:
        run_id: Run identifier returned by infl_run.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with `ok`, the failed roles, each artefact's verdict, and both the
        claimed and the recomputed package fingerprint.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id) / "package"
        report = verify_package(directory)
        return _result(
            status="ok" if report.get("ok") else "drift", verification=report
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_cohort_diff(
    older: str,
    newer: str,
    write_artifact: bool = True,
    config: RunnableConfig = None,
) -> str:
    """Measure what changed between two frozen cohorts of the same question.

    A search-backed cohort is a moment: the query is fixed, the ranking is not.
    This compares two snapshots and reports membership churn, how far the
    survivors' own numbers moved, and how far their place inside the cohort
    moved — the last one matters because rank-derived features are relative to
    whoever else is in the cohort.

    Args:
        older: Cohort name or path of the earlier snapshot.
        newer: Cohort name or path of the later snapshot.
        write_artifact: Whether to save the comparison as JSON and Markdown.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON diff with membership, value drift, rank drift, a verdict, the
        caveats that limit it, and the paths of any written artifacts.
    """
    try:
        workspace = _workspace(config)
        before = CohortSnapshot.load(_snapshot_path(workspace, older))
        after = CohortSnapshot.load(_snapshot_path(workspace, newer))
        diff = diff_snapshots(before, after)
        payload: dict[str, Any] = {
            "status": "ok",
            "diff": diff.to_dict(),
            "summary": diff.summary_line(),
            "markdown": drift_markdown(diff),
        }
        if write_artifact:
            directory = _root(workspace) / "diffs"
            directory.mkdir(parents=True, exist_ok=True)
            stem = f"{_safe_stem(before.cohort_id)}__vs__{_safe_stem(after.cohort_id)}"
            json_path = directory / f"{stem}.json"
            md_path = directory / f"{stem}.md"
            json_path.write_text(
                json.dumps(
                    diff.to_dict(), ensure_ascii=False, indent=2, sort_keys=True
                ),
                encoding="utf-8",
            )
            md_path.write_text(drift_markdown(diff) + "\n", encoding="utf-8")
            payload["artifacts"] = {
                "json": str(json_path),
                "markdown": str(md_path),
            }
        return _result(**payload)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


@tool(parse_docstring=True)
def infl_acquisition_request(
    doi: str = "", title: str = "", config: RunnableConfig = None
) -> str:
    """Describe how a human can legitimately obtain a paper the run could not read.

    Use this instead of giving up silently: it lists interlibrary loan, author
    request, library copy and purchase, in that order.

    Args:
        doi: DOI of the paper.
        title: Human-readable title for the request.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with the citation line and the numbered steps.
    """
    try:
        del config
        label = title.strip() or doi.strip() or "the paper"
        return _result(
            status="ok",
            title=label,
            doi=doi.strip(),
            steps=acquisition_request_placeholder(doi, title=label),
            refused=[
                "shadow-library mirrors",
                "borrowed or shared credentials",
                "scraping behind a paywall against the publisher's terms",
            ],
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


def acquisition_request_placeholder(doi: str, *, title: str = "") -> dict[str, Any]:
    """The four legitimate routes a human can take, in order of effort."""
    label = title.strip() or doi.strip() or "the paper"
    return {
        "title": label,
        "doi": doi.strip(),
        "steps": [
            {
                "order": 1,
                "route": "interlibrary_loan",
                "detail": "Ask your library to borrow it.",
            },
            {
                "order": 2,
                "route": "author_request",
                "detail": "Email the corresponding author for a copy.",
            },
            {
                "order": 3,
                "route": "library_copy",
                "detail": "Use the institutional subscription you already have.",
            },
            {
                "order": 4,
                "route": "publisher_purchase",
                "detail": "Buy the article from the publisher.",
            },
        ],
        "note": (
            "Supply the file with infl_parse_pdf once obtained; the run will then "
            "use its text layer."
        ),
    }


def _parse_work(payload: dict[str, Any]) -> dict[str, Any]:
    from jw.research_influence.providers.openalex import parse_work

    return parse_work(payload)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_payloads(path: Path) -> list[dict[str, Any]]:
    """Read one recorded export: JSONL, a JSON array, or an API envelope."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [
            item
            for line in text.splitlines()
            if line.strip()
            for item in [json.loads(line)]
            if isinstance(item, dict)
        ]
    return _payload_records(json.loads(text))


def _payload_records(payload: Any) -> list[dict[str, Any]]:
    """Accept the three shapes a recorded OpenAlex export arrives in."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("results", "works", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _slug(text: str) -> str:
    cleaned = "".join(
        character if character.isalnum() else "-" for character in text.strip().lower()
    )
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts)[:48] or "cohort"


@tool(parse_docstring=True)
def infl_network(run_id: str, paper_id: str = "", config: RunnableConfig = None) -> str:
    """Return the citation-network measurements of a run's subject papers.

    Args:
        run_id: Run identifier returned by infl_run.
        paper_id: Paper to read; empty means every measured paper.
        config: Runtime-injected task workspace configuration.

    Returns:
        JSON with reference quality, incoming attention, self-citation and the
        PageRank percentile inside the resolved graph, plus how to read them.
    """
    try:
        workspace = _workspace(config)
        directory = _run_dir(workspace, run_id)
        path = directory / "package" / "citation.csv"
        if not path.is_file():
            raise ValueError(
                f"run {run_id} has no citation network: it needs allow_network=True"
            )
        rows = _read_csv(path)
        wanted = paper_id.strip()
        if wanted:
            rows = [row for row in rows if row.get("paper_id") == wanted]
            if not rows:
                raise ValueError(f"paper {wanted} was not measured in run {run_id}")
        return _result(
            status="ok",
            papers=rows[:_MAX_LISTED],
            count=len(rows),
            measured=sum(1 for row in rows if row.get("status") == "ok"),
            reading_hint=(
                "references_in_top_decile is a share, not a count: it says how much "
                "of the cited literature is itself well cited. self_citations is a "
                "lower bound from surname overlap only. pagerank_percentile is "
                "computed inside the references we could resolve, so a paper whose "
                "references resolved poorly sits lower than its real position."
            ),
        )
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        return _result(status="error", error=str(exc))


INFLUENCE_TOOLS = [
    infl_capabilities,
    infl_cohort_diff,
    infl_cohorts,
    infl_snapshot_synthetic,
    infl_snapshot_search,
    infl_snapshot_import,
    infl_enrich_references,
    infl_fetch_paper,
    infl_fetch_fulltext,
    infl_parse_pdf,
    infl_run,
    infl_status,
    infl_report,
    infl_bias,
    infl_explain,
    infl_network,
    infl_package,
    infl_acquisition_request,
]

register_tool_bundle("research-influence", INFLUENCE_TOOLS, include_in_main=False)

__all__ = ["INFLUENCE_TOOLS"]
