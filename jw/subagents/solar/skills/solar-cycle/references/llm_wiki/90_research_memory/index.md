# Module 90 — Research Memory Interface

## Purpose

Define how dynamic findings, counterexamples, and failed experimental
experience interact with the built-in Wiki without contaminating canonical
knowledge.

## Memory entry families

- `finding`: a non-trivial result tied to an immutable run and verified
  experiment artifact.
- `counterexample`: a cycle, regime, ablation, or observation that weakens a
  claim.
- failed-experiment experience: represented as a candidate finding or
  counterexample when it has reusable methodological value.

## Candidate writeback requirements

- originating `run_id`;
- exact research question;
- data and source versions;
- experiment or verification artifact;
- claim and valid range;
- uncertainty and limitations;
- related canonical ids;
- reason the result is non-trivial and reusable;
- unresolved conflicts.

## Promotion and conflict rules

- A single run remains candidate unless authoritative literature or expert
  review independently supports it.
- Cross-run reproduction must use meaningfully independent runs, not repeated
  execution of the same cached result.
- A counterexample does not silently delete a canonical mechanism; it narrows,
  conflicts with, deprecates, or supersedes a precisely identified claim.
- Built-in Wiki updates happen through reviewed versioned files, not by direct
  runtime mutation of this directory.

This module is used primarily by the unified research Agent, evidence reviewer,
and knowledge manager during the provenance/conflict test scenario.
