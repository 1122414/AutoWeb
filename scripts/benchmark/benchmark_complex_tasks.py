"""Run five public complex-crawl capability cases in a real headless browser."""

from __future__ import annotations

try:
    from . import benchmark_natural_language_agent as base
except ImportError:  # Direct script execution.
    import benchmark_natural_language_agent as base


CASES = {
    "products_three_pages": base.BenchmarkCase(
        key="products_three_pages",
        name="web-scraping.dev - three-page products",
        url="https://web-scraping.dev/products",
        task=(
            "打开 https://web-scraping.dev/products，连续抓取前3页，每页5个商品的"
            "名称、价格和对应URL，总计15条互不重复的数据后结束。"
        ),
        expected_min_items=15,
        expected_max_items=15,
        required_field_groups=(
            ("title", "name"),
            ("price",),
            ("url", "href", "detail_url"),
        ),
        anchor_values=(),
        anchor_required=False,
        capability="多页翻页｜三页累计、字段验证、去重",
    ),
    "quotes_infinite_scroll": base.BenchmarkCase(
        key="quotes_infinite_scroll",
        name="Quotes to Scrape - infinite scroll",
        url="https://quotes.toscrape.com/scroll",
        task=(
            "打开 https://quotes.toscrape.com/scroll，持续向下滚动加载，最多滚动4轮，"
            "提取20条名言的正文和作者；累计20条互不重复的数据后结束。"
        ),
        expected_min_items=20,
        expected_max_items=20,
        required_field_groups=(
            ("text", "quote", "content", "title"),
            ("author", "name"),
        ),
        anchor_values=(),
        anchor_required=False,
        capability="无限滚动｜动态加载、增量去重、停滞上限",
    ),
    "books_list_detail": base.BenchmarkCase(
        key="books_list_detail",
        name="Books to Scrape - list plus details",
        url="https://books.toscrape.com/",
        task=(
            "打开 https://books.toscrape.com/，提取当前页前5本书的标题和对应URL，"
            "然后进入每本书详情页提取产品描述；5本书的列表与详情字段完整后结束。"
        ),
        expected_min_items=5,
        expected_max_items=5,
        required_field_groups=(
            ("title", "name"),
            ("url", "href", "detail_url", "final_url"),
            ("description", "summary"),
        ),
        anchor_values=(),
        anchor_required=False,
        allow_detail_batch=True,
        capability="列表进入详情｜累计URL、批量详情、详情字段回并",
    ),
    "hockey_filter_two_pages": base.BenchmarkCase(
        key="hockey_filter_two_pages",
        name="Scrape This Site - filter then paginate",
        url="https://www.scrapethissite.com/pages/forms/",
        task=(
            "打开 https://www.scrapethissite.com/pages/forms/，在搜索框筛选关键词“a”，"
            "提交筛选后抓取前2页，每页10行球队数据；字段包括球队名称、年份、"
            "胜场和负场，累计20行后结束。"
        ),
        expected_min_items=20,
        expected_max_items=20,
        required_field_groups=(
            ("team", "team_name", "name", "title"),
            ("year",),
            ("wins", "win"),
            ("losses", "loss"),
        ),
        anchor_values=(),
        anchor_required=False,
        capability="筛选后翻页｜文本提交、筛选状态、两页累计",
    ),
    "products_restart_resume": base.BenchmarkCase(
        key="products_restart_resume",
        name="web-scraping.dev - restart after page one",
        url="https://web-scraping.dev/products",
        task=(
            "打开 https://web-scraping.dev/products，连续抓取前3页，每页5个商品的"
            "名称、价格和对应URL；执行中断后从已完成页继续，总计15条且不重复。"
        ),
        expected_min_items=15,
        expected_max_items=15,
        required_field_groups=(
            ("title", "name"),
            ("price",),
            ("url", "href", "detail_url"),
        ),
        anchor_values=(),
        anchor_required=False,
        restart_after_pages=1,
        capability="中断恢复｜进度JSON往返、新线程续跑、CLI会话复用",
    ),
}


if __name__ == "__main__":
    base.CASES = CASES
    raise SystemExit(base.main())
