# Typhoon Model Scientific Repair Design

**Date:** 2026-07-10

**Scope:** Repair the current BAVI landfall model against the independent P0/P1/P2 audit while preserving official-agency disagreement and producing reproducible before/after evidence.

## Scientific Contract

The program emits three products with separate semantics:

1. `official_guidance` preserves each agency's native wind averaging period, native forecast points, and target-China landfall interpolation.
2. `environmental_diagnostics` contains full-column thermodynamic potential intensity, ocean heat content, vertical wind shear, and global land/ocean state along each path.
3. `mechanistic_scenarios` contains research calculations only when every required predictor and a compatible calibration artifact are present. Missing thermodynamic profiles or missing WNP calibration produces a fail-closed status with reasons.

No cross-agency mean, preferred grade, or pseudo-probabilistic quantile is published. The agency-by-agency branch range is the decision-facing uncertainty statement.

## P0 Decisions

### Full-column potential intensity

The bulk two-level approximation is retired. The PI adapter follows the `tcpyPI`/Bister-Emanuel input contract: SST, sea-level pressure, and pressure/temperature/mixing-ratio arrays through the atmospheric column. Invalid thermodynamic columns produce `valid=false` with a reason; zero PI never enters the intensity integrator as a physical equilibrium.

### CWA parser isolation

The parser selects one storm object by canonical identity before reading its current fix and forecast list. The current analysis and each forecast keep their own timestamp, position, wind, and point kind. Whole-track QC validates chronology, speed, wind range, duplicate times, and identity continuity.

### Ocean intensity dynamics

The relaxation equation and OHC reserve multiplier are removed from the decision product. A logistic-growth interface uses PI, shear, convective instability, and calibrated coefficients. Published Atlantic coefficients may appear only as an explicitly named diagnostic benchmark. Operational WNP output requires a calibration artifact produced by the hindcast pipeline.

### Land intensity dynamics

Every ocean-to-land transition on a global land mask starts a separate land segment. Kaplan-DeMaria decay uses `R=0.9`. The implementation enforces non-increasing intensity over land and treats values at or below the background threshold as dissipating states.

## P1 Decisions

- Shear is a required growth-rate predictor and therefore affects any enabled LGEM scenario.
- OHC integrates `rho * cp * max(T-26 C, 0)` with exact linear interpolation at the threshold crossing.
- NMC/CMA winds retain their 2-minute native period. JTWC 1-minute values use the documented at-sea `0.93` conversion only in an explicitly labeled 10-minute comparison field.
- Storm discovery uses independent catalogs. A failed NMC catalog leaves the remaining adapters available and records NMC as failed.
- Global Natural Earth polygons drive physical land exposure. The China polygon remains the reporting target.
- Environmental spatial QC compares neighboring samples and rejects isolated SST/profile discontinuities.
- OHC values below the threshold carry a zero warm-layer diagnostic and no artificial reserve fraction.

## P2 Decisions

- Track/polygon intersections are solved in space along each forecast segment, then mapped to time by the segment's temporal parameter. Narrow islands cannot disappear between fixed 10-minute endpoint samples.
- The scenario matrix remains grouped by initial analysis and path. It reports branch minimum/maximum and named members; sample quantiles carry no probability label and are omitted.
- Decision-facing winds round to 1 m/s, positions to 0.1 degree, and times to 30 minutes. Raw computational values remain in diagnostics.
- Tests include CWA single/multi-storm fixtures, PI invalid columns, shear sensitivity, exact OHC crossing, NMC outage, global land, narrow-island intersection, KD `R`, post-land monotonicity, units, and output-schema assertions.

## Calibration Gate

The hindcast framework accepts archived forecast-cycle predictors and best-track outcomes for at least five WNP landfalls, using only fields available 40 hours before each landfall. It performs leave-one-storm-out evaluation and records MAE, bias, grade error, and landfall-time error. Until such a dataset is supplied and passes configured thresholds, the model status remains `uncalibrated_research`; official guidance stays available.

## Evidence Format

Each repaired defect has a deterministic fixture with `before`, `after`, expected physical invariant, and pass/fail status. Live output adds a concise `repair_evidence` section without replacing fixture-based regression evidence.
