from __future__ import annotations

from pathlib import Path
import unittest

from typhoon_markov.published_erc import extract_pdf_text, parse_kuo_ce_table


class PublishedErcTests(unittest.TestCase):
    def test_parse_kuo_rows_and_repeat_occurrence(self) -> None:
        text = """
1997-10W     Rosie       140       072212Z      19.3N       131.8E        135         0722-2147Z(SSMI)             10             PN         28.97          26.32
2004-19W(2)    Chaba     155   082300Z   26.9N   134.3E   105   0827-0920Z(SSMI)   105     NP       52.16    60.95
"""
        rows = parse_kuo_ce_table(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tc_name"], "Rosie")
        self.assertEqual(rows[1]["occurrence_for_tc"], 2)
        self.assertEqual(rows[1]["tc_number"], "2004-19W")
        self.assertIsNone(rows[0]["erc_onset_label"])

    def test_published_kuo_table_has_62_rows(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "data"
            / "published"
            / "CEdataWPAC9706.pdf"
        )
        rows = parse_kuo_ce_table(extract_pdf_text(path))
        self.assertEqual(len(rows), 62)
        self.assertEqual(len({row["tc_number"] for row in rows}), 55)


if __name__ == "__main__":
    unittest.main()
