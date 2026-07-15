import math
import unittest

from typhoon_markov.rk45 import integrate_rk45


class RK45Tests(unittest.TestCase):
    def test_matches_exponential_decay_analytic_solution(self):
        result = integrate_rk45(
            lambda _time, values: (-0.5 * values[0],),
            (1.0,),
            0.0,
            10.0,
            relative_tolerance=1.0e-9,
            absolute_tolerances=(1.0e-12,),
            maximum_step_s=0.5,
        )

        self.assertAlmostEqual(result.values[0], math.exp(-5.0), places=10)
        self.assertGreater(result.stats.accepted_steps, 0)

    def test_zero_duration_returns_initial_state(self):
        result = integrate_rk45(lambda _time, values: values, (3.0, 4.0), 2.0, 2.0)
        self.assertEqual(result.values, (3.0, 4.0))
        self.assertEqual(result.stats.accepted_steps, 0)


if __name__ == "__main__":
    unittest.main()
