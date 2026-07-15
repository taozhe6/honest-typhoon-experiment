# Western North Pacific path-track benchmark

Status: `round-v2-complete-learning-reproduction-unvalidated`.

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

The selection rule was frozen in commit `8528a77`, and the resulting eligibility manifest
was frozen in commit `52199e3` before expanded-sample errors were read. This learning
exercise fits zero path-prediction parameters and claims no operational forecast advantage.
