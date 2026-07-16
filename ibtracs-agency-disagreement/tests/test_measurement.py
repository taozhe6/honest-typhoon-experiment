from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
from shapely.geometry import LineString, box

from ibtracs_measurement.analysis import regression_design
from ibtracs_measurement.data import KT_TO_MS, analysis_sample, normalize_winds
from ibtracs_measurement.geometry import CoastGeometry
from ibtracs_measurement.landfall_truth import (
    Event,
    bootstrap_error_correlations,
    event_truth_from_grade_a,
    finalize_records,
    parse_isd_wnd,
    source_event_status_table,
)
from ibtracs_measurement.stats import (
    average_off_diagonal,
    leave_one_out_deviations,
    neff_from_rho,
    pairwise_sd_matrix,
)
from ibtracs_measurement.wind_pressure import (
    bootstrap_error_intervals,
    cross_validate_pressure_only,
    diagnose_wind_pressure,
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


class LandfallTruthTests(unittest.TestCase):
    def test_isd_wnd_parser_preserves_window_and_quality(self) -> None:
        normal = parse_isd_wnd("090,1,N,0123,1")
        five_minute = parse_isd_wnd("180,1,H,0200,4")
        rejected = parse_isd_wnd("270,1,N,0300,3")
        self.assertEqual(normal["speed_ms"], 12.3)
        self.assertIsNone(normal["averaging_window_minutes"])
        self.assertEqual(five_minute["averaging_window_minutes"], 5.0)
        self.assertTrue(five_minute["quality_passed"])
        self.assertFalse(rejected["quality_passed"])

    def test_grade_b_observations_never_enter_event_truth(self) -> None:
        record = {
            "SID": "S1",
            "grade": "B",
            "scoreable": False,
            "comparable_10min_ms": 20.0,
            "source_id": "TEST",
            "station_id": "X",
            "observation_time_utc": "2020-01-01T00:00:00+00:00",
            "independent_measurement": True,
            "quality_passed": True,
        }
        truth = finalize_records([record])
        self.assertTrue(event_truth_from_grade_a(truth).empty)

    def test_source_event_status_has_one_row_per_source(self) -> None:
        event = Event(
            sid="S1",
            name="TEST",
            crossing_time=pd.Timestamp("2020-01-01T00:00:00Z"),
            latitude=10.0,
            longitude=120.0,
        )
        record = {
            "SID": "S1",
            "source_id": "FOUND",
            "grade": "B",
            "scoreable": False,
            "independent_measurement": True,
            "quality_passed": True,
        }
        truth = finalize_records([record])
        catalog = [
            {
                "source_id": "FOUND",
                "source": "Found source",
                "availability": "available",
                "access": "anonymous",
                "url": "https://example.test/found",
                "evidence": "[验证过]",
            },
            {
                "source_id": "BLOCKED",
                "source": "Blocked source",
                "availability": "unavailable",
                "access": "application",
                "url": "https://example.test/blocked",
                "evidence": "[据文档]",
            },
        ]
        status = source_event_status_table(
            [event],
            truth,
            catalog,
            collected_source_ids=["FOUND"],
            absent_status={
                "BLOCKED": {
                    "status": "source_unavailable_access_barrier",
                    "reason": "application required",
                    "searched_for_event": False,
                }
            },
        )
        self.assertEqual(len(status), 2)
        self.assertEqual(status.loc[status.source_id.eq("FOUND"), "record_count"].item(), 1)
        self.assertEqual(
            status.loc[status.source_id.eq("BLOCKED"), "status"].item(),
            "source_unavailable_access_barrier",
        )

    def test_error_correlation_bootstrap_uses_storm_blocks(self) -> None:
        rows = pd.DataFrame(
            {
                "SID": [f"S{index}" for index in range(6)],
                "A_error_ms": [1, 2, 4, 7, 11, 16],
                "B_error_ms": [2, 4, 8, 14, 22, 32],
            }
        )
        result = bootstrap_error_correlations(
            rows,
            ("A", "B"),
            replicates=100,
            seed=31,
        )
        self.assertEqual(len(result), 4)
        pair = result.loc[
            result["agency_i"].eq("A") & result["agency_j"].eq("B")
        ].iloc[0]
        self.assertAlmostEqual(pair["correlation"], 1.0)
        self.assertEqual(pair["cluster_unit"], "SID")
        self.assertGreater(pair["valid_bootstrap_replicates"], 90)


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

    def test_wind_pressure_diagnostic_recovers_linear_relation(self) -> None:
        rows = []
        for storm in range(12):
            for step in range(5):
                deficit = 10.0 + 5.0 * step + storm
                rows.append(
                    {
                        "SID": f"S{storm:02d}",
                        "wind_ms": 7.0 + 0.55 * deficit + (storm % 3 - 1) * 0.2,
                        "pressure_hpa": 1010.0 - deficit,
                        "pressure_deficit_hpa": deficit,
                    }
                )
        frame = pd.DataFrame(rows)
        result = diagnose_wind_pressure(frame, replicates=100, seed=19)
        self.assertAlmostEqual(result["wind_from_pressure"]["slope"], 0.55, delta=0.01)
        self.assertLess(result["wind_pressure_pearson_r"], -0.99)

    def test_pressure_only_cross_validation_groups_by_storm(self) -> None:
        rows = []
        base_time = pd.Timestamp("2020-01-01", tz="UTC")
        for storm in range(15):
            for step in range(4):
                deficit = 8.0 + 4.0 * step + storm
                rows.append(
                    {
                        "SID": f"S{storm:02d}",
                        "time": base_time + pd.Timedelta(6 * step, unit="h"),
                        "wind_ms": 8.0 + 0.5 * deficit,
                        "pressure_hpa": 1010.0 - deficit,
                        "pressure_deficit_hpa": deficit,
                    }
                )
        frame = pd.DataFrame(rows)
        predictions, summary = cross_validate_pressure_only(frame, folds=5, seed=23)
        self.assertTrue(predictions.groupby("SID")["fold"].nunique().eq(1).all())
        self.assertLess(summary["pressure_only"]["rmse_ms"], 1e-10)
        intervals = bootstrap_error_intervals(
            predictions["wind_ms"].to_numpy(),
            predictions["predicted_wind_ms"].to_numpy(),
            predictions["SID"].to_numpy(),
            replicates=100,
            seed=29,
        )
        self.assertLess(intervals["rmse_ms"][1], 1e-9)


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
