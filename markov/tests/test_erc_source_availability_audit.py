import importlib.util
from pathlib import Path
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "audit_erc_source_availability.py"
)
SPEC = importlib.util.spec_from_file_location(
    "audit_erc_source_availability", SCRIPT_PATH
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {SCRIPT_PATH}")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class ERCSourceAvailabilityAuditTests(unittest.TestCase):
    def test_tcprimed_inventory_counts_storms_and_sensors(self):
        keys = [
            "v01r01/final/2024/WP/01/TCPRIMED_v01r01-final_WP012024_GMI_GPM_a.nc",
            "v01r01/final/2024/WP/01/TCPRIMED_v01r01-final_WP012024_ATMS_NPP_b.nc",
            "v01r01/final/2024/WP/01/TCPRIMED_v01r01-final_WP012024_env_s_e.nc",
            "v01r01/final/2024/WP/02/TCPRIMED_v01r01-final_WP022024_GMI_GPM_c.nc",
        ]
        summary = AUDIT.summarize_tcprimed_keys(keys)
        self.assertEqual(summary["storm_count"], 2)
        self.assertEqual(summary["overpass_file_count"], 3)
        self.assertEqual(summary["environment_file_count"], 1)
        self.assertEqual(summary["overpass_count_by_sensor"], {"ATMS": 1, "GMI": 2})

    def test_archer_ids_are_unique_and_season_scoped(self):
        payload = b'2026_01W/ 2026_02W/ 2026_02W/ 2025_30W/'
        self.assertEqual(
            AUDIT.archer_west_pacific_ids(payload, 2026),
            ["2026_01W", "2026_02W"],
        )


if __name__ == "__main__":
    unittest.main()
