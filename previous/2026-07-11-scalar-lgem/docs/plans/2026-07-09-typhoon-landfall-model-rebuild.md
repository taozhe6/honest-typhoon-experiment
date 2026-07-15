# Typhoon Landfall Model Rebuild Implementation Plan

> **Status: superseded on 2026-07-10.** This file records the discarded relaxation-ODE design. The implemented scientific contract is in [Typhoon Model Scientific Repair Design](2026-07-10-typhoon-model-scientific-repair-design.md), and the current runbook is in [Typhoon Landfall Model](../typhoon-landfall-model.md). Do not use this plan as current behavior.

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the endpoint-fitted landfall script with an auditable, track-conditioned reduced-order physical scenario model that preserves institutional disagreement.

**Architecture:** Source adapters dynamically discover the active storm and normalize all fixes to UTC. A validation layer aligns every agency to one effective time, checks identity, position, and intensity plausibility, and keeps disagreement visible. A spherical time-parametric track solver identifies sea-to-land entry into the configured target country. A Dormand-Prince RK45 model uses the common analyzed initial state, each agency track, GFS environmental fields, and HYCOM temperature profiles; official forecast winds remain in a separate guidance product.

**Tech Stack:** Python 3 standard library, public JSON/text feeds, Open-Meteo `gfs_global` hourly fields, HYCOM NCSS CSV profiles, Natural Earth GeoJSON, `unittest`.

---

### Task 1: Establish the configuration and normalized data contract

**Files:**
- Create: `config/typhoon_landfall_model.json`
- Create: `scripts/typhoon_landfall_core.py`
- Modify: `scripts/typhoon_bavi_landfall_model.py`
- Test: `tests/test_typhoon_landfall_model.py`

**Step 1: Write failing tests**

Add fixtures for a `StormIdentity`, a normalized `TrackPoint`, and configuration loading. Verify that storm identifiers, quality thresholds, and physical constants are loaded from JSON rather than embedded in the executable path.

**Step 2: Run the focused test**

Run: `python3 -m unittest tests.test_typhoon_landfall_model.ConfigTests -v`

Expected: failure before the core module exists.

**Step 3: Implement the minimal data contract**

Create typed dataclasses for storm identity, forecast point, source track, and source status. Centralize non-storm-specific URLs, target area, solver tolerances, quality limits, conversion assumptions, and literature provenance in the JSON configuration.

**Step 4: Run the focused test**

Run: `python3 -m unittest tests.test_typhoon_landfall_model.ConfigTests -v`

Expected: pass.

### Task 2: Replace fixed storm IDs with dynamic discovery

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Test: `tests/test_typhoon_landfall_model.py`

**Step 1: Write failing tests**

Use saved NMC, JMA, JTWC, HKO, and CWA response snippets. Verify that BAVI/2609 resolves to the dynamic NMC ID, JMA `TC2611`, JTWC `09W`, HKO `2614`, and CWA’s active name without an embedded storm-specific URL.

**Step 2: Implement source adapters**

Implement dynamic catalog parsing:

- NMC `list_default` for active storm ID and name;
- JMA `targetTc.json` for its current cyclone identifier;
- JTWC `abpwweb.txt` for active warning number and storm name;
- HKO `tc_gis_list.js` for its current GIS ID;
- CWA `TY_NEWS-Data.js` for active names and forecast content.

Each adapter must validate the selected name/number against the canonical identity and include the resolved endpoint in output provenance.

**Step 3: Run the focused test**

Run: `python3 -m unittest tests.test_typhoon_landfall_model.DiscoveryTests -v`

Expected: pass.

### Task 3: Add same-time quality control and temporal track geometry

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Test: `tests/test_typhoon_landfall_model.py`

**Step 1: Write failing tests**

Construct tracks issued three hours apart. Verify that source positions and winds are compared at the latest common effective analysis time. Add malformed winds of `5` and `500 m/s` to verify physical plausibility flags and rejection behavior.

**Step 2: Implement quality control**

Align tracks through UTC interpolation, select a medoid for diagnostic comparison, calculate robust spread statistics, and keep valid divergent agencies in the result. Identity mismatches, invalid timestamps, and impossible physical values exclude a source. Position and intensity divergence produce explicit warnings and a decision-risk classification.

**Step 3: Implement time-parametric geometry**

Use normalized Cartesian cubic Hermite interpolation with a spherical fallback. Fetch the configured Natural Earth target-country polygons and locate the first outside-to-inside crossing with time-domain subdivision and bisection. The solver reports the curve method, time tolerance, and landfall transition.

**Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_typhoon_landfall_model.AlignmentTests tests.test_typhoon_landfall_model.GeometryTests -v`

Expected: pass.

### Task 4: Implement the environmental and intensity model

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Test: `tests/test_typhoon_landfall_model.py`

**Step 1: Write failing tests**

Test the potential-intensity thermodynamics, HYCOM profile OHC integration, Kaplan-DeMaria land-decay term, and RK45 integration against analytic constant-tendency cases.

**Step 2: Implement environmental forcing**

Query hourly `gfs_global` temperature, humidity, pressure, outflow temperature, and 850–200 hPa winds along each track. Query HYCOM vertical water-temperature profiles at the same time and location; derive SST and OHC by integration above the 26 C isotherm. Record field timestamps and model identifiers. A stale or absent field produces a failed physical scenario with a visible reason.

**Step 3: Implement the ODE45 scenario model**

Use a Dormand-Prince 5(4) integrator. The ocean branch relaxes toward Emanuel-style thermodynamic PI, with the warm-ocean energy reservoir derived from the live OHC profile. The land branch uses the Kaplan-DeMaria exponential decay coefficient and background wind from configuration. It never fits a rate to future agency wind endpoints.

**Step 4: Run focused tests**

Run: `python3 -m unittest tests.test_typhoon_landfall_model.PhysicsTests -v`

Expected: pass.

### Task 5: Rebuild output and verify end to end

**Files:**
- Modify: `scripts/typhoon_landfall_core.py`
- Modify: `scripts/typhoon_bavi_landfall_model.py`
- Test: `tests/test_typhoon_landfall_model.py`

**Step 1: Replace the output contract**

Emit two separate products: `official_guidance` contains each agency’s time-interpolated guidance; `mechanistic_scenarios` contains combinations of valid initial analyses and valid tracks. Emit ranges, quantiles, grade spans, source-by-source tables, and divergence flags. Do not emit `preferred_column`, `mean_grade`, legacy interpolated keys, or duplicate aliases.

**Step 2: Run all tests and compile checks**

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/typhoon_landfall_core.py scripts/typhoon_bavi_landfall_model.py
```

Expected: all pass.

**Step 3: Run a live smoke test**

Run:

```bash
python3 scripts/typhoon_bavi_landfall_model.py --storm BAVI
```

Expected: dynamic IDs appear in provenance, source alignment occurs at one UTC time, institutional intensity spread is shown directly, and the physical output lists scenarios rather than a single mean.

**Step 4: Inspect the generated JSON**

Verify that every environmental input includes source time, every source includes identity evidence, and every estimate has exactly one value per named concept.
