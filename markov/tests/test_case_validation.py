import json
from pathlib import Path
import unittest

from typhoon_markov.case_validation import (
    ObservationContractError,
    audit_regime_wind_influence,
    evaluate_model_eligibility,
    require_cross_channel_operator,
    score_native_channel,
)
from typhoon_markov.model import Forcing, PhysicalConstants, State


ROOT = Path(__file__).resolve().parents[1]


class BaviCaseValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(
            (ROOT / "config" / "wind_observation_contract.json").read_text(
                encoding="utf-8"
            )
        )
        cls.case = json.loads(
            (ROOT / "cases" / "bavi_2026_reintensification.json").read_text(
                encoding="utf-8"
            )
        )
        cls.model_config = json.loads(
            (ROOT / "config" / "model_v0.json").read_text(encoding="utf-8")
        )

    def test_every_stage_carries_native_averaging_period(self):
        for agency, channel in self.case["channels"].items():
            expected = self.contract["agencies"][agency][
                "wind_averaging_period_seconds"
            ]
            for stage in ("initial_analysis", "forecast", "truth"):
                self.assertEqual(
                    channel[stage]["wind_averaging_period_seconds"], expected
                )

    def test_every_source_locks_selected_evidence_hash(self):
        for source in self.case["provenance"]["sources"].values():
            self.assertRegex(source["selected_evidence_sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(source["selected_evidence_bytes_utf8"], 0)

    def test_nmc_native_channel_captures_two_ms_reintensification(self):
        score = score_native_channel(self.case["channels"]["NMC"], self.contract)
        self.assertEqual(score.averaging_period_seconds, 120)
        self.assertEqual(score.initial_wind_native, 40.0)
        self.assertEqual(score.forecast_wind_native, 42.0)
        self.assertEqual(score.observed_wind_native, 42.0)
        self.assertEqual(score.observed_change_native, 2.0)
        self.assertTrue(score.event_direction_captured)

    def test_jtwc_native_channel_misses_ten_knot_reintensification(self):
        score = score_native_channel(self.case["channels"]["JTWC"], self.contract)
        self.assertEqual(score.averaging_period_seconds, 60)
        self.assertEqual(score.initial_wind_native, 75.0)
        self.assertEqual(score.forecast_wind_native, 75.0)
        self.assertEqual(score.observed_wind_native, 85.0)
        self.assertEqual(score.forecast_error_native, -10.0)
        self.assertFalse(score.event_direction_captured)

    def test_jma_native_channel_has_no_reintensification_in_truth(self):
        score = score_native_channel(self.case["channels"]["JMA"], self.contract)
        self.assertEqual(score.averaging_period_seconds, 600)
        self.assertEqual(score.initial_wind_native, 85.0)
        self.assertEqual(score.forecast_wind_native, 85.0)
        self.assertEqual(score.observed_wind_native, 80.0)
        self.assertEqual(score.observed_change_native, -5.0)

    def test_cross_window_score_requires_observation_operator(self):
        nmc = self.case["channels"]["NMC"]["truth"]
        jtwc = self.case["channels"]["JTWC"]["truth"]
        with self.assertRaises(ObservationContractError):
            require_cross_channel_operator(nmc, jtwc)

    def test_current_regime_has_zero_wind_tendency_effect(self):
        probes = [
            (
                State(38.5, 0.75, 96_200.0, 120_000.0),
                Forcing(70.0, 8.0, 0.45, 55.0, 1.0, 6.0, 1.0, 0.0, 22.0),
            ),
            (
                State(45.0, 0.82, 95_000.0, 50_000.0),
                Forcing(68.0, 16.0, 0.65, 35.0, 1.5, 8.0, 1.0, 0.0, 25.0),
            ),
        ]
        report = audit_regime_wind_influence(
            probes, PhysicalConstants.literature_western_north_pacific()
        )
        self.assertFalse(report.influences_wind_tendency)
        self.assertEqual(report.maximum_pairwise_difference_ms_per_day, 0.0)

        eligibility = evaluate_model_eligibility(
            ROOT, self.model_config, self.case, report
        )
        self.assertFalse(eligibility.eligible)
        self.assertEqual(
            eligibility.verdict, "structural-fail-regime-has-zero-wind-effect"
        )


if __name__ == "__main__":
    unittest.main()
