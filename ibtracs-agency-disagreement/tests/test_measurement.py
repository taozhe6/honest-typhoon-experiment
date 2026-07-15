from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
from shapely.geometry import LineString, box

from ibtracs_measurement.analysis import regression_design
from ibtracs_measurement.data import KT_TO_MS, analysis_sample, normalize_winds
from ibtracs_measurement.geometry import CoastGeometry
from ibtracs_measurement.stats import (
    average_off_diagonal,
    leave_one_out_deviations,
    neff_from_rho,
    pairwise_sd_matrix,
)


class ConversionTests(unittest.TestCase):
    def test_wind_conversion_preserves_declared_windows(self) -> None:
        row = {
            "USA_WIND": [100.0],
            "TOKYO_WIND": [100.0],
            "CMA_WIND": [100.0],
            "HKO_WIND": [100.0],
            "KMA_WIND": [100.0],
        }
        converted = normalize_winds(pd.DataFrame(row), usa_factor=0.93, cma_factor=0.96)
        self.assertAlmostEqual(converted.loc[0, "JTWC"], 100.0 * KT_TO_MS * 0.93)
        self.assertAlmostEqual(converted.loc[0, "CMA"], 100.0 * KT_TO_MS * 0.96)
        self.assertAlmostEqual(converted.loc[0, "JMA"], 100.0 * KT_TO_MS)

    def test_analysis_sample_stage_uses_exact_six_hour_neighbors(self) -> None:
        times = pd.date_range("2020-01-01", periods=3, freq="6h", tz="UTC")
        frame = pd.DataFrame(
            {
                "SID": ["A"] * 3,
                "time": times,
                "SEASON": [2020] * 3,
                "NATURE": ["TS"] * 3,
                "is_land": [False] * 3,
                "USA_WIND": [40.0, 50.0, 60.0],
                "TOKYO_WIND": [40.0, 50.0, 60.0],
                "CMA_WIND": [40.0, 50.0, 60.0],
                "HKO_WIND": [40.0, 50.0, 60.0],
                "KMA_WIND": [40.0, 50.0, 60.0],
            }
        )
        for agency in ("JTWC", "JMA", "CMA", "HKO", "KMA"):
            frame[f"original_{agency}"] = True
        normalized = normalize_winds(frame, usa_factor=0.93, cma_factor=0.96)
        sample = analysis_sample(normalized)
        middle = sample.loc[sample["time"].eq(times[1])].iloc[0]
        self.assertEqual(middle["stage"], "intensifying")


class StatisticalTests(unittest.TestCase):
    def test_pairwise_sd_uses_differences(self) -> None:
        values = np.array([[1.0, 1.0], [2.0, 4.0], [3.0, 7.0]])
        result = pairwise_sd_matrix(values)
        self.assertAlmostEqual(result[0, 1], np.std(np.array([0.0, -2.0, -4.0]), ddof=1))

    def test_leave_one_out_deviations_are_zero_sum(self) -> None:
        rng = np.random.default_rng(7)
        values = rng.normal(size=(50, 5))
        deviations = leave_one_out_deviations(values)
        np.testing.assert_allclose(deviations.sum(axis=1), 0.0, atol=1e-12)
        rho_bar = average_off_diagonal(np.corrcoef(deviations, rowvar=False))
        denominator, _ = neff_from_rho(rho_bar)
        self.assertLess(abs(denominator), 0.2)

    def test_regression_design_caps_near_distance(self) -> None:
        frame = pd.DataFrame(
            {
                "coast_distance_km": [100.0, 400.0, 700.0],
                "relative_disagreement": [0.1, 0.2, 0.3],
                "SID": ["A", "A", "B"],
                "intensity_bin": ["17.2-24.4"] * 3,
                "era": ["2020-2024"] * 3,
                "stage": ["steady"] * 3,
            }
        )
        x, _, names = regression_design(frame, breakpoint_km=400.0, controls=False)
        near = x[:, names.index("distance_near_per_100km")]
        far = x[:, names.index("distance_far_per_100km")]
        np.testing.assert_allclose(near, [1.0, 4.0, 4.0])
        np.testing.assert_allclose(far, [0.0, 0.0, 3.0])


class GeometryTests(unittest.TestCase):
    def test_distance_and_crossing(self) -> None:
        coastline = [LineString([(0.0, -2.0), (0.0, 2.0)])]
        points = np.array([[0.0, -2.0], [0.0, 0.0], [0.0, 2.0]])
        geometry = CoastGeometry(
            lines=coastline,
            land=box(0.0, -2.0, 2.0, 2.0),
            densified_lonlat=points,
            spacing_km=5.0,
        )
        distance = geometry.distance_km(np.array([1.0]), np.array([0.0]))[0]
        self.assertTrue(110.0 < distance < 112.5)
        fraction = geometry.crossing_fraction(-1.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(fraction, 0.5, places=6)


if __name__ == "__main__":
    unittest.main()
