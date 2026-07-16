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

    def test_b_branch_required_deliverables_exist(self) -> None:
        branch = OUTPUTS / "b_branch"
        required = (
            ROOT / "report_b_branch.md",
            ROOT / "landfall_truth_report.md",
            branch / "independent_truth_error_table.csv",
            branch / "independent_truth_error_rows.csv",
            branch / "independent_truth_error_correlation.csv",
            branch / "independent_truth_error_correlation_intervals.csv",
            branch / "landfall_truth.csv",
            branch / "landfall_truth_exclusions.csv",
            branch / "landfall_truth_event_coverage.csv",
            branch / "landfall_truth_source_event_status.csv",
            branch / "landfall_truth_support_evidence.csv",
            branch / "cwa_tdb_station_crosscheck.csv",
            branch / "cwa_tdb_eyewall_review.csv",
            branch / "landfall_truth_coverage_summary.json",
            branch / "landfall_truth_source_audit.json",
            branch / "landfall_truth_manifest.json",
            branch / "source_truth_audit.json",
            branch / "landfall_cma_reference_error_table.csv",
            branch / "landfall_cma_reference_correlation_point.csv",
            branch / "wind_pressure_results.json",
            branch / "pressure_only_cross_validation_rows.csv",
            branch / "wind_pressure_diagnostic.png",
            branch / "run_manifest.json",
        )
        self.assertTrue(all(path.exists() and path.stat().st_size > 0 for path in required))

    def test_b_branch_truth_gate_and_legacy_reproduction(self) -> None:
        branch = OUTPUTS / "b_branch"
        truth = pd.read_csv(branch / "independent_truth_error_table.csv")
        self.assertEqual(len(truth), 5)
        self.assertTrue(truth["matched_independent_truth_events"].eq(4).all())
        self.assertTrue(truth["mae_ms"].notna().all())
        self.assertTrue(
            truth["status"].eq("measured_against_external_grade_a_truth").all()
        )
        summary = json.loads(
            (branch / "landfall_truth_coverage_summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(summary["frozen_events"], 108)
        self.assertEqual(summary["grade_a_events"], 4)
        self.assertEqual(summary["grade_a_or_b_events"], 108)
        self.assertEqual(summary["truth_records"], 16289)
        self.assertEqual(summary["grade_a_records"], 4)
        self.assertEqual(summary["grade_b_records"], 16285)
        self.assertEqual(summary["cwa_tdb_support_events"], 11)
        self.assertEqual(summary["cwa_tdb_station_crosscheck_rows"], 4086)
        observations = pd.read_csv(branch / "landfall_truth.csv")
        self.assertEqual(len(observations), summary["truth_records"])
        self.assertEqual(observations["grade"].value_counts().to_dict(), {"B": 16285, "A": 4})
        scoreable = observations["scoreable"].astype(str).str.lower()
        self.assertTrue(
            scoreable.loc[observations["grade"].eq("A")].eq("true").all()
        )
        self.assertTrue(
            scoreable.loc[observations["grade"].eq("B")].eq("false").all()
        )
        self.assertTrue(observations["raw_sha256"].str.fullmatch(r"[0-9a-f]{64}").all())
        source_status = pd.read_csv(branch / "landfall_truth_source_event_status.csv")
        self.assertEqual(len(source_status), 108 * 13)
        self.assertFalse(source_status[["SID", "source_id"]].duplicated().any())
        self.assertTrue(source_status.groupby("source_id")["SID"].nunique().eq(108).all())
        coverage = pd.read_csv(branch / "landfall_truth_event_coverage.csv")
        self.assertEqual(len(coverage), 108)
        self.assertTrue(coverage["grade_a_or_b_event"].all())
        self.assertEqual(int(coverage["grade_a_event"].sum()), 4)
        self.assertEqual(
            set(coverage.loc[coverage.landfall_country_name.eq("Taiwan"), "landfall_country_code"]),
            {"TWN"},
        )
        support = pd.read_csv(branch / "landfall_truth_support_evidence.csv")
        self.assertEqual(len(support), 88)
        self.assertEqual(
            support.loc[support.evidence_type.eq("warning_registry_match"), "SID"].nunique(),
            11,
        )
        self.assertEqual(
            support.loc[support["product"].eq("Radar"), "SID"].nunique(),
            11,
        )
        self.assertEqual(
            support.loc[
                support.evidence_type.eq("official_station_peak_radar_pair"), "SID"
            ].nunique(),
            11,
        )
        crosscheck = pd.read_csv(branch / "cwa_tdb_station_crosscheck.csv")
        self.assertEqual(len(crosscheck), 4086)
        self.assertEqual(crosscheck["SID"].nunique(), 11)
        self.assertTrue(
            crosscheck.loc[crosscheck.measure_type.eq("CWA"), "averaging_window_minutes"]
            .eq(10.0)
            .all()
        )
        self.assertTrue(
            crosscheck.loc[
                crosscheck.measure_type.eq("AUTOPRECP_WIND"),
                "averaging_window_minutes",
            ].isna().all()
        )
        review = pd.read_csv(branch / "cwa_tdb_eyewall_review.csv")
        self.assertEqual(len(review), 11)
        self.assertEqual(int(review["final_grade"].eq("A").sum()), 4)
        self.assertTrue(review.loc[review.final_grade.eq("A"), "scoreable"].all())
        correlation = pd.read_csv(
            branch / "independent_truth_error_correlation_intervals.csv"
        )
        self.assertEqual(len(correlation), 25)
        self.assertTrue(correlation["matched_grade_a_events"].eq(4).all())
        self.assertTrue(correlation["cluster_unit"].eq("SID").all())
        self.assertTrue(
            correlation.loc[
                correlation.agency_i.ne(correlation.agency_j),
                "valid_bootstrap_replicates",
            ].gt(0).all()
        )
        source = json.loads((branch / "source_truth_audit.json").read_text(encoding="utf-8"))
        self.assertEqual(source["cma_2015_2024_owd_present_rows"], 0)
        self.assertGreater(source["content_candidate_file_count"], 0)
        self.assertEqual(source["wp_agency_content_candidate_file_count"], 0)
        result = json.loads(
            (branch / "wind_pressure_results.json").read_text(encoding="utf-8")
        )
        legacy = result["legacy_reproduction"]
        self.assertEqual(legacy["rows"], 16225)
        self.assertLess(abs(legacy["difference"]), 1e-12)

    def test_b_branch_cross_validation_has_no_storm_leakage(self) -> None:
        branch = OUTPUTS / "b_branch"
        rows = pd.read_csv(branch / "pressure_only_cross_validation_rows.csv")
        self.assertTrue(rows.groupby("SID")["fold"].nunique().eq(1).all())
        rmse = float(np.sqrt(np.mean((rows["predicted_wind_ms"] - rows["wind_ms"]) ** 2)))
        result = json.loads(
            (branch / "wind_pressure_results.json").read_text(encoding="utf-8")
        )
        reported = result["pressure_only_cross_validation"]["pressure_only"]["rmse_ms"]
        self.assertAlmostEqual(rmse, reported, places=12)

    def test_b_branch_plot_has_nonblank_pixel_variance(self) -> None:
        image = np.asarray(
            Image.open(OUTPUTS / "b_branch" / "wind_pressure_diagnostic.png").convert("RGB")
        )
        self.assertGreater(float(image.std()), 5.0)


if __name__ == "__main__":
    unittest.main()
