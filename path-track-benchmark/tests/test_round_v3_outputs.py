from __future__ import annotations

import csv
import json
import unittest
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "round_v3"


class RoundV3OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (PROJECT_ROOT / "config" / "round_v3.json").read_text(encoding="utf-8")
        )
        cls.summary = json.loads(
            (OUTPUT_ROOT / "summary.json").read_text(encoding="utf-8")
        )
        cls.diagnostics = json.loads(
            (OUTPUT_ROOT / "correlation_neff.json").read_text(encoding="utf-8")
        )
        cls.source_audit = json.loads(
            (OUTPUT_ROOT / "source_audit.json").read_text(encoding="utf-8")
        )
        with (OUTPUT_ROOT / "paired_track_rows.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            cls.rows = list(csv.DictReader(handle))
        with (OUTPUT_ROOT / "coverage_audit.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            cls.coverage = list(csv.DictReader(handle))

    def test_dyc2_source_audit_is_machine_checked(self) -> None:
        self.assertEqual(self.source_audit["files_scanned"], 27)
        self.assertEqual(self.source_audit["tech_counts"]["DYC2"], 0)
        self.assertGreater(self.source_audit["tech_counts"]["UKM"], 0)
        self.assertFalse(self.config["historical_alias"]["official_atcf_tech"])

    def test_strict_pair_keys_are_unique_and_use_frozen_storms(self) -> None:
        keys = [
            (row["atcf_id"], row["cycle_utc"], int(row["lead_hours"]))
            for row in self.rows
        ]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(
            {row["atcf_id"] for row in self.rows},
            set(self.config["eligible_atcf_ids"]),
        )
        self.assertEqual({row["atcf_id"][-4:] for row in self.rows}, {"2022", "2024"})

    def test_summary_counts_match_paired_rows(self) -> None:
        counts = Counter(int(row["lead_hours"]) for row in self.rows)
        for item in self.summary:
            self.assertEqual(item["record_count"], counts[item["lead_hours"]])
            self.assertEqual(
                item["storm_count"],
                len(
                    {
                        row["atcf_id"]
                        for row in self.rows
                        if int(row["lead_hours"]) == item["lead_hours"]
                    }
                ),
            )

    def test_neff_formula_and_same_sample_delta(self) -> None:
        primary = self.diagnostics["primary"]
        expected_baseline = 2.0 / (1.0 + primary["rho_cmc_ngx"])
        expected_independent = 2.0 / (1.0 + primary["rho_local_eq2_ukm"])
        self.assertAlmostEqual(primary["neff_cmc_ngx"], expected_baseline)
        self.assertAlmostEqual(primary["neff_local_eq2_ukm"], expected_independent)
        self.assertAlmostEqual(
            primary["delta_neff"], expected_independent - expected_baseline
        )
        self.assertEqual(primary["record_count"], len(self.rows))
        self.assertIn("not measure accuracy", self.diagnostics["scope_disclaimer"])

    def test_2023_ukm_coverage_is_reported_as_zero(self) -> None:
        selected = [
            row
            for row in self.coverage
            if row["season"] == "2023" and row["lead_hours"] == "72"
        ]
        self.assertGreater(len(selected), 0)
        self.assertTrue(all(int(row["ukm_cycle_count"]) == 0 for row in selected))
        self.assertTrue(
            all(int(row["three_way_common_cycle_count"]) == 0 for row in selected)
        )


if __name__ == "__main__":
    unittest.main()

