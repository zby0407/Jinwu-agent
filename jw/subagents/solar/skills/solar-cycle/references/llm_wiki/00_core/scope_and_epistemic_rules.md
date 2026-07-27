# Scope and Epistemic Rules

## Scientific scope

The built-in Wiki supports a bounded AI Scientist workflow centered on:

- Solar Cycle 26 trend assessment with an explicit uncertainty range.
- Physical interpretation through solar-cycle and dynamo mechanisms.
- Polar-field precursor evidence and its limited historical sample.
- Stability or drift of relationships among solar-activity indicators.
- Hypothesis generation, counterevidence review, and next-test selection.
- Active-region and flare knowledge only where it informs the above tasks or
  prevents misuse of short-duration event data.

The Wiki does not authorize deterministic official forecasting, creation of
unobserved numerical values, or a claim that one statistical relationship proves
a dynamo mechanism.

## Knowledge classes

The design document defines seven legal entry types:

- `concept`
- `mechanism`
- `data_source`
- `experiment_paradigm`
- `hypothesis_template`
- `finding`
- `counterexample`

The first five provide stable built-in domain grounding. `finding` and
`counterexample` normally belong to dynamic research memory and remain
`candidate` until promoted through the review gate.

## Epistemic interpretation

- `confidence=high`: authoritative support and a well-bounded statement. It
  does not mean universal or exception-free.
- `confidence=medium`: useful working knowledge with material data, model, or
  scope limitations.
- `confidence=low`: provisional context; it must not independently support a
  strong conclusion.
- `status=canonical`: reviewed knowledge usable for grounding.
- `status=candidate`: not yet a built-in premise.
- `status=deprecated` or `superseded`: historical knowledge retained for audit,
  not an active premise.

## Mandatory reasoning boundaries

1. Sunspot number and F10.7 are activity proxies, not direct measurements of
   the internal solar magnetic field.
2. Correlation, predictive usefulness, and physical mechanism are three
   different claims. Never collapse them.
3. Polar-field observations span few cycles; a precursor relation constrains a
   trend but does not justify deterministic Cycle 26 forecasting.
4. A model result is not scientific evidence until its data version, split,
   backtest, uncertainty, and failure conditions are available.
5. Leave-one-cycle-out or other historical tests must preserve temporal and
   cycle boundaries. Feature selection and tuning must not use the held-out
   target.
6. Relationship drift invalidates silent reuse of a proxy calibration across
   cycles or phases.
7. Short-duration flare records cannot replace long-duration cycle indicators.
   Flare occurrence may be a manifestation of magnetic activity without being
   a direct measure of the cycle's dynamo state.
8. Missing data, proxy substitution, calibration revisions, and low sample size
   travel with every downstream conclusion.
9. A new result that conflicts with canonical knowledge becomes a conflict for
   review, not an automatic correction and not something to hide.
10. When the Wiki lacks support, report a knowledge gap and propose the next
    observation, source review, or experiment.

## Canonical promotion boundary

A candidate may become canonical only through at least one path defined by the
system design:

- independent cross-run reproduction without an unresolved counterexample;
- direct support from peer-reviewed literature or an authoritative textbook;
- explicit domain-expert approval.

Promotion records its reason, supporting sources or run ids, reviewer, scope,
and version. Deprecated entries are retained with the reason and replacement.
