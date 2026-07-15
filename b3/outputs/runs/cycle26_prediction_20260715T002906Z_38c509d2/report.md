# Solar Cycle 26 strength and mechanism explanation

- run_id: `cycle26_prediction_20260715T002906Z_38c509d2`
- created_at: `2026-07-15T00:29:06.032038+00:00`
- prediction class: `weak-to-moderate`
- confidence: `0.74`
- claim boundary: 结论限于回顾性观测约束、反证和假设优先级，因果机制仍待独立验证；产物仅供研究。

## Top Hypothesis

**H1_poloidal_precursor_needed**: Cycle-26 confidence should be governed by polar-field precursor evidence, not by sunspot/F10.7 proxies alone.

- mechanism: Babcock-Leighton flux-transport dynamo
- evidence score: 0.82
- next test: Add NSO/SOLIS or polar faculae proxies to extend polar-field evidence before 1976.

## Supporting Evidence

- WSO polar precursor pairs available = 4
- Spearman polar-strength-vs-next-peak = 0.8

## Counter Evidence

- Only a small number of complete WSO-to-next-cycle pairs are available, so the result is a constraint rather than a definitive forecast.

## Tournament Ranking

- H1_poloidal_precursor_needed: Elo 1059.7
- H2_waldmeier_constraint: Elo 1030.0
- H3_proxy_relation_drift: Elo 1000.2
- H4_hemispheric_asymmetry: Elo 970.1
- H5_low_order_dynamo_closure: Elo 940.0

## Self Corrections

- immature_cycle26_polar_precursor: Use WSO as a historical precursor constraint, but keep Cycle-26 amplitude bounded until the Cycle-25/26 minimum-time polar field is mature and cross-validated.
- proxy_relation_drift: Lower confidence for single-proxy hypotheses and request phase-stratified reanalysis.
- hemispheric_coverage_limit: Use asymmetry as modern-era mechanism evidence only, because the available SILSO hemispheric product begins in 1992.

## Iteration Trace

- Iteration 1 `baseline_cycle_morphology` (completed): 0.5 -> 0.7; next: Request polar-field precursor evidence before making any Cycle-26 amplitude statement.
- Iteration 2 `polar_precursor_and_toy_model` (completed): 0.7 -> 0.82; next: Run evidence review and lower confidence where sparse data or proxy drift creates risk.
- Iteration 3 `evidence_review_and_self_correction` (completed): 0.82 -> 0.74; next: Export the run as JSON/Markdown and ask the next data-acquisition iteration to extend polar precursor evidence.

## Next Validation

- Add NSO/SOLIS or polar faculae proxies to extend polar-field evidence before 1976.
- Run leave-one-cycle-out robustness and compare with a low-order dynamo toy model.
- Repeat with smoothed F10.7 and activity-phase stratification.
