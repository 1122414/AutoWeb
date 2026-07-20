import unittest

from skills.dpcli_crawl_policy import (
    build_detail_batch_action,
    detail_candidate_items,
    goal_requests_detail_batch,
    should_run_detail_batch,
)


EXTRACT_RESULT = {
    "ok": True,
    "action": "extract",
    "data": {
        "items": [
            {"title": "One", "detail_url": "https://example.test/one"},
            {"title": "Two", "url": "https://example.test/two"},
            {"title": "No URL"},
            {"title": "Script", "url": "javascript:"},
            {"title": "Mail", "url": "mailto:hello@example.test"},
            {"title": "Duplicate", "url": "https://example.test/two/"},
        ]
    },
    "error": None,
}


class DPCLICrawlPolicyTests(unittest.TestCase):
    def test_goal_detail_detection(self):
        self.assertTrue(goal_requests_detail_batch("爬取每一部电影的详情简介"))
        self.assertFalse(goal_requests_detail_batch("只爬取列表标题"))

    def test_detail_candidate_items_filters_missing_urls(self):
        items = detail_candidate_items(EXTRACT_RESULT)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "One")
        self.assertNotIn("javascript:", [item.get("url") for item in items])

    def test_should_run_detail_batch(self):
        self.assertTrue(should_run_detail_batch({
            "user_task": "爬取列表并点进去获取详情",
            "dpcli_result": EXTRACT_RESULT,
        }))
        self.assertFalse(should_run_detail_batch({
            "user_task": "爬取列表并点进去获取详情",
            "dpcli_result": EXTRACT_RESULT,
            "dpcli_detail_batch_ran": True,
        }))

    def test_build_detail_batch_action_limits_items(self):
        action = build_detail_batch_action({
            "user_task": "详情",
            "current_url": "https://example.test/list",
            "dpcli_result": EXTRACT_RESULT,
        }, max_items=1)

        self.assertEqual(action["skill"], "batch-detail-extract")
        self.assertEqual(len(action["params"]["items"]), 1)
        self.assertEqual(action["params"]["limit"], 1)
        self.assertEqual(action["params"]["extractor"], "legacy-js")
        self.assertIn("output_file", action["params"])
        self.assertIn("progress_file", action["params"])


if __name__ == "__main__":
    unittest.main()
