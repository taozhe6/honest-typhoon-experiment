# Typhoon Landfall Model

## Status

The model was scientifically repaired and verified on 2026-07-11. It publishes official agency branches and environmental diagnostics. Mechanistic landfall intensity remains `uncalibrated_research` until a Western North Pacific hindcast artifact passes the five-storm gate.

## Files

- `scripts/typhoon_landfall_core.py`: discovery, parsing, QC, geometry, environmental sampling, PI, LGEM calibration, and output.
- `scripts/typhoon_bavi_landfall_model.py`: command-line entry point.
- `config/typhoon_landfall_model.json`: source URLs, native wind periods, numerical thresholds, provenance, and calibration gate.
- `tests/test_typhoon_landfall_model.py`: deterministic audit regressions.
- `outputs/typhoon_landfall/bavi_latest.json`: latest verified live output.

The same five files are bundled in `/Users/taozhe/Downloads/台风模型_五文件_2026-07-10/`. The core and tests support both project layout and same-directory layout.

## Scientific Contract

`official_guidance` keeps each agency's native averaging period. NMC/CMA remains 2-minute, JTWC remains 1-minute, and JTWC has a separately labeled 10-minute comparison using WMO factor 0.93. A CMA wind grade appears only on a 2-minute value.

`environmental_diagnostics` uses GFS full-column temperature and humidity profiles, a two-ring approximation to the roughly 5-degree environment, HYCOM center-track SST/OHC, `tcpyPI 1.4`, and global Natural Earth land polygons. Invalid PI nodes carry a reason and supply no wind value.

`mechanistic_scenarios` requires a WNP calibration artifact with at least five storms, 40-hour issue-time data discipline, dataset hash, leave-one-storm-out metrics, and accepted MAE/bias. Missing calibration produces zero mechanistic scenarios. Official guidance remains available.

## Install

Python needs NumPy. Full PI additionally needs the verified pure-Python `tcpyPI 1.4` wheel; the core disables Numba acceleration automatically.

```bash
python3 -m pip install numpy
python3 -m pip install --no-deps tcpypi==1.4
```

The configured wheel SHA-256 is `8be34cb3f7ca6db1f98a94537ae1797dd85e94773d28cfdaecc6f97da91cad02`. Missing `tcpyPI` produces a fail-closed PI reason.

## Run

From the project root:

```bash
python3 scripts/typhoon_bavi_landfall_model.py --storm BAVI
```

From the five-file bundle:

```bash
cd '/Users/taozhe/Downloads/台风模型_五文件_2026-07-10'
python3 typhoon_bavi_landfall_model.py --storm BAVI
```

Official-source-only smoke test:

```bash
python3 typhoon_bavi_landfall_model.py --storm BAVI --skip-physics --output /tmp/bavi_official.json
```

## Verify

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/typhoon_landfall_core.py scripts/typhoon_bavi_landfall_model.py
```

From the five-file bundle:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest test_typhoon_landfall_model -v
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile typhoon_landfall_core.py typhoon_bavi_landfall_model.py
```

The 2026-07-11 verification ran 32 tests. The same 32 tests also passed from the five-file bundle. The live run resolved NMC, JMA, JTWC, HKO, and CWA independently and produced five available environmental paths.

## Interpret Output

- Use `official_guidance.by_source` for named branches.
- Use `comparison_10min_envelope` as a descriptive range with no probability meaning.
- Use `repair_evidence` for deterministic before/after counterexamples.
- Read every PI node's `valid` and `reason` fields before using its diagnostics.
- Treat `uncalibrated_research` as a closed scientific gate. It carries no operational landfall-intensity claim.
