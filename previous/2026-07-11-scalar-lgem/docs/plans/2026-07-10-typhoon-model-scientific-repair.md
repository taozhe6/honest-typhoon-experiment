# Typhoon Model Scientific Repair Implementation Plan

> **Status: implemented and verified on 2026-07-11.** All P0/P1/P2 code and regression items are complete. The WNP calibration gate remains intentionally closed until five qualifying archived forecast-cycle cases exist.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Repair every P0/P1/P2 finding in the independent audit and publish reproducible before/after numerical evidence.

**Architecture:** Keep source adapters and official guidance in the existing core module, introduce strict typed validation at adapter boundaries, replace bulk PI with a full-column PI adapter, split global physical land from the China reporting target, and gate mechanistic output on a WNP hindcast calibration artifact. Tests use frozen fixtures; the live run is a separate smoke test.

**Tech Stack:** Python 3.12+ standard library, NumPy, optional `tcpyPI==1.4`, public official feeds, Open-Meteo GFS vertical profiles, HYCOM profiles, Natural Earth GeoJSON, `unittest`.

---

### Task 1: Freeze the audit counterexamples

**Files:**
- Modify: `tests/test_typhoon_landfall_model.py`
- Modify: `scripts/typhoon_landfall_core.py`

**Step 1: Add failing P0 fixtures**

Add isolated CWA single-storm and multi-storm payloads, an invalid PI column with SST below near-surface air temperature, and an over-land low-wind case.

**Step 2: Record baseline evidence**

Run a small evidence harness against the current functions and retain the observed values in test constants: CWA current overwrite, zero PI, and `12.681 -> 13.123 m/s` over-land strengthening.

**Step 3: Run focused tests**

Run: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_typhoon_landfall_model.P0AuditRegressionTests -v`

Expected: failures that reproduce all P0 defects.

### Task 2: Repair P0 parsing and physical invariants

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Modify: `config/typhoon_landfall_model.json`
- Modify: `tests/test_typhoon_landfall_model.py`

**Step 1: Isolate CWA storms**

Select a single storm object by number/name before parsing; preserve analysis and forecast points; validate the full track.

**Step 2: Add full-column PI and fail-closed behavior**

Define the PI profile contract and adapter. Reject incomplete, non-monotonic, or thermodynamically invalid columns. Keep diagnostics available with explicit failure reasons.

**Step 3: Replace the relaxation/OHC-reserve path**

Remove it from mechanistic output. Introduce a calibrated-LGEM interface with required PI, shear, convective-instability, and calibration metadata. Emit `uncalibrated_research` when the artifact is absent.

**Step 4: Enforce land monotonicity**

Use Kaplan-DeMaria with `R=0.9`, apply it at every global land entry, and assert `V(t+dt) <= V(t)` for land segments.

**Step 5: Verify P0 evidence**

Run the P0 test class and compare every before/after value with its invariant.

### Task 3: Repair P1 forcings, units, discovery, and masks

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Modify: `config/typhoon_landfall_model.json`
- Modify: `tests/test_typhoon_landfall_model.py`

**Step 1: Add failing P1 tests**

Cover shear sensitivity, exact OHC threshold integration, native averaging periods, JTWC factor `0.93`, KD `R`, independent NMC outage, global land transitions, SST spatial QC, and zero warm-layer behavior.

**Step 2: Implement exact OHC integration**

Interpolate the 26 C crossing once and integrate each positive trapezoid without double counting.

**Step 3: Preserve unit provenance**

Carry native wind, native averaging period, conversion factor, and comparison wind as separate fields. Label CMA/NMC as 2-minute and JTWC as 1-minute.

**Step 4: Decouple discovery and land masks**

Fetch adapters independently and collect failures. Load global land geometry for physics and China geometry for reporting.

**Step 5: Wire shear and environmental QC**

Require shear in LGEM growth calculations, validate profile neighborhoods, and reject isolated discontinuities.

**Step 6: Verify P1 evidence**

Run focused tests and save before/after values for every repaired defect.

### Task 4: Repair P2 geometry and output semantics

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Modify: `config/typhoon_landfall_model.json`
- Modify: `tests/test_typhoon_landfall_model.py`

**Step 1: Add failing P2 tests**

Cover a narrow island crossed inside one 10-minute interval, matrix grouping, absent quantiles/preferred values, decision-facing rounding, and schema uniqueness.

**Step 2: Implement segment/polygon crossing**

Find all boundary intersections on each temporal track segment and classify transitions around each candidate; map the earliest sea-to-land fraction to time.

**Step 3: Rebuild the output contract**

Publish official branches, named research matrix members, min/max envelope, calibration state, repair evidence, and rounded decision fields. Remove pseudo-probability quantiles and duplicate aliases.

**Step 4: Verify P2 evidence**

Run the focused geometry and output tests.

### Task 5: Add the WNP hindcast calibration gate

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Modify: `config/typhoon_landfall_model.json`
- Modify: `tests/test_typhoon_landfall_model.py`

**Step 1: Define archived-case schema**

Require storm ID, forecast issue time, predictors available at issue time, target landfall observation, data provenance, and averaging period.

**Step 2: Implement leave-one-storm-out evaluation**

Require at least five storms and report MAE, bias, wind-grade error, and landfall-time error. Reject cases containing post-issue information.

**Step 3: Gate calibration artifacts**

Accept an artifact only when dataset hash, coefficient set, fold metrics, units, and model version validate.

**Step 4: Test insufficient and valid synthetic archives**

Verify that fewer than five storms remain research-only and that a valid artifact activates the LGEM pathway.

### Task 6: End-to-end verification and five-file delivery

**Files:**
- Modify: `outputs/typhoon_landfall/bavi_latest.json`
- Copy: five requested files to `/Users/taozhe/Downloads/台风模型_五文件_2026-07-10/`

**Step 1: Run full tests and compile checks**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/typhoon_landfall_core.py scripts/typhoon_bavi_landfall_model.py
```

**Step 2: Run the live model**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/typhoon_bavi_landfall_model.py --storm BAVI
```

Verify independent source status, native units, target/global land separation, environmental failure reasons, calibration gate, and official branch spread.

**Step 3: Generate repair evidence**

Store deterministic before/after values under `repair_evidence` in the latest JSON and verify every item has a cited invariant.

**Step 4: Synchronize the five delivery files**

Copy core, entry point, config, tests, and latest JSON to the existing Downloads directory. Compare SHA-256 hashes with workspace originals.

**Step 5: Report residual scientific limits**

State the live source failures, missing archived WNP calibration data, and any unavailable vertical profile fields directly.
