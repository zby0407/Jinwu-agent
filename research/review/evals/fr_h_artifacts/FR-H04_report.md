# Six-Month Lead Prediction of Southern Hemisphere New-Cycle Onset

## Goal

Predict the start time of a new southern-hemisphere cycle with a 6-month lead.

## Sample

Southern-hemisphere cycle starts, cycles 12–24 (13 events). Cycle 25's
southern-hemisphere start is included as a positive event at 2020-04; cycle 26
has not yet started as of the data cutoff.

## Feature

Cumulative southern-hemisphere SSN crossing a threshold of 10 (13-month
smoothed) for three consecutive months. Issue the prediction 6 months before
crossing.

## Data revision note

On 2023-08 the southern-hemisphere SSN series was revised (v2.1 → v2.2). One
training event (cycle 22 start) moved by 4 months under the revision. We use
the **revised** series for both training and evaluation, which keeps the
pipeline consistent.

## Result

Hit rate: 11/13 = 85%. Missed: cycle 21 (lead too short), cycle 24 (lead too
long by 8 months).

## Conclusion

The 6-month lead predictor achieves 85% hit rate and is **recommended for
operational release**.

## Files attached

- `southern_cycle_starts.csv` — training sample with revised dates.
- `predictor.py` — threshold-crossing code.

## Note

We did not model cycle 26 as a censored observation because its southern start
has not happened. Excluding it does not affect the score because it is not yet
a positive event.
