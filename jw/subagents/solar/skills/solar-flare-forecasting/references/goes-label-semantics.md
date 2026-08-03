# GOES flare label semantics

Use a named NOAA/NCEI GOES XRS product and bind its documentation, retrieval
time, file identity, and quality metadata. Prefer science-quality flare-summary
or composite flare-report products for retrospective labels. Do not mix a
real-time detection stream with a retrospective summary without an explicit
sensitivity analysis.

## Required distinctions

- GOES class is based on soft X-ray irradiance in the 0.1–0.8 nm channel.
- Flare class, peak irradiance, duration, fluence, and total released energy are
  different quantities.
- Event start, peak, end, and post-event timestamps have algorithm-specific
  definitions.
- Successive or overlapping flares can share an elevated background.
- Saturation, impaired states, satellite changes, and source-location
  uncertainty must remain visible.

## Label construction

For each forecast instance:

1. Use only the bound prediction window.
2. Apply an explicit threshold such as `M1.0+`.
3. Define boundary inclusion, normally `(start, end]`, and keep it unchanged.
4. For active-region labels, document NOAA/HARP association and treatment of
   unassigned, multi-region, behind-limb, and limb events.
5. Preserve the strongest event and event count as audit fields even when the
   primary label is binary.

## Missingness policy

`observed_no_event` is a scientific zero. `missing`, `impaired`,
`outside_coverage`, and `not_yet_available` are not zeros. Exclude, censor, or
model them according to the bound protocol and report counts by state.

## Version boundary

Legacy fixed-width event reports and GOES-R Level-2 products differ in format,
algorithms, calibration, metadata, and location capability. Never assume one
parser or one event definition is homogeneous across the whole GOES era.
