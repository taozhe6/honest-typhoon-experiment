import inspect
import math
import random
import unittest

from typhoon_markov.model import (
    FREE_PARAMETER_NAMES,
    OBSERVATION_CHANNELS,
    Forcing,
    MarkovParameters,
    ModelDomainError,
    PhysicalConstants,
    Regime,
    State,
    fast_tendencies,
    initialize_core_moisture,
    observation_vector,
    step,
    transition_probabilities,
)


class MarkovModelTests(unittest.TestCase):
    def setUp(self):
        self.constants = PhysicalConstants.literature_western_north_pacific()
        self.parameters = MarkovParameters(
            persistence_logit=1.2,
            regime_width=0.6,
            calibration_id="synthetic-only-test",
            calibrated=False,
        )
        self.state = State(45.0, 0.75, 94_500.0, 25_000.0)
        self.forcing = Forcing(
            potential_intensity_ms=70.0,
            shear_ms=10.0,
            entropy_deficit=0.5,
            mixed_layer_depth_m=50.0,
            submixed_stratification_k_per_100m=1.0,
            translation_speed_ms=5.0,
            surface_exchange_multiplier=1.0,
            land_fraction=0.0,
            latitude_deg=20.0,
        )

    def test_state_and_parameter_budget_are_explicit(self):
        self.assertEqual(len(self.state.as_vector()), 4)
        self.assertEqual(FREE_PARAMETER_NAMES, ("persistence_logit", "regime_width"))
        self.assertEqual(OBSERVATION_CHANNELS, ("wind_ms", "central_pressure_pa", "rmw_m"))
        self.assertLess(len(FREE_PARAMETER_NAMES), len(OBSERVATION_CHANNELS))

    def test_transition_probabilities_form_distribution(self):
        probabilities = transition_probabilities(
            Regime.QUASI_STEADY,
            self.state,
            self.forcing,
            self.parameters,
            self.constants,
        )
        self.assertTrue(all(0.0 < probability < 1.0 for probability in probabilities))
        self.assertAlmostEqual(sum(probabilities), 1.0, places=12)

    def test_transition_api_has_no_history_argument(self):
        signature = inspect.signature(transition_probabilities)
        self.assertEqual(
            tuple(signature.parameters),
            ("current_regime", "state", "forcing", "parameters", "constants"),
        )

    def test_same_markov_state_and_seed_reproduce_same_step(self):
        first = step(
            self.state,
            Regime.QUASI_STEADY,
            self.forcing,
            self.parameters,
            self.constants,
            random.Random(73),
        )
        second = step(
            self.state,
            Regime.QUASI_STEADY,
            self.forcing,
            self.parameters,
            self.constants,
            random.Random(73),
        )
        self.assertEqual(first.regime, second.regime)
        self.assertEqual(first.transition_probabilities, second.transition_probabilities)
        self.assertEqual(first.state, second.state)

    def test_stronger_shear_reduces_six_hour_wind(self):
        low_shear = self.forcing
        high_shear = Forcing(**{**self.forcing.__dict__, "shear_ms": 35.0})
        high_persistence = MarkovParameters(20.0, 0.6, "synthetic-only-test", False)

        low = step(
            self.state,
            Regime.QUASI_STEADY,
            low_shear,
            high_persistence,
            self.constants,
            random.Random(2),
        )
        high = step(
            self.state,
            Regime.QUASI_STEADY,
            high_shear,
            high_persistence,
            self.constants,
            random.Random(2),
        )
        self.assertLess(high.state.wind_ms, low.state.wind_ms)

    def test_slow_translation_and_shallow_mixed_layer_reduce_wind(self):
        well_coupled = Forcing(
            **{
                **self.forcing.__dict__,
                "mixed_layer_depth_m": 80.0,
                "translation_speed_ms": 8.0,
            }
        )
        strong_cold_wake = Forcing(
            **{
                **self.forcing.__dict__,
                "mixed_layer_depth_m": 10.0,
                "translation_speed_ms": 1.0,
            }
        )
        high_persistence = MarkovParameters(20.0, 0.6, "synthetic-only-test", False)
        first = step(
            self.state,
            Regime.QUASI_STEADY,
            well_coupled,
            high_persistence,
            self.constants,
            random.Random(2),
        )
        second = step(
            self.state,
            Regime.QUASI_STEADY,
            strong_cold_wake,
            high_persistence,
            self.constants,
            random.Random(2),
        )
        self.assertLess(second.state.wind_ms, first.state.wind_ms)

    def test_open_ocean_zero_pi_is_a_hard_failure(self):
        invalid = Forcing(**{**self.forcing.__dict__, "potential_intensity_ms": 0.0})
        with self.assertRaises(ModelDomainError):
            invalid.validate()

    def test_moisture_initialization_recovers_known_state(self):
        tendency = fast_tendencies(self.state, self.forcing, self.constants).wind_tendency_ms2
        recovered = initialize_core_moisture(
            self.state.wind_ms, tendency, self.forcing, self.constants
        )
        self.assertAlmostEqual(recovered, self.state.core_moisture, places=12)

    def test_smaller_rmw_changes_pressure_more_quickly(self):
        parameters = MarkovParameters(20.0, 0.6, "synthetic-only-test", False)
        small = State(45.0, 0.75, 94_500.0, 15_000.0)
        large = State(45.0, 0.75, 94_500.0, 60_000.0)
        small_result = step(
            small,
            Regime.INTENSIFYING,
            self.forcing,
            parameters,
            self.constants,
            random.Random(1),
        )
        large_result = step(
            large,
            Regime.INTENSIFYING,
            self.forcing,
            parameters,
            self.constants,
            random.Random(1),
        )
        small_drop = small.central_pressure_pa - small_result.state.central_pressure_pa
        large_drop = large.central_pressure_pa - large_result.state.central_pressure_pa
        self.assertGreater(small_drop, large_drop)

    def test_pressure_equation_matches_short_interval_analytic_solution(self):
        resting = State(0.0, 0.0, 100_000.0, 30_000.0)
        neutral_ocean = Forcing(
            potential_intensity_ms=70.0,
            shear_ms=0.0,
            entropy_deficit=0.0,
            mixed_layer_depth_m=50.0,
            submixed_stratification_k_per_100m=1.0,
            translation_speed_ms=5.0,
            surface_exchange_multiplier=1.0,
            land_fraction=0.0,
            latitude_deg=20.0,
        )
        result = step(
            resting,
            Regime.INTENSIFYING,
            neutral_ocean,
            MarkovParameters(20.0, 0.6, "synthetic-only-test", False),
            self.constants,
            random.Random(1),
        )
        duration_s = 6.0 * 3_600.0
        expected = resting.central_pressure_pa * math.exp(
            -2.0
            * self.constants.intensifying_column_speed_m_s
            * duration_s
            / resting.rmw_m
        )
        self.assertAlmostEqual(result.state.central_pressure_pa, expected, places=6)
        self.assertAlmostEqual(result.state.rmw_m, resting.rmw_m, places=9)

    def test_intensifying_wind_contracts_rmw_under_aam_closure(self):
        moist_state = State(30.0, 1.0, 98_000.0, 45_000.0)
        favorable = Forcing(
            **{
                **self.forcing.__dict__,
                "potential_intensity_ms": 80.0,
                "shear_ms": 2.0,
                "entropy_deficit": 0.2,
            }
        )
        result = step(
            moist_state,
            Regime.INTENSIFYING,
            favorable,
            MarkovParameters(20.0, 0.6, "synthetic-only-test", False),
            self.constants,
            random.Random(1),
        )
        self.assertGreater(result.state.wind_ms, moist_state.wind_ms)
        self.assertLess(result.state.rmw_m, moist_state.rmw_m)

    def test_full_land_forcing_decays_wind(self):
        land = Forcing(
            potential_intensity_ms=0.0,
            shear_ms=10.0,
            entropy_deficit=0.5,
            mixed_layer_depth_m=0.0,
            submixed_stratification_k_per_100m=0.0,
            translation_speed_ms=5.0,
            surface_exchange_multiplier=3.0,
            land_fraction=1.0,
            latitude_deg=20.0,
        )
        result = step(
            self.state,
            Regime.WEAKENING,
            land,
            MarkovParameters(20.0, 0.6, "synthetic-only-test", False),
            self.constants,
            random.Random(1),
        )
        self.assertLess(result.state.wind_ms, self.state.wind_ms)

    def test_full_land_removes_intensifying_regime_memory(self):
        weak_state = State(10.0, 0.75, 100_000.0, 25_000.0)
        land = Forcing(
            potential_intensity_ms=0.0,
            shear_ms=10.0,
            entropy_deficit=0.5,
            mixed_layer_depth_m=0.0,
            submixed_stratification_k_per_100m=0.0,
            translation_speed_ms=5.0,
            surface_exchange_multiplier=3.0,
            land_fraction=1.0,
            latitude_deg=20.0,
        )
        parameters = MarkovParameters(20.0, 0.6, "synthetic-only-test", False)
        probabilities = transition_probabilities(
            Regime.INTENSIFYING,
            weak_state,
            land,
            parameters,
            self.constants,
        )
        self.assertEqual(probabilities[int(Regime.INTENSIFYING)], 0.0)
        result = step(
            weak_state,
            Regime.INTENSIFYING,
            land,
            parameters,
            self.constants,
            random.Random(1),
        )
        self.assertLess(result.state.wind_ms, weak_state.wind_ms)
        self.assertGreaterEqual(
            result.state.central_pressure_pa, weak_state.central_pressure_pa
        )

    def test_observation_equation_exposes_three_scored_fields(self):
        observed = observation_vector(self.state)
        self.assertEqual(observed, (45.0, 94_500.0, 25_000.0))
        self.assertTrue(all(math.isfinite(value) for value in observed))


if __name__ == "__main__":
    unittest.main()
