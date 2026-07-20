from __future__ import annotations

import unittest

from scripts.benchmark.benchmark_chinese_sites import CASES
from scripts.benchmark.benchmark_natural_language_agent import (
    _evaluate,
    _terminal_status,
)


class ChineseSiteBenchmarkTests(unittest.TestCase):
    def test_registry_contains_twenty_sites_and_four_movie_sites(self) -> None:
        movie_cases = [
            case for case in CASES.values() if case.capability.startswith("电影｜")
        ]

        self.assertEqual(len(CASES), 20)
        self.assertGreaterEqual(len(movie_cases), 3)

    def test_chinese_list_result_passes_without_dynamic_anchor(self) -> None:
        case = CASES["douban_movie"]
        items = [
            {
                "title": f"中文电影标题{i}",
                "url": f"https://movie.douban.com/subject/{i}/",
            }
            for i in range(5)
        ]

        evaluation = _evaluate(
            case,
            status="completed",
            results=[
                {
                    "ok": True,
                    "action": "open",
                    "data": {"page": {"url": case.url}},
                },
                {
                    "ok": True,
                    "action": "extract",
                    "data": {"items": items},
                },
            ],
        )

        self.assertTrue(all(evaluation["checks"].values()))
        self.assertEqual(evaluation["accuracy_score"], 100.0)
        self.assertEqual(evaluation["chinese_title_ratio"], 1.0)

    def test_taxonomy_and_tracking_links_fail_content_relevance(self) -> None:
        samples = {
            "douban_movie": [
                {
                    "title": f"电影类型{i}",
                    "url": f"https://movie.douban.com/typerank?type={i}",
                }
                for i in range(5)
            ],
            "sohu_news": [
                {
                    "title": f"合作媒体入口标题{i}",
                    "url": f"https://track.sohu.com/promotion?link={i}",
                }
                for i in range(5)
            ],
            "gushiwen": [
                {
                    "title": f"诗歌分类{i}",
                    "url": f"https://www.gushiwen.cn/gushi/category-{i}.aspx",
                }
                for i in range(5)
            ],
        }

        for case_key, items in samples.items():
            with self.subTest(case=case_key):
                case = CASES[case_key]
                evaluation = _evaluate(
                    case,
                    status="completed",
                    results=[
                        {
                            "ok": True,
                            "action": "open",
                            "data": {"page": {"url": case.url}},
                        },
                        {
                            "ok": True,
                            "action": "extract",
                            "data": {"items": items},
                        },
                    ],
                )

                self.assertFalse(evaluation["checks"]["content_relevance"])
                self.assertEqual(evaluation["relevant_item_ratio"], 0.0)

    def test_mtime_trailers_and_generic_titles_do_not_pass_as_movies(self) -> None:
        case = CASES["mtime_movie"]
        items = [
            {
                "title": "给阿嬷的情书",
                "url": "https://movie.mtime.com/278420/",
            },
            {
                "title": "link",
                "url": "https://movie.mtime.com/278455/trailer",
            },
            {
                "title": "https://movie.mtime.com/278455/",
                "url": "https://movie.mtime.com/278455/",
            },
            {
                "title": "link",
                "url": "https://movie.mtime.com/272068/trailer",
            },
            {
                "title": "群星闪耀时",
                "url": "https://movie.mtime.com/272068/",
            },
        ]

        evaluation = _evaluate(
            case,
            status="completed",
            results=[
                {
                    "ok": True,
                    "action": "open",
                    "data": {"page": {"url": case.url}},
                },
                {
                    "ok": True,
                    "action": "extract",
                    "data": {"items": items},
                },
            ],
        )

        self.assertFalse(evaluation["checks"]["chinese_title_ratio"])
        self.assertFalse(evaluation["checks"]["content_relevance"])

    def test_failed_terminal_verification_is_not_reported_completed(self) -> None:
        self.assertEqual(
            _terminal_status(
                {
                    "is_complete": True,
                    "verification_result": {"is_success": False},
                }
            ),
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
