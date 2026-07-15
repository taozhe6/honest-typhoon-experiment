import importlib.util
from pathlib import Path
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "audit_ibtracs_observations.py"
)
SPEC = importlib.util.spec_from_file_location("audit_ibtracs_observations", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SCRIPT_PATH}")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ObservationAuditScriptTests(unittest.TestCase):
    def test_correlation_and_effective_rank_detect_one_dimensional_data(self):
        rows = [(1.0, 2.0, 3.0), (2.0, 4.0, 6.0), (3.0, 6.0, 9.0)]
        matrix = AUDIT.correlation_matrix(rows)
        eigenvalues = AUDIT.symmetric_3x3_eigenvalues(matrix)
        dimensions = AUDIT.effective_dimensions(eigenvalues)

        for row in matrix:
            self.assertTrue(all(abs(value - 1.0) < 1.0e-12 for value in row))
        self.assertAlmostEqual(eigenvalues[0], 3.0, places=12)
        self.assertAlmostEqual(dimensions["participation_ratio"], 1.0, places=12)

    def test_symmetric_eigenvalues_match_diagonal_matrix(self):
        eigenvalues = AUDIT.symmetric_3x3_eigenvalues(
            [[1.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 2.0]]
        )
        self.assertEqual(eigenvalues, [3.0, 2.0, 1.0])

    def test_effective_rank_keeps_redundancy_distinct_from_parameter_rank(self):
        dimensions = AUDIT.effective_dimensions([2.4, 0.58, 0.02])
        self.assertLess(dimensions["participation_ratio"], 2.0)
        self.assertGreater(dimensions["participation_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
