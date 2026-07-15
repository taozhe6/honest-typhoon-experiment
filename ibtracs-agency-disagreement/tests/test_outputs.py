from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"


class OutputAuditTests(unittest.TestCase):
    def load_json(self, name: str) -> dict:
        return json.loads((OUTPUTS / name).read_text(encoding="utf-8"))

    def test_required_deliverables_exist(self) -> None:
        required = (
            ROOT / "report.md",
            OUTPUTS / "pairwise_disagreement_S088.csv",
            OUTPUTS / "pairwise_disagreement_S093.csv",
            OUTPUTS / "pairwise_disagreement_intervals.json",
            OUTPUTS / "neff_sensitivity.json",
            OUTPUTS / "coast_effect.json",
            OUTPUTS / "missingness.json",
            OUTPUTS / "landfall_audit.json",
            OUTPUTS / "disagreement_vs_coast.png",
            OUTPUTS / "analysis_dataset.csv",
            OUTPUTS / "provenance.json",
        )
        self.assertTrue(all(path.exists() and path.stat().st_size > 0 for path in required))

    def test_pairwise_matrices_are_symmetric(self) -> None:
        for scenario in ("S088", "S093"):
            matrix = pd.read_csv(
                OUTPUTS / f"pairwise_disagreement_{scenario}.csv", index_col=0
            ).to_numpy(float)
            self.assertEqual(matrix.shape, (5, 5))
            np.testing.assert_allclose(matrix, matrix.T, atol=1e-12)
            np.testing.assert_allclose(np.diag(matrix), 0.0, atol=1e-12)

    def test_neff_formula_and_gate(self) -> None:
        result = self.load_json("neff_sensitivity.json")
        for scenario in ("S088", "S093"):
            item = result["scenarios"][scenario]["cma_factors"]["0.96"]
            self.assertAlmostEqual(item["denominator"], 1.0 + 4.0 * item["rho_bar"])
            self.assertAlmostEqual(item["neff"], 5.0 / item["denominator"])
            self.assertGreater(item["neff_interval"][0], 5.0)
            self.assertFalse(item["finite_identifiable"])

    def test_core_coast_result_is_reverse_direction_without_breakpoint(self) -> None:
        result = self.load_json("coast_effect.json")
        for scenario in ("S088", "S093"):
            adjusted = result[scenario]["suite"]["adjusted"]
            self.assertLess(adjusted["near_slope"]["upper"], 0.0)
            self.assertLess(adjusted["slope_change"]["lower"], 0.0)
            self.assertGreater(adjusted["slope_change"]["upper"], 0.0)
            self.assertEqual(adjusted["decision"], "disagreement_increases_toward_coast")

    def test_geometry_truth_gate_and_bootstrap_provenance(self) -> None:
        provenance = self.load_json("provenance.json")
        self.assertEqual(provenance["bootstrap_replicates"], 2000)
        self.assertTrue(provenance["taiwan_land_mask_check_121E_23_5N"])
        self.assertLessEqual(provenance["coast_validation"]["p95_absolute_error_km"], 5.0)
        landfall = self.load_json("landfall_audit.json")
        for scenario in ("S088", "S093"):
            self.assertEqual(landfall[scenario]["station_truth_fields"], [])
            self.assertEqual(landfall[scenario]["truth_error_status"], "unidentifiable_from_ibtracs")

    def test_plot_has_nonblank_pixel_variance(self) -> None:
        image = np.asarray(Image.open(OUTPUTS / "disagreement_vs_coast.png").convert("RGB"))
        self.assertGreater(float(image.std()), 5.0)


if __name__ == "__main__":
    unittest.main()
