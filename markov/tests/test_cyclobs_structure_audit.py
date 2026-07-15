import importlib.util
import math
from pathlib import Path
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "audit_cyclobs_structure.py"
)
SPEC = importlib.util.spec_from_file_location("audit_cyclobs_structure", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SCRIPT_PATH}")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class CyclObsStructureAuditTests(unittest.TestCase):
    def test_detects_two_separated_axisymmetric_wind_peaks(self):
        radius = [float(value) for value in range(201)]
        profile = [
            15.0
            + 25.0 * math.exp(-((value - 30.0) / 10.0) ** 2)
            + 18.0 * math.exp(-((value - 105.0) / 16.0) ** 2)
            for value in radius
        ]
        coverage = [1.0] * len(radius)

        peaks, _ = AUDIT.detect_profile_peaks(
            radius,
            profile,
            coverage,
            search_bounds_km=(10.0, 180.0),
            smoothing_window_km=9.0,
            prominence_window_km=20.0,
            minimum_coverage_fraction=0.5,
            minimum_prominence_ms=0.25,
        )
        pair = AUDIT.select_dual_peak_pair(
            peaks,
            minimum_prominence_ms=0.5,
            minimum_separation_km=30.0,
        )

        self.assertEqual(len(pair), 2)
        self.assertAlmostEqual(pair[0]["radius_km"], 30.0, delta=2.0)
        self.assertAlmostEqual(pair[1]["radius_km"], 105.0, delta=2.0)

    def test_coverage_gate_removes_an_unobserved_outer_peak(self):
        radius = [float(value) for value in range(201)]
        profile = [
            15.0
            + 25.0 * math.exp(-((value - 30.0) / 10.0) ** 2)
            + 18.0 * math.exp(-((value - 105.0) / 16.0) ** 2)
            for value in radius
        ]
        coverage = [1.0 if value < 80.0 else 0.2 for value in radius]

        peaks, _ = AUDIT.detect_profile_peaks(
            radius,
            profile,
            coverage,
            search_bounds_km=(10.0, 180.0),
            smoothing_window_km=9.0,
            prominence_window_km=20.0,
            minimum_coverage_fraction=0.5,
            minimum_prominence_ms=0.25,
        )
        pair = AUDIT.select_dual_peak_pair(
            peaks,
            minimum_prominence_ms=0.5,
            minimum_separation_km=30.0,
        )

        self.assertEqual(pair, [])

    def test_query_parameters_are_supplied_by_the_case(self):
        url = AUDIT.build_api_url(
            "https://example.test/api",
            sid="wp012030",
            start_date="2030-01-02",
            stop_date="2030-01-04",
        )
        self.assertIn("sid=wp012030", url)
        self.assertIn("acquisition_start_time=2030-01-02", url)
        self.assertIn("acquisition_stop_time=2030-01-04", url)


if __name__ == "__main__":
    unittest.main()
