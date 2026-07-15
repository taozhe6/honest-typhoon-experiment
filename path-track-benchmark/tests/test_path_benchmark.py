from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from path_benchmark.core import (
    DataConflictError,
    ForecastPoint,
    PairedTrackRow,
    parse_adeck,
    parse_atcf_coordinate,
    read_ibtracs_truth,
    strict_pair,
    summarize_rows,
    track_error_km,
)


class PathBenchmarkTests(unittest.TestCase):
    def test_coordinate_parser_handles_dateline_and_hemispheres(self) -> None:
        self.assertEqual(parse_atcf_coordinate("123N"), 12.3)
        self.assertEqual(parse_atcf_coordinate("045S"), -4.5)
        self.assertAlmostEqual(parse_atcf_coordinate("1799E"), 179.9)
        self.assertAlmostEqual(parse_atcf_coordinate("1799W"), -179.9)

    def test_identical_wind_radius_rows_are_deduplicated(self) -> None:
        row = "WP, 12, 2024090100, 03, CMC, 024, 200N, 1400E, 40, 990"
        points = parse_adeck(
            row + "\n" + row, "WP122024", {"CMC"}, {24}
        )
        self.assertEqual(len(points), 1)

    def test_conflicting_duplicate_positions_fail(self) -> None:
        text = "\n".join(
            [
                "WP, 12, 2024090100, 03, CMC, 024, 200N, 1400E",
                "WP, 12, 2024090100, 03, CMC, 024, 201N, 1400E",
            ]
        )
        with self.assertRaises(DataConflictError):
            parse_adeck(text, "WP122024", {"CMC"}, {24})

    def test_wgs84_equatorial_degree(self) -> None:
        self.assertAlmostEqual(track_error_km(0.0, 0.0, 0.0, 1.0), 111.319, 3)

    def test_truth_reader_and_strict_pair_use_exact_valid_time(self) -> None:
        csv_text = "\n".join(
            [
                "USA_ATCF_ID,ISO_TIME,USA_LAT,USA_LON",
                ",,degrees_north,degrees_east",
                "WP122024,2024-09-02 00:00:00,20.0,140.0",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "truth.csv"
            path.write_text(csv_text, encoding="utf-8")
            truth = read_ibtracs_truth(path, {"WP122024"})
        cycle = datetime(2024, 9, 1, tzinfo=timezone.utc)
        forecasts = [
            ForecastPoint("WP122024", cycle, "CMC", 24, 20.1, 140.0),
            ForecastPoint("WP122024", cycle, "NGX", 24, 19.9, 140.0),
        ]
        rows = strict_pair(forecasts, truth, {"WP122024": "YAGI"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].valid_time_utc.hour, 0)

    def test_storm_block_summary_is_deterministic(self) -> None:
        cycle = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index, storm in enumerate(("WP012024", "WP022024"), start=1):
            rows.append(
                PairedTrackRow(
                    storm,
                    storm,
                    cycle,
                    cycle,
                    24,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    10.0 * index,
                    20.0 * index,
                )
            )
        first = summarize_rows(rows, [24], 100, 123)
        second = summarize_rows(rows, [24], 100, 123)
        self.assertEqual(first, second)
        self.assertEqual({item["aid"] for item in first}, {"CMC", "NGX", "CMC_MINUS_NGX"})


if __name__ == "__main__":
    unittest.main()
