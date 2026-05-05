from __future__ import annotations

import unittest

from skills.windows_app_finder import _norm, score_name


class TestWindowsAppFinderScoring(unittest.TestCase):
    def test_chinese_colon_normalization(self):
        self.assertEqual(_norm("洛克王国：世界.lnk"), _norm("洛克王国 世界"))

    def test_exact_game_shortcut_scores_high(self):
        score = score_name("洛克王国世界", "洛克王国：世界.lnk")
        self.assertGreaterEqual(score, 90)

    def test_public_desktop_path_can_help(self):
        score = score_name(
            "洛克王国",
            "launcher.lnk",
            r"C:\Users\Public\Desktop\洛克王国：世界.lnk",
        )
        self.assertGreaterEqual(score, 35)

    def test_unrelated_name_scores_low(self):
        score = score_name("洛克王国", "Visual Studio Code.lnk")
        self.assertLess(score, 35)


if __name__ == "__main__":
    unittest.main()
