# Discarded: the v1 confidence and severity system

Wildfire Sentinel v1 computed a 0–100 "confidence score" for every detection and
derived a four-level "severity" from it. Neither exists in v2. This note records
what they were and why they were removed, so the decision isn't quietly re-made
later.

## What v1 did

A score out of 100, summed from four hand-written threshold ladders:

| Component | Max points | Input |
|---|---|---|
| Thermal intensity | 35 | brightness in Kelvin |
| Fire radiative power | 30 | FRP in MW |
| NASA satellite confidence | 20 | the FIRMS `confidence` field |
| Spatial persistence | 15 | count of earlier detections near the same point |

Severity was then a re-binning of that same number: 0–30 low, 31–60 medium,
61–85 high, 86–100 critical.

## Why it was removed

1. **The weights had no derivation.** They were not fitted to anything, not
   validated against ground truth, and not taken from any published method. The
   README nonetheless called the result a "proprietary algorithm".
2. **Components 1 and 2 double-count.** Brightness temperature and FRP are both
   measures of thermal output and are physically correlated. Adding them as if
   they were independent evidence overweights thermal intensity.
3. **The score was not reproducible.** Spatial persistence counted rows already
   present in the database, so the same observation received a different score
   depending on ingestion order and timing. Re-running the pipeline changed the
   numbers.
4. **It looked like a probability and wasn't one.** A 0–100 output invites
   reading as "87% likely to be a real fire". It was calibrated against nothing.
5. **Severity was not severity.** It was a relabelling of the confidence score,
   so "critical" meant "we are confident this detection is real", not "this fire
   is large or dangerous". Those are different questions, and conflating them is
   actively misleading to anyone reading the dashboard.

## What v2 does instead

- Stores NASA's own confidence exactly as supplied, in the product's own
  semantics: `confidence_text` for VIIRS (low / nominal / high), `confidence_pct`
  for MODIS (0–100) once MODIS is added. A database constraint enforces that
  exactly one is populated. They are never converted into each other.
- Stores FRP, brightness, and the other measurements as received, with
  `brightness_channel` recording which FIRMS channel each brightness value came
  from, so VIIRS and MODIS brightness values are never silently compared.
- Derives no risk score at all. Analytics rank and filter on the source
  measurements themselves.

If a scored ranking is wanted later, the honest form is an explicitly documented
heuristic with stated inputs and a stated purpose — not a number presented as a
validated confidence.
