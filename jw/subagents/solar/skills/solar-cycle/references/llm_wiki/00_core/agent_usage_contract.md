# Agent Usage Contract

The built-in Wiki is shared grounding, but each role consumes a different
subset and must preserve different boundaries.

| Role | Required Wiki knowledge | Required output behavior |
| --- | --- | --- |
| Unified research agent | scope, mechanism map, conflicts, lifecycle | coordinate conflicting evidence; sign candidate writeback; prevent overclaiming |
| Research planner | known mechanisms, standard experiments, prior gaps | include data, mechanism, falsification, evaluation, and next-test steps; cite entry ids |
| Data and features | source coverage, calibration, proxy meaning, feature definitions | label missingness, substitutions, drift, units, and physical interpretation |
| Automatic experiment | backtest, ablation, drift, precursor and robustness paradigms | record versions, splits, metrics, warnings, failures, and compared knowledge ids |
| Scientific hypothesis | mechanisms, counterconditions, hypothesis templates | produce falsifiable cards; separate observations, experiment results, and mechanisms |
| Evidence review | canonical boundaries, source limitations, counterexamples, wording risks | score support and pressure from counterevidence; adjust confidence; identify next test |
| Knowledge manager | entry schema, promotion, deprecation, conflict and provenance rules | ingest only as candidate; never silently overwrite; preserve lifecycle history |

## Task-to-agent knowledge handoff

Every material statement handed from one role to the next must include:

- the Wiki entry ids that provide its domain premise;
- the source or data version when the statement is empirical;
- the valid range and known limitations;
- whether it is an observation, statistical result, model interpretation, or
  mechanism claim;
- any unresolved counterexample or knowledge gap.

## Forbidden shortcuts

- A planning Agent may not invent a stock experiment unrelated to the stated
  mechanism question.
- A data Agent may not label an empirical proxy as an internal solar quantity.
- An experiment Agent may not select only favorable cycles or hide failed
  ablations.
- A hypothesis Agent may not use model plausibility as observational evidence.
- An evidence reviewer may not treat canonical status as immunity from new
  counterevidence.
- A knowledge manager may not promote a single-run result directly into the
  built-in Wiki.
