from __future__ import annotations

import json
from pathlib import Path
import unittest

from typhoon_markov.model import (
    Forcing,
    MarkovParameters,
    PhysicalConstants,
    Regime,
    State,
    fast_tendencies,
    transition_probabilities,
)
from typhoon_markov.sensitivity import (
    constants_with_multipliers,
    fixed_regime_trajectory,
    markov_trajectory,
    parse_scenario,
    run_sensitivity,
)


class SensitivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.constants = PhysicalConstants.literature_western_north_pacific()
        self.state = State(35.0, 0.78, 97000.0, 40000.0)
        self.forcing = Forcing(72.0, 7.0, 0.45, 55.0, 1.0, 5.5, 1.0, 0.0, 20.0)
        self.parameters = MarkovParameters(1.2, 0.6, "test", False)

    def test_unknown_constant_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            constants_with_multipliers(self.constants, {"tau": 1.3})

    def test_joint_Ck_depth_scaling_is_exactly_invariant(self) -> None:
        perturbed = constants_with_multipliers(
            self.constants,
            {"exchange_coefficient": 1.3, "boundary_layer_depth_m": 1.3},
        )
        left = fast_tendencies(self.state, self.forcing, self.constants)
        right = fast_tendencies(self.state, self.forcing, perturbed)
        self.assertAlmostEqual(left.wind_tendency_ms2, right.wind_tendency_ms2, places=16)
        self.assertAlmostEqual(left.moisture_tendency_s, right.moisture_tendency_s, places=16)
        base_probability = transition_probabilities(
            Regime.QUASI_STEADY,
            self.state,
            self.forcing,
            self.parameters,
            self.constants,
        )
        changed_probability = transition_probabilities(
            Regime.QUASI_STEADY,
            self.state,
            self.forcing,
            self.parameters,
            perturbed,
        )
        self.assertEqual(base_probability, changed_probability)

    def test_regime_schedule_cannot_change_wind_path(self) -> None:
        forcings = [self.forcing] * 3
        fixed = fixed_regime_trajectory(
            self.state,
            forcings,
            [Regime.WEAKENING, Regime.INTENSIFYING, Regime.QUASI_STEADY],
            self.constants,
            duration_hours=6.0,
        )
        markov = markov_trajectory(
            self.state,
            Regime.QUASI_STEADY,
            forcings,
            self.parameters,
            self.constants,
            seed=123,
            duration_hours=6.0,
        )
        for left, right in zip(fixed, markov):
            self.assertAlmostEqual(left["state"]["wind_ms"], right["state"]["wind_ms"])

    def test_preregistered_config_has_three_scenarios_and_eight_variants(self) -> None:
        path = Path(__file__).resolve().parents[1] / "config" / "global_sensitivity.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(config["scenarios"]), 3)
        self.assertEqual(len(config["perturbations"]), 8)
        for scenario in config["scenarios"]:
            _, _, forcings, regimes = parse_scenario(scenario)
            self.assertEqual(len(forcings), 8)
            self.assertEqual(len(regimes), 8)

    def test_preregistered_audit_reproduces_structural_checks(self) -> None:
        path = Path(__file__).resolve().parents[1] / "config" / "global_sensitivity.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        results = run_sensitivity(config)
        ratio_check = results["structural_checks"]["Ck_over_h_ratio_invariance"]
        regime_check = results["structural_checks"][
            "regime_has_zero_wind_path_effect"
        ]
        self.assertTrue(ratio_check["passed"])
        self.assertLessEqual(
            ratio_check["maximum_native_state_delta"],
            config["ratio_invariance_absolute_tolerance"],
        )
        self.assertTrue(regime_check["passed"])
        self.assertEqual(regime_check["maximum_fixed_vs_markov_wind_delta_ms"], 0.0)
        self.assertEqual(len(results["summary"]), 16)


if __name__ == "__main__":
    unittest.main()
