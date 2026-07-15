# Western North Pacific path-track benchmark

Status: `round-v3-complete-learning-reproduction-unvalidated`.

The project goal and permanent rules are recorded verbatim in the
[project document](../README.md).

Round v1 reproduces two operational dynamical-model tracks from archived ATCF a-decks
and verifies both against post-season best-track positions. The original frozen contract
is in [`preregistration.md`](preregistration.md); the user-directed, pre-result round-v1
scope is registered in [`deviations.md`](deviations.md).

Round v1 remains in [`report.md`](report.md). The mechanically selected 26-storm round v2,
including the fixed spherical consensus, error correlation, `n_eff`, and storm-held-out
uncertainty, is in [`report_round_v2.md`](report_round_v2.md). Machine-readable round-v2
artifacts are under [`outputs/round_v2/`](outputs/round_v2/).

Round v3 audits the apparent `DYC2` source, proves that the local file contains an
equal-weight spherical CMC/NGX consensus with no official ATCF `DYC2` TECH rows, and
compares that local consensus with the independently developed UK Met Office model.
The source audit is in [`dyc2_source_audit.md`](dyc2_source_audit.md); the frozen design,
results, and machine-readable artifacts are in [`preregistration_round_v3.md`](preregistration_round_v3.md),
[`report_round_v3.md`](report_round_v3.md), and [`outputs/round_v3/`](outputs/round_v3/).

The selection rule was frozen in commit `054f225`, and the resulting eligibility manifest
was frozen in commit `d30ff7e` before expanded-sample errors were read. This learning
exercise fits zero path-prediction parameters and claims no operational forecast advantage.
