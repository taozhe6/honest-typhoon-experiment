from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from path_benchmark.core import ForecastPoint
from path_benchmark.eligibility import (
    common_cycle_count_at_lead,
    read_cma_peak_candidates,
)


class EligibilityTests(unittest.TestCase):
    def test_reads_main_track_peak_and_reports_missing_cma(self) -> None:
        text = "\n".join(
            [
                "USA_ATCF_ID,SEASON,TRACK_TYPE,NAME,CMA_WIND",
                "WP012022,2022,main,ALPHA,70",
                "WP012022,2022,main,ALPHA,85",
                "WP012022,2022,spur,ALPHA,100",
                "WP022023,2023,main,BETA,",
                "WP902023,2023,main,INVEST,100",
                "WP032025,2025,main,GAMMA,100",
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ibtracs.csv"
            path.write_text(text + "\n", encoding="utf-8")
            storms = read_cma_peak_candidates(path, {2022, 2023, 2024})
        self.assertEqual([storm.atcf_id for storm in storms], ["WP012022", "WP022023"])
        self.assertEqual(storms[0].peak_cma_wind_kt, 85.0)
        self.assertIsNone(storms[1].peak_cma_wind_kt)

    def test_common_72h_cycle_requires_both_raw_aids(self) -> None:
        cycle = datetime(2024, 8, 1, tzinfo=timezone.utc)
        points = [
            ForecastPoint("WP012024", cycle, "CMC", 72, 20.0, 130.0),
            ForecastPoint("WP012024", cycle, "NGX", 72, 21.0, 131.0),
            ForecastPoint("WP012024", cycle, "CMC", 96, 22.0, 132.0),
        ]
        common, by_aid = common_cycle_count_at_lead(points, ("CMC", "NGX"), 72)
        self.assertEqual(common, 1)
        self.assertEqual(by_aid, {"CMC": 1, "NGX": 1})


if __name__ == "__main__":
    unittest.main()
