from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "c_event_label_v2"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CEventLabelV2OutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = json.loads(
            (OUTPUT / "benchmark.json").read_text(encoding="utf-8")
        )
        cls.manifest = json.loads(
            (OUTPUT / "manifest.json").read_text(encoding="utf-8")
        )
        with (OUTPUT / "candidate_rates.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            cls.candidates = list(csv.DictReader(handle))
        with (OUTPUT / "validation_rows.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            cls.validation = list(csv.DictReader(handle))

    def test_selected_label_hits_preregistered_development_target(self) -> None:
        selection = self.result["selection"]
        selected = selection["selected"]
        self.assertTrue(selection["target_achieved"])
        self.assertEqual(selected["horizon_hours"], 24)
        self.assertEqual(selected["threshold_ms"], 2.5)
        self.assertGreaterEqual(selected["event_row_rate"], 0.05)
        self.assertLessEqual(selected["event_row_rate"], 0.15)
        self.assertEqual(len(self.candidates), 12)

    def test_validation_is_nondegenerate_and_temporally_sealed(self) -> None:
        metrics = self.result["benchmark"]["metrics"]
        self.assertTrue(metrics["nondegenerate_validation_classes"])
        self.assertEqual(metrics["validation_classes"], [0, 1])
        self.assertEqual(metrics["validation_rows"], len(self.validation))
        self.assertTrue(all(2019 <= int(row["SEASON"]) <= 2024 for row in self.validation))

    def test_persistence_brier_is_worse_under_frozen_bootstrap(self) -> None:
        brier = self.result["benchmark"]["metrics"]["brier"]
        climate = brier["climatology_brier"]["value"]
        persistence = brier["persistence_brier"]["value"]
        difference = brier["persistence_minus_climatology_brier"]
        self.assertAlmostEqual(difference["value"], persistence - climate)
        self.assertGreater(difference["ci95_low"], 0.0)
        self.assertGreater(difference["ci95_high"], 0.0)

    def test_quantization_and_evidence_scope_are_explicit(self) -> None:
        audit = self.result["quantization_audit"]
        self.assertEqual(audit["wind_average_window_minutes"], 1)
        self.assertEqual(audit["multiple_of_5kt_fraction"], 1.0)
        groups = self.result["selection"]["quantization_equivalence_groups"]
        self.assertEqual(len(groups), 6)
        self.assertTrue(any(len(group["labels"]) == 3 for group in groups))
        self.assertIn("causation remains unassigned", self.result["label_semantics"])
        self.assertEqual(self.result["qualification"], "unvalidated")

    def test_manifest_hashes_match_outputs(self) -> None:
        for name, audit in self.manifest["outputs"].items():
            path = OUTPUT / name
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_size, audit["bytes"])
            self.assertEqual(sha256_file(path), audit["sha256"])


if __name__ == "__main__":
    unittest.main()

