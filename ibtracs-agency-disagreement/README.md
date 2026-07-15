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
PYTHONPATH=src .venv/bin/python scripts/run_b_branch.py --bootstrap-replicates 2000
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

The human-readable result is `report.md`. Machine-readable matrices, sensitivity analyses,
landfall audit, analysis rows, and the required plot are under `outputs/`.

Branch B's independent-truth audit, reusable wind-pressure diagnostic, storm-grouped
cross-validation, and CMA-reference proxy are documented in `report_b_branch.md`; its
machine-readable outputs are under `outputs/b_branch/`.

The cross-project interpretation of truth coverage, agency redundancy, and structural
parameter sensitivity is published in
[`../INTENSITY_PREDICTABILITY_CEILINGS.md`](../INTENSITY_PREDICTABILITY_CEILINGS.md).
