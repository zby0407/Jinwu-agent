# Baselines and metrics

Evaluate the probability product before optimizing a thresholded yes/no view.

## Required baselines

- **Climatology**: training-period event rate under the same target definition.
- **Persistence**: recent flare occurrence or the previous issued probability,
  computed only from information available at issue time.
- **Simple interpretable baseline**: McIntosh/region class, a small regularized
  logistic model, or another predeclared low-complexity model when its inputs
  exist.

Never calculate a test-set climatology and use it as the reference forecast.

## Probability verification

Report:

- Brier score and Brier skill score against a declared reference;
- log loss when probabilities are strictly bounded away from zero and one for
  numerical evaluation;
- reliability bins with forecast mean, observed frequency, and sample count;
- resolution or discrimination, using PR-AUC when rare-event ranking matters;
- uncertainty intervals using time- or region-blocked resampling.

Calibration must use a held-out training-era calibration set or nested
cross-validation. Do not calibrate on final test outcomes.

## Thresholded verification

At thresholds fixed without the final test set, report the contingency table
and at least:

- probability of detection (POD/recall);
- false alarm ratio (FAR);
- critical success index (CSI);
- true skill statistic (TSS);
- Heidke skill score (HSS), with its reference definition;
- precision when relevant to users.

Accuracy alone is invalid for qualification because non-events dominate,
especially for X-class targets.

## Slices

Where sample size permits, verify by target threshold, cycle phase, central
meridian distance, active-region/full-disk mode, instrument/product version,
and activity regime. Mark small slices as descriptive rather than conclusive.

## Qualification rule

No single metric qualifies a model. Require:

- improvement over declared simple baselines on frozen data;
- usable calibration at decision-relevant probability ranges;
- no catastrophic miss or false-alarm behavior hidden by an aggregate score;
- stable direction under predeclared sensitivity checks;
- an explicit valid range and failure state.
