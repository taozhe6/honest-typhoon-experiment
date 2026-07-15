from __future__ import annotations

import csv
import json
import math
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "round_v2"


class RoundV2OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (PROJECT_ROOT / "config" / "round_v2.json").read_text(encoding="utf-8")
        )
        cls.manifest = json.loads(
            (PROJECT_ROOT / "config" / "round_v2_eligibility_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        cls.summary = json.loads(
            (OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
        )
        cls.correlation = json.loads(
            (OUTPUT_DIR / "correlation_neff.json").read_text(encoding="utf-8")
        )
        cls.cv = json.loads(
            (OUTPUT_DIR / "loocv_intervals.json").read_text(encoding="utf-8")
        )
        with (OUTPUT_DIR / "paired_track_rows.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_manifest_is_frozen_before_errors_and_matches_rows(self) -> None:
        self.assertEqual(
            self.manifest["status"], "eligibility-frozen-before-round-v2-errors"
        )
        self.assertFalse(self.manifest["selection_rule"]["reads_track_error"])
        expected = {storm["atcf_id"] for storm in self.manifest["qualified_storms"]}
        actual = {row["atcf_id"] for row in self.rows}
        self.assertEqual(actual, expected)

    def test_pair_keys_are_unique_and_summary_counts_match(self) -> None:
        keys = {
            (row["atcf_id"], row["cycle_utc"], row["lead_hours"])
            for row in self.rows
        }
        self.assertEqual(len(keys), len(self.rows))
        for lead in self.config["lead_hours"]:
            count = sum(int(row["lead_hours"]) == lead for row in self.rows)
            for aid in ("CMC", "NGX", "DYC2"):
                record = next(
                    item
                    for item in self.summary
                    if item["lead_hours"] == lead and item["aid"] == aid
                )
                self.assertEqual(record["record_count"], count)

    def test_neff_formula_and_scope_disclaimer(self) -> None:
        primary = self.correlation["primary"]
        self.assertAlmostEqual(primary["neff"], 2.0 / (1.0 + primary["rho"]))
        self.assertIn("not model dynamical independence", self.correlation["scope_disclaimer"])

    def test_loocv_uses_storm_holdouts(self) -> None:
        estimated = [row for row in self.cv if row["status"] == "estimated"]
        self.assertEqual(len(estimated), 15)
        self.assertTrue(
            all(row["training_storm_count_per_fold"] >= 10 for row in estimated)
        )
        self.assertTrue(all(0.0 <= row["coverage"] <= 1.0 for row in estimated))

    def test_consensus_coordinates_are_finite(self) -> None:
        for row in self.rows:
            self.assertTrue(math.isfinite(float(row["dyc2_latitude"])))
            self.assertTrue(math.isfinite(float(row["dyc2_longitude"])))
            self.assertTrue(math.isfinite(float(row["dyc2_error_km"])))


if __name__ == "__main__":
    unittest.main()
