import unittest

from typhoon_markov.audit import (
    HindcastMetrics,
    audit_identifiability,
    evaluate_falsification,
)


class AuditTests(unittest.TestCase):
    def test_identifiability_gate_passes_full_rank_disjoint_design(self):
        report = audit_identifiability(
            [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            effective_observation_dimension=2.5,
            observation_error_covariance_registered=True,
            scalar_observation_count=1_000,
            training_storm_ids=(f"train-{index}" for index in range(40)),
            holdout_storm_ids=(f"test-{index}" for index in range(12)),
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.parameter_count, 2)
        self.assertEqual(report.observation_channel_count, 3)

    def test_identifiability_gate_rejects_rank_deficiency(self):
        report = audit_identifiability(
            [[1.0, 2.0], [2.0, 4.0]],
            effective_observation_dimension=2.5,
            observation_error_covariance_registered=True,
            scalar_observation_count=1_000,
            training_storm_ids=(f"train-{index}" for index in range(40)),
            holdout_storm_ids=(f"test-{index}" for index in range(12)),
        )
        self.assertFalse(report.passed)
        self.assertFalse(
            report.checks["full_whitened_parameter_sensitivity_rank"]
        )

    def test_identifiability_gate_rejects_storm_leakage(self):
        training = [f"storm-{index}" for index in range(40)]
        holdout = ["storm-0"] + [f"test-{index}" for index in range(11)]
        report = audit_identifiability(
            [[1.0, 0.0], [0.0, 1.0]],
            effective_observation_dimension=2.5,
            observation_error_covariance_registered=True,
            scalar_observation_count=1_000,
            training_storm_ids=training,
            holdout_storm_ids=holdout,
        )
        self.assertFalse(report.passed)
        self.assertFalse(report.checks["storm_disjoint_split"])

    def test_effective_dimension_is_a_non_gating_redundancy_diagnostic(self):
        report = audit_identifiability(
            [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            effective_observation_dimension=1.464133937313892,
            observation_error_covariance_registered=True,
            scalar_observation_count=1_000,
            training_storm_ids=(f"train-{index}" for index in range(40)),
            holdout_storm_ids=(f"test-{index}" for index in range(12)),
        )
        self.assertTrue(report.passed)
        self.assertTrue(
            report.diagnostics["effective_dimension_below_parameter_count"]
        )

    def test_identifiability_gate_requires_observation_error_covariance(self):
        report = audit_identifiability(
            [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            effective_observation_dimension=2.5,
            observation_error_covariance_registered=False,
            scalar_observation_count=1_000,
            training_storm_ids=(f"train-{index}" for index in range(40)),
            holdout_storm_ids=(f"test-{index}" for index in range(12)),
        )
        self.assertFalse(report.passed)
        self.assertFalse(
            report.checks["observation_error_covariance_registered"]
        )

    def test_falsification_gate_passes_only_when_all_targets_hold(self):
        baseline = HindcastMetrics(10.0, 15.0, 30.0, 12.0, 0.20, 0.80, 0)
        model = HindcastMetrics(9.0, 14.0, 29.0, 11.0, 0.15, 0.80, 0)
        report = evaluate_falsification(model, baseline, climatology_ri_brier_score=0.18)
        self.assertTrue(report.passed)

        failed = HindcastMetrics(9.0, 14.0, 29.0, 11.0, 0.19, 0.80, 0)
        failed_report = evaluate_falsification(
            failed, baseline, climatology_ri_brier_score=0.18
        )
        self.assertFalse(failed_report.passed)
        self.assertFalse(failed_report.checks["ri_probability_beats_climatology"])


if __name__ == "__main__":
    unittest.main()
