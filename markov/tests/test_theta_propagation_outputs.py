from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "theta_propagation"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ThetaPropagationOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = json.loads(
            (OUTPUT / "theta_propagation.json").read_text(encoding="utf-8")
        )
        cls.manifest = json.loads(
            (OUTPUT / "manifest.json").read_text(encoding="utf-8")
        )
        with (OUTPUT / "theta_grid.csv").open(newline="", encoding="utf-8") as handle:
            cls.rows = list(csv.DictReader(handle))

    def test_grid_matches_frozen_design(self) -> None:
        self.assertEqual(len(self.result["theta_multipliers"]), 61)
        self.assertEqual(len(self.result["scenario_summaries"]), 3)
        self.assertEqual(len(self.rows), 183)
        self.assertEqual({float(row["comparison_hour"]) for row in self.rows}, {48.0})

    def test_final_state_envelope_is_structurally_consistent(self) -> None:
        cross = self.result["cross_scenario"]
        self.assertAlmostEqual(
            cross["maximum_baseline_centered_absolute_delta_ms"],
            3.5432238790581287,
        )
        self.assertAlmostEqual(
            cross["maximum_endpoint_to_endpoint_width_ms"],
            6.068231659921768,
        )
        checks = self.result["structural_checks"]
        self.assertTrue(checks["all_wind_responses_monotonic"])
        self.assertTrue(checks["equivalent_parameterizations"]["passed"])

    def test_provenance_inputs_match_current_frozen_files(self) -> None:
        provenance = self.result["provenance"]
        self.assertEqual(
            provenance["config_sha256"],
            sha256_file(ROOT / "config" / "theta_propagation.json"),
        )
        self.assertEqual(
            provenance["scenario_config_sha256"],
            sha256_file(ROOT / "config" / "global_sensitivity.json"),
        )
        self.assertEqual(
            provenance["protocol_sha256"],
            sha256_file(ROOT / "docs" / "theta-propagation-protocol.md"),
        )
        self.assertRegex(provenance["analysis_code_git_commit"], r"^[0-9a-f]{40}$")

    def test_manifest_hashes_match_outputs(self) -> None:
        for name, audit in self.manifest["outputs"].items():
            path = OUTPUT / name
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_size, audit["bytes"])
            self.assertEqual(sha256_file(path), audit["sha256"])


if __name__ == "__main__":
    unittest.main()
