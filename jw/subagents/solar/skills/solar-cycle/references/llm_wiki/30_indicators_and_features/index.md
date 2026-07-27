# Module 30 — Indicators, Proxies, and Physical Features

## Purpose

Define what each observable measures, how project features are constructed, and
where proxy interpretation becomes unsafe.

## Required knowledge families

- International sunspot number as an empirical activity index.
- F10.7 radio flux as a chromospheric/coronal activity proxy.
- Direct polar-field measurements and historical polar proxies.
- Cycle amplitude, minimum, length, phase duration, rise slope, and integrated
  activity.
- North–south asymmetry and phase lag.
- Relationship drift across cycles, phases, and activity levels.
- Missingness, normalization, smoothing, alignment, and proxy substitution.

## Feature semantics contract

Every feature page must state:

- mathematical definition;
- input cadence and smoothing;
- units or dimensionless status;
- physical interpretation;
- whether the feature is direct, derived, or a proxy;
- valid cycle/phase range;
- known sensitivity to source version or boundary selection;
- leakage risks when used for prediction.

## Questions this module must answer

1. What can sunspot number, F10.7, and polar-field data legitimately indicate?
2. When are F10.7 and sunspot number non-interchangeable?
3. How should drift be defined and tested across cycles?
4. Which Cycle 26 features are available at forecast time?
5. Which apparent precursors accidentally include future information?

## Boundaries

- Predictive contribution is not physical causality.
- A feature selected after seeing the held-out cycle is leaked.
- A proxy relationship must carry its calibration and phase dependence.
