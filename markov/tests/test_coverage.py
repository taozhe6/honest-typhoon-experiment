from __future__ import annotations

import json
import hashlib
from pathlib import Path
import unittest

from typhoon_markov.coverage import (
    classify_narrative_windows,
    evidence_tally,
    extract_cyclobs_passes,
    observed_dual_ring_intervals,
)


ROOT = Path(__file__).resolve().parents[1]


class CoverageAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(
            (ROOT / "config" / "bavi_erc_coverage_audit.json").read_text(
                encoding="utf-8"
            )
        )
        cls.audit = json.loads(
            (ROOT / "outputs" / "bavi_2026_cyclobs_structure_audit.json").read_text(
                encoding="utf-8"
            )
        )
        cls.passes = extract_cyclobs_passes(
            cls.audit,
            maximum_center_quality_exclusive=2.0,
        )

    def test_primary_scene_tally_and_interval_are_reproduced(self) -> None:
        self.assertEqual(len(self.passes), 16)
        self.assertEqual(sum(record["quality_eligible"] for record in self.passes), 12)
        positives = [
            record for record in self.passes if record["dual_ring_structure_observed"]
        ]
        self.assertEqual(len(positives), 4)
        intervals = observed_dual_ring_intervals(self.passes)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["positive_scene_count"], 4)

    def test_july_four_and_seven_are_indeterminate(self) -> None:
        windows = classify_narrative_windows(
            self.passes,
            self.config["event_windows"],
            maximum_sufficient_sampling_gap_hours=6.0,
            operator_detection_rate_available=self.config["cyclobs"][
                "validated_erc_detection_rate_available"
            ],
        )
        by_id = {window["id"]: window for window in windows}
        self.assertEqual(
            by_id["narrative_window_jul04"]["primary_verdict"],
            "primary_indeterminate",
        )
        self.assertEqual(
            by_id["narrative_window_jul07"]["primary_verdict"],
            "primary_indeterminate",
        )
        self.assertEqual(
            by_id["narrative_window_jul09"]["primary_verdict"],
            "primary_double_ring_structure_observed_in_window",
        )
        self.assertTrue(
            all(not window["sufficient_to_assert_physical_absence"] for window in windows)
        )

    def test_required_tally_excludes_secondary_narrative(self) -> None:
        intervals = observed_dual_ring_intervals(self.passes)
        windows = classify_narrative_windows(
            self.passes,
            self.config["event_windows"],
            maximum_sufficient_sampling_gap_hours=6.0,
            operator_detection_rate_available=self.config["cyclobs"][
                "validated_erc_detection_rate_available"
            ],
        )
        self.assertEqual(
            evidence_tally(intervals, windows),
            {
                "confirmed_primary_double_ring_intervals": 1,
                "indeterminate_narrative_windows": 2,
                "narrative_windows_without_primary_temporal_coverage": 0,
            },
        )

    def test_secondary_source_is_explicitly_non_truth(self) -> None:
        evidence_class = self.config["secondary_narrative_source"]["evidence_class"]
        self.assertIn("secondary_narrative", evidence_class)
        self.assertIn("not_project_verified", evidence_class)

    def test_published_snapshot_keeps_primary_and_secondary_evidence_separate(self) -> None:
        output_dir = ROOT / "outputs" / "c_coverage_correction"
        payload = json.loads(
            (output_dir / "bavi_erc_coverage_audit.json").read_text(encoding="utf-8")
        )
        tally = payload["primary_evidence"]["required_tally"]
        self.assertEqual(tally["confirmed_primary_double_ring_intervals"], 1)
        self.assertEqual(tally["indeterminate_narrative_windows"], 2)
        self.assertEqual(tally["narrative_windows_without_primary_temporal_coverage"], 0)
        self.assertIn("excluded from primary tally", payload["secondary_narrative"]["role"])
        tcprimed = payload["primary_evidence"]["tc_primed"]
        self.assertEqual(tcprimed["file_count"], len(tcprimed["keys"]))
        self.assertTrue(tcprimed["source_evidence"]["response_sha256"])

        manifest = json.loads(
            (output_dir / "manifest.json").read_text(encoding="utf-8")
        )
        for name, metadata in manifest.items():
            path = output_dir / name
            self.assertEqual(path.stat().st_size, metadata["bytes"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), metadata["sha256"])


if __name__ == "__main__":
    unittest.main()
