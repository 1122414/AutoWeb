"""Run AutoWeb against 20 public Chinese sites in a real headless browser."""

from __future__ import annotations

try:
    from . import benchmark_natural_language_agent as base
except ImportError:  # Direct script execution.
    import benchmark_natural_language_agent as base


def _case(
    key: str,
    name: str,
    category: str,
    url: str,
    subject: str,
    capability: str,
    *,
    required_fields: tuple[tuple[str, ...], ...] = (
        ("title", "name"),
        ("url", "href", "detail_url"),
    ),
    relevant_url_patterns: tuple[str, ...] = (),
    forbidden_url_patterns: tuple[str, ...] = (),
    minimum_title_length: int = 0,
    minimum_chinese_title_ratio: float = 0.8,
) -> base.BenchmarkCase:
    return base.BenchmarkCase(
        key=key,
        name=name,
        url=url,
        task=(
            f"打开 {url}，提取页面主要内容区前5{subject}的标题或名称和对应URL，"
            "得到5条有效、互不重复的中文内容后立即结束任务；不要进入详情页。"
        ),
        expected_min_items=5,
        expected_max_items=5,
        required_field_groups=required_fields,
        anchor_values=(),
        capability=f"{category}｜{capability}",
        anchor_required=False,
        minimum_chinese_title_ratio=minimum_chinese_title_ratio,
        relevant_url_patterns=relevant_url_patterns,
        forbidden_url_patterns=forbidden_url_patterns,
        minimum_relevant_item_ratio=0.8,
        minimum_title_length=minimum_title_length,
    )


CASES = {
    # Four movie sites satisfy the explicit "three or more movie sites" scope.
    "douban_movie": _case(
        "douban_movie",
        "豆瓣电影排行榜",
        "电影",
        "https://movie.douban.com/chart",
        "部电影",
        "排行榜、电影卡片、静态列表",
        relevant_url_patterns=(r"movie\.douban\.com/subject/\d+",),
    ),
    "maoyan_movie": _case(
        "maoyan_movie",
        "猫眼电影",
        "电影",
        "https://www.maoyan.com/films",
        "部电影",
        "电影列表、反自动化边界、动态页面",
        relevant_url_patterns=(r"maoyan\.com/films/\d+",),
    ),
    "mtime_movie": _case(
        "mtime_movie",
        "Mtime时光网",
        "电影",
        "https://film.mtime.com/",
        "部电影",
        "电影门户、混合内容区",
        relevant_url_patterns=(r"movie\.mtime\.com/\d+/?(?:\?.*)?$",),
        forbidden_url_patterns=(r"/(?:trailer|video|photo)(?:/|$)",),
        minimum_title_length=2,
    ),
    "bilibili_movie": _case(
        "bilibili_movie",
        "哔哩哔哩电影",
        "电影",
        "https://www.bilibili.com/movie/",
        "部电影",
        "JavaScript 渲染、视频卡片",
        relevant_url_patterns=(r"bilibili\.com/bangumi/play/",),
    ),
    "sina_news": _case(
        "sina_news",
        "新浪新闻",
        "新闻",
        "https://news.sina.com.cn/",
        "篇新闻",
        "大型门户、多栏目链接",
        minimum_title_length=8,
    ),
    "netease_news": _case(
        "netease_news",
        "网易新闻",
        "新闻",
        "https://news.163.com/",
        "篇新闻",
        "新闻门户、动态推荐",
        minimum_title_length=8,
    ),
    "tencent_news": _case(
        "tencent_news",
        "腾讯新闻",
        "新闻",
        "https://news.qq.com/",
        "篇新闻",
        "JavaScript 新闻流",
        minimum_title_length=8,
    ),
    "sohu_news": _case(
        "sohu_news",
        "搜狐新闻",
        "新闻",
        "https://news.sohu.com/",
        "篇新闻",
        "新闻门户、重复内容区",
        forbidden_url_patterns=(r"/promotion(?:\?|/|$)",),
        minimum_title_length=8,
    ),
    "ifeng_news": _case(
        "ifeng_news",
        "凤凰网资讯",
        "新闻",
        "https://news.ifeng.com/",
        "篇新闻",
        "资讯列表、多区域首页",
        minimum_title_length=8,
    ),
    "people_news": _case(
        "people_news",
        "人民网",
        "新闻",
        "http://www.people.com.cn/",
        "篇新闻",
        "传统门户、静态链接密集页",
        minimum_title_length=8,
    ),
    "xinhua_news": _case(
        "xinhua_news",
        "新华网",
        "新闻",
        "https://www.news.cn/",
        "篇新闻",
        "新闻门户、响应式首页",
        minimum_title_length=8,
    ),
    "cnblogs": _case(
        "cnblogs",
        "博客园",
        "科技社区",
        "https://www.cnblogs.com/",
        "篇文章",
        "开发者文章流",
        relevant_url_patterns=(
            r"cnblogs\.com/.+/p/",
            r"news\.cnblogs\.com/n/",
        ),
        minimum_title_length=8,
    ),
    "36kr": _case(
        "36kr",
        "36氪",
        "科技资讯",
        "https://36kr.com/",
        "篇文章",
        "科技商业资讯、文章卡片",
        relevant_url_patterns=(r"36kr\.com/p/\d+",),
        minimum_title_length=8,
    ),
    "ithome": _case(
        "ithome",
        "IT之家",
        "科技资讯",
        "https://www.ithome.com/",
        "篇资讯",
        "高密度新闻列表",
        minimum_title_length=8,
    ),
    "baidu_hot": _case(
        "baidu_hot",
        "百度热搜",
        "热点",
        "https://top.baidu.com/board?tab=realtime",
        "条热点",
        "动态榜单、排名列表",
        relevant_url_patterns=(r"baidu\.com/s\?",),
        minimum_title_length=4,
    ),
    "weather": _case(
        "weather",
        "中国天气网",
        "生活",
        "https://www.weather.com.cn/",
        "篇天气资讯",
        "天气门户、城市与资讯混合区",
        relevant_url_patterns=(r"weather\.com\.cn/",),
        minimum_title_length=4,
    ),
    "xiachufang": _case(
        "xiachufang",
        "下厨房",
        "生活",
        "https://www.xiachufang.com/",
        "道菜谱",
        "菜谱卡片、图片链接",
        relevant_url_patterns=(r"xiachufang\.com/recipe/",),
    ),
    "douban_book": _case(
        "douban_book",
        "豆瓣读书热门榜",
        "文化",
        "https://book.douban.com/chart",
        "本书",
        "图书榜单、作者与详情链接",
        relevant_url_patterns=(r"book\.douban\.com/subject/\d+",),
    ),
    "gushiwen": _case(
        "gushiwen",
        "古文岛（原古诗文网）",
        "文化",
        "https://www.gushiwen.cn/gushi/tangshi.aspx",
        "首诗文",
        "诗文卡片、中文长文本",
        relevant_url_patterns=(r"gushiwen\.cn/shiwenv_",),
    ),
    "dangdang": _case(
        "dangdang",
        "当当图书畅销榜",
        "电商",
        "https://bang.dangdang.com/books/bestsellers/01.00.00.00.00.00-recent7-0-0-1-1",
        "本书",
        "商品榜单、价格与详情链接",
        relevant_url_patterns=(r"product\.dangdang\.com/\d+",),
        required_fields=(
            ("title", "name"),
            ("url", "href", "detail_url"),
        ),
    ),
}


if __name__ == "__main__":
    base.CASES = CASES
    raise SystemExit(base.main())
