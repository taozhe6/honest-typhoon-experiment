from __future__ import annotations

import json
from pathlib import Path
import unittest

from typhoon_markov.model import PhysicalConstants
from typhoon_markov.theta_propagation import (
    constants_for_theta_multiplier,
    linear_grid,
    run_theta_grid,
)


ROOT = Path(__file__).resolve().parents[1]


class ThetaPropagationTests(unittest.TestCase):
    def test_linear_grid_contains_registered_endpoints_and_baseline(self) -> None:
        grid = linear_grid(0.7, 1.3, 61)
        self.assertEqual(len(grid), 61)
        self.assertAlmostEqual(grid[0], 0.7)
        self.assertAlmostEqual(grid[30], 1.0)
        self.assertAlmostEqual(grid[-1], 1.3)

    def test_two_parameterizations_produce_the_same_theta(self) -> None:
        baseline = PhysicalConstants.literature_western_north_pacific()
        for multiplier in (0.7, 1.3):
            by_ck = constants_for_theta_multiplier(
                baseline, multiplier, parameterization="exchange_coefficient"
            )
            by_h = constants_for_theta_multiplier(
                baseline, multiplier, parameterization="boundary_layer_depth"
            )
            self.assertAlmostEqual(
                by_ck.exchange_coefficient / by_ck.boundary_layer_depth_m,
                by_h.exchange_coefficient / by_h.boundary_layer_depth_m,
            )

    def test_reduced_grid_propagates_to_final_state_and_passes_invariance(self) -> None:
        theta = json.loads(
            (ROOT / "config" / "theta_propagation.json").read_text(encoding="utf-8")
        )
        scenarios = json.loads(
            (ROOT / "config" / "global_sensitivity.json").read_text(encoding="utf-8")
        )
        theta["theta_multiplier_grid"] = {
            "minimum": 0.7,
            "maximum": 1.3,
            "points": 3,
            "spacing": "linear",
        }
        theta["scenario_ids"] = ["open_ocean_intensifying"]
        result = run_theta_grid(theta, scenarios)
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(len(result["scenario_summaries"]), 1)
        self.assertTrue(
            result["structural_checks"]["equivalent_parameterizations"]["passed"]
        )
        self.assertGreater(
            result["cross_scenario"][
                "maximum_baseline_centered_absolute_delta_ms"
            ],
            0.0,
        )

    def test_rejects_inconsistent_comparison_hour(self) -> None:
        theta = json.loads(
            (ROOT / "config" / "theta_propagation.json").read_text(encoding="utf-8")
        )
        scenarios = json.loads(
            (ROOT / "config" / "global_sensitivity.json").read_text(encoding="utf-8")
        )
        theta["comparison_hour"] = 42
        with self.assertRaisesRegex(RuntimeError, "comparison hours do not match"):
            run_theta_grid(theta, scenarios)


if __name__ == "__main__":
    unittest.main()
