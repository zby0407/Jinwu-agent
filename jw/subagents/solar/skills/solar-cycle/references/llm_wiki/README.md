# Solar-Cycle Co-Scientist Built-in LLM Wiki

This directory is the versioned, model-facing knowledge skeleton for the
Solar-Cycle Co-Scientist. Its design basis is
`docs/architecture/2026-06-20-solar-cycle-co-scientist-design.md`.

The Wiki is intentionally bounded. It exists to support the project's concrete
research tasks:

1. Solar Cycle 26 trend assessment with mechanism interpretation.
2. Polar-field precursor hypothesis review.
3. F10.7–sunspot relationship drift analysis.
4. Evidence review of the approximately 11-year cycle and solar-dynamo
   mechanisms.
5. Knowledge provenance and conflict review.
6. Leakage-controlled full-disk or active-region solar-flare probability
   forecast design and verification.
7. Secondary active-region and flare context needed by solar-cycle tasks.

It is not a dump of the JSONbook corpus and it is not a general astronomy
encyclopedia. JSONbook is an offline reference collection used to write and
review compact canonical entries. Raw book chunks do not belong in this
directory.

## Runtime reading contract

1. Always read the files listed under `always_load` in
   `_meta/manifest.yaml`.
2. Select one task bundle from `_meta/manifest.yaml`.
3. Read that bundle's module indexes and available entry files.
4. Cite material Wiki entry ids in downstream structured artifacts.
5. Preserve every entry's `valid_range`, uncertainty, proxy limitation, and
   correlation-versus-mechanism boundary.
6. If the Wiki does not support a claim, record a knowledge gap. Do not fill the
   gap from model memory and present it as canonical.

This is deterministic task-based loading, not embedding or vector retrieval.
Explicit flare forecasts load the `flare_forecast` task bundle and follow the
shared `solar-flare-forecasting` skill; they do not require a new sub-agent.

## Dynamic literature source layer

`_meta/literature_feeds.json` defines bounded subscriptions for the project's
solar-cycle, polar-field, dynamo, F10.7, hemispheric-asymmetry, and flare
questions. `lit_feed_catalog` lists them and `lit_feed_sync(feed_id)` refreshes
one feed at a time.

Feed searches use a recent-year window, relevance ranking, and a deterministic
title/abstract term gate. Accepted hits are family-deduplicated and recorded
with provider, publication, refereed/retraction, first-seen, and sync-receipt
metadata. A provider credential or outage yields `partial`/`unavailable` rather
than a fabricated citation. NASA ADS uses `ADS_API_TOKEN`; OpenAlex can use
`OPENALEX_API_KEY`; Crossref polite-pool contact uses `CROSSREF_MAILTO`.

The feed is a discovery layer, not a second reusable grounding path. Each
cached source has a content fingerprint and immutable delta events distinguish
the historical baseline from new sources, versions, metadata changes,
retractions, and feed membership changes.

For hypothesis work, `lit_bundle_build` may freeze a task-specific snapshot of
at most five directly relevant cached abstracts. A dedicated evidence binder
checks every quote against that snapshot. Those quotes may support, oppose, or
limit hypotheses in that one task, but they are not Wiki entries and cannot be
reused as canonical grounding.

For Wiki maintenance, a changed source first creates a quote-grounded
source-to-entry impact (`supports`, `contradicts`, `qualifies`, or `extends`).
Any concrete edit is saved only as a `proposal_only` patch against an exact
entry version. It never auto-merges; a retraction preserves the source and
blocks affected grounding until fresh task-bound evidence is ingested.

## Write boundary

- Files in this directory are the built-in canonical seed and are maintained
  from traceable source work.
- Research-run findings, counterexamples, and failed experiments begin as
  `candidate` entries in the dynamic knowledge service.
- A runtime candidate becomes canonical only after cross-run reproduction;
  promotion writes an explicit versioned Wiki update.
- Deprecated knowledge is retained with its replacement or deprecation reason.

## Directory map

```text
00_core/                         always-loaded scientific boundaries and maps
10_solar_cycle/                  cycle morphology, Hale cycle, Waldmeier context
20_dynamo_and_polar_field/       dynamo mechanisms and polar precursor physics
30_indicators_and_features/      observables, proxies, features, drift semantics
40_active_regions_and_flares/    active-region and flare context
50_data_sources/                 source, calibration, coverage, and bias knowledge
60_experiment_paradigms/         standard tests, backtests, ablations, diagnostics
70_hypothesis_templates/         reusable falsifiable hypothesis structures
80_evidence_review/              scoring, counterevidence, and wording controls
90_research_memory/              dynamic finding/counterexample interface
_meta/                           loader-safe manifests, catalogs, and source plan
templates/                       entry and review templates
```

## Scaffold status

- `seeded`: an existing canonical entry is already present.
- `planned`: the entry has a defined role but no reviewed canonical page yet.
- `candidate`: drafted from sources but not accepted as canonical.
- `canonical`: reviewed and safe for built-in grounding.
- `deprecated` / `superseded`: retained historical knowledge.

`_meta/entry_catalog.yaml` is the authoritative inventory for seeded and
planned entries. Empty placeholder pages are deliberately avoided: a missing
page is an explicit knowledge gap, not fake completeness.
