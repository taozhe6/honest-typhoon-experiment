from dataclasses import fields
import json
from pathlib import Path
import unittest

from typhoon_markov.model import (
    FREE_PARAMETER_NAMES,
    OBSERVATION_CHANNELS,
    Forcing,
    PhysicalConstants,
    State,
)


ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(
            (ROOT / "config" / "model_v0.json").read_text(encoding="utf-8")
        )

    def test_research_status_blocks_authoritative_forecast(self):
        self.assertEqual(self.config["status"], "research-rejected")
        self.assertFalse(self.config["authoritative_forecast_enabled"])
        self.assertEqual(
            self.config["structural_rejection"]["test_id"],
            "bavi-2026-regime-wind-influence",
        )

    def test_parameter_and_forcing_schema_match_code(self):
        self.assertEqual(tuple(self.config["free_parameters"]), FREE_PARAMETER_NAMES)
        self.assertEqual(
            tuple(field.name for field in fields(State)),
            tuple(self.config["state_vector"]),
        )
        self.assertEqual(
            tuple(field.name for field in fields(Forcing)),
            tuple(self.config["required_forcing"]),
        )
        self.assertEqual(
            self.config["parameter_budget"]["scored_observation_field_count"],
            len(OBSERVATION_CHANNELS),
        )
        self.assertEqual(
            tuple(self.config["scored_observables"]),
            OBSERVATION_CHANNELS,
        )

    def test_fixed_constant_values_match_code(self):
        constants = PhysicalConstants.literature_western_north_pacific()
        fixed = self.config["fixed_constants"]
        self.assertEqual(
            constants.exchange_coefficient,
            fixed["surface_exchange_coefficient"]["value"],
        )
        self.assertEqual(
            constants.boundary_layer_depth_m,
            fixed["western_north_pacific_boundary_layer_depth_m"]["value"],
        )
        self.assertEqual(
            constants.thermodynamic_efficiency,
            fixed["thermodynamic_efficiency"]["value"],
        )
        self.assertEqual(constants.kappa, fixed["fast_kappa"]["value"])


if __name__ == "__main__":
    unittest.main()
