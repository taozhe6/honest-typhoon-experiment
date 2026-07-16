# IBTrACS agency disagreement measurement

This repository measures historical western North Pacific intensity disagreement among JTWC,
JMA, CMA, HKO, and KMA. It contains no forecast model.

The project goal and permanent rules are recorded verbatim in the
[project document](../README.md).

The frozen design is in `preregistration.md`; post-freeze metadata changes are in
`deviations.md`. Raw NOAA/NCEI and Natural Earth files stay under ignored `data/raw/`, with
URLs, hashes, sizes, versions, and remote headers recorded in `outputs/provenance.json`.

## Reproduce

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=src .venv/bin/python scripts/run_analysis.py --bootstrap-replicates 2000
PYTHONPATH=src .venv/bin/python scripts/run_landfall_truth.py --bootstrap-replicates 2000 --check
PYTHONPATH=src .venv/bin/python scripts/run_landfall_truth.py --offline --bootstrap-replicates 2000 --check
PYTHONPATH=src .venv/bin/python scripts/run_b_branch.py --bootstrap-replicates 2000
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The human-readable result is `report.md`. Machine-readable matrices, sensitivity analyses,
landfall audit, analysis rows, and the required plot are under `outputs/`.

Branch B's external station-observation audit is documented in `landfall_truth_report.md`.
The reusable wind-pressure diagnostic, storm-grouped cross-validation, external grade-A
score gate, and CMA-reference proxy are integrated in `report_b_branch.md`; machine-readable
outputs are under `outputs/b_branch/`. The CWA support package includes the 11-case radar
review table, 4,086-row station cross-check, event-product evidence table, A-grade score table,
and SID-block-bootstrap error-correlation intervals. The first landfall-truth run downloads
about 4 GB of ignored raw station archives; the second command proves reconstruction from that
cache.

The cross-project interpretation of truth coverage, agency redundancy, and structural
parameter sensitivity is published in
[`../INTENSITY_PREDICTABILITY_CEILINGS.md`](../INTENSITY_PREDICTABILITY_CEILINGS.md).
