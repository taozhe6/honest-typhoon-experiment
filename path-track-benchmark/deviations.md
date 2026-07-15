# Registered deviations

## D001: user-directed learning subset and removal of DYC2

- Date: 2026-07-15.
- Timing: registered before any forecast position was paired with best track and before any path error was calculated.
- Trigger: the user's latest execution instruction requires 3--5 historical typhoons, at least two operational models, and explicitly forbids constructing a path predictor in this round.
- Replaces: preregistration sections 1.2, 1.4, 2.1 `DYC2`, 6, 8, and the full-population sample in section 4 for this round only.

Frozen round-v1 design:

1. Storms: Hinnamnor (`WP122022`), Doksuri (`WP052023`), Gaemi (`WP052024`), and Yagi (`WP122024`).
2. Operational model aids: Canadian model `CMC` and NAVGEM/NOGAPS with GFS tracker `NGX`.
3. Leads: 24, 48, 72, 96, and 120 hours.
4. Verification: exact-valid-time `USA_LAT/USA_LON` post-season best-track positions from IBTrACS.
5. Primary sample: strict paired intersection of both model aids and best track.
6. Metrics: WGS84 track error by model and lead, record count, storm count, mean, median, and storm-block bootstrap 95% interval.
7. Deliverables: one error-versus-lead plot, machine-readable rows and summary, and one learning-reproduction conclusion.
8. Qualification label: `learning-reproduction`; no claim of model superiority or validated operational skill.

The equal-weight `DYC2` consensus is not run, fitted, scored, or plotted in round v1.

