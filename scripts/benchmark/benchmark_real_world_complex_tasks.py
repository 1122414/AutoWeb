"""Run 20 complex public-site crawl tasks in a real headless browser."""

from __future__ import annotations

try:
    from . import benchmark_natural_language_agent as base
except ImportError:
    import benchmark_natural_language_agent as base


def _case(
    key: str,
    name: str,
    url: str,
    task: str,
    count: int,
    capability: str,
    *,
    required_fields: tuple[tuple[str, ...], ...] = (
        ("title", "name"),
        ("url", "href", "detail_url"),
    ),
    relevant_url_patterns: tuple[str, ...] = (),
    forbidden_url_patterns: tuple[str, ...] = (),
    minimum_chinese_title_ratio: float = 0.5,
    minimum_title_length: int = 2,
) -> base.BenchmarkCase:
    return base.BenchmarkCase(
        key=key,
        name=name,
        url=url,
        task=task,
        expected_min_items=count,
        expected_max_items=count,
        required_field_groups=required_fields,
        anchor_values=(),
        capability=capability,
        anchor_required=False,
        minimum_chinese_title_ratio=minimum_chinese_title_ratio,
        relevant_url_patterns=relevant_url_patterns,
        forbidden_url_patterns=forbidden_url_patterns,
        minimum_relevant_item_ratio=0.8,
        minimum_title_length=minimum_title_length,
    )


CASES = {
    "iiice_resources": _case(
        "iiice_resources", "下次一定 - 宝藏资源混合首页", "https://www.iiice.cn/",
        "打开 https://www.iiice.cn/，等待SPA首页完整渲染；跳过页头导航、QQ群、广告和页脚，从主要资源卡片区提取前12个不同资源，字段固定为 title、url。只保存公开页面元数据，不访问外链；得到12条后结束。",
        12, "SPA资源门户｜多分区、外链卡片、广告与导航干扰",
        minimum_chinese_title_ratio=0.4,
    ),
    "guozhi_lab": _case(
        "guozhi_lab", "果汁实验室 - 高密度网站目录", "http://guozhivip.com/lab/",
        "打开 http://guozhivip.com/lab/，在主要网站推荐网格中提取前7个不同条目，排除顶部导航、热榜菜单、联系入口和页脚；字段固定为 title、description、url。不要进入外部网站，得到7条有效数据后结束。",
        7, "真实导航站｜密集链接、图标卡片、相邻栏目易混淆",
    ),
    "wangfei_catalog": _case(
        "wangfei_catalog", "网飞啦 - 影视混合目录", "https://www.wangfei.la/",
        "打开 https://www.wangfei.la/，只从首页影视内容卡片区提取前10部不同影视条目，排除导航、热搜标签、福利入口、用户功能及播放链接；字段固定为 title、url。只采集目录元数据，不点击播放、不下载、不使用搜索，得到10条后结束。",
        10, "影视门户｜多频道混排、更新角标、播放入口干扰",
        relevant_url_patterns=(r"wangfei\.la/vod-detail-id-\d+\.html",),
        forbidden_url_patterns=(r"wangfei\.la/vod-play-id-",),
        minimum_chinese_title_ratio=0.3,
    ),
    "douban_movie_chart": _case(
        "douban_movie_chart", "豆瓣电影排行榜", "https://movie.douban.com/chart",
        "打开 https://movie.douban.com/chart，从电影排行榜主体列表提取前10部不同电影，排除影人、分类导航和榜单说明；字段固定为 title、url。得到10条后结束，不进入详情页。",
        10, "电影榜单｜海报、标题、评分、人物链接混排",
        relevant_url_patterns=(r"movie\.douban\.com/subject/\d+",),
    ),
    "douban_top250": _case(
        "douban_top250", "豆瓣电影 Top 250", "https://movie.douban.com/top250",
        "打开 https://movie.douban.com/top250，从 Top 250 榜单主体严格提取当前页排名前10部不同电影，排除影人链接、短评入口、分类导航、页码和榜单说明；字段固定为 title、url。得到10条后结束，不翻页、不进入详情页。",
        10, "经典电影榜单｜排名、别名、演职员、评分、短评和分页密集混排",
        relevant_url_patterns=(r"movie\.douban\.com/subject/\d+",),
    ),
    "maoyan_films": _case(
        "maoyan_films", "猫眼电影 - 影片列表", "https://www.maoyan.com/films",
        "打开 https://www.maoyan.com/films，等待影片列表渲染，从影片主体网格提取前10部电影，排除导航、广告、分页数字和购票按钮；字段固定为 title、score、url。只采集公开目录元数据，得到10条后结束。",
        10, "商业电影站｜动态卡片、票房与购票元素干扰",
        relevant_url_patterns=(r"maoyan\.com/films/\d+",),
    ),
    "mtime_films": _case(
        "mtime_films", "Mtime时光网 - 电影门户", "https://film.mtime.com/",
        "打开 https://film.mtime.com/，从主要电影推荐或热映列表中提取前10部不同电影，排除预告片、视频、图片和栏目导航；字段固定为 title、url。得到10条后结束。",
        10, "电影门户｜推荐流、视频入口、图片链接混杂",
        relevant_url_patterns=(r"movie\.mtime\.com/\d+/?(?:\?.*)?$",),
        forbidden_url_patterns=(r"/(?:trailer|video|photo)(?:/|$)",),
    ),
    "bilibili_movies": _case(
        "bilibili_movies", "哔哩哔哩电影频道", "https://www.bilibili.com/movie/",
        "打开 https://www.bilibili.com/movie/，等待JavaScript内容加载，从电影内容卡片区提取前10部不同电影，排除番剧导航、排行榜序号、按钮和会员促销；字段固定为 title、url。不播放视频，得到10条后结束。",
        10, "视频平台｜JavaScript渲染、徽标、会员与导航干扰",
        relevant_url_patterns=(r"bilibili\.com/bangumi/play/",),
    ),
    "iqiyi_movies": _case(
        "iqiyi_movies", "爱奇艺电影频道", "https://www.iqiyi.com/dianying/",
        "打开 https://www.iqiyi.com/dianying/，等待动态电影频道加载，从主要电影卡片区提取前10部不同电影，排除频道导航、明星、广告和VIP按钮；字段固定为 title、url。只采目录元数据，不播放，得到10条后结束。",
        10, "视频平台｜多楼层异步卡片、VIP与明星链接干扰",
        relevant_url_patterns=(r"iqiyi\.com/v_", r"iqiyi\.com/a_"),
    ),
    "tencent_movies": _case(
        "tencent_movies", "腾讯视频电影频道", "https://v.qq.com/channel/movie",
        "打开 https://v.qq.com/channel/movie，等待电影频道动态内容加载，从电影卡片区提取前10部不同电影，排除顶部导航、短视频、人物和运营按钮；字段固定为 title、url。不播放视频，得到10条后结束。",
        10, "视频平台｜异步推荐流、人物与视频链接共存",
        relevant_url_patterns=(r"v\.qq\.com/x/cover/",),
    ),
    "youku_movies": _case(
        "youku_movies", "优酷电影频道", "https://www.youku.com/ku/webmovie",
        "打开 https://www.youku.com/ku/webmovie，等待电影频道渲染，从主要电影内容卡片中提取前10部不同电影，排除导航、综艺、人物、广告和会员按钮；字段固定为 title、url。不播放视频，得到10条后结束。",
        10, "视频平台｜多频道混排、异步图片卡片、运营位干扰",
        relevant_url_patterns=(r"v\.youku\.com/video", r"youku\.com/v_show/", r"youku\.com/v_nextstage/"),
    ),
    "apple_iphones": _case(
        "apple_iphones", "Apple中国 - iPhone产品页", "https://www.apple.com.cn/iphone/",
        "打开 https://www.apple.com.cn/iphone/，从主要 iPhone 产品选择区提取前5个不同入口（1个选购总入口和4个当前机型），排除全局导航、以旧换新说明、配件、服务、比较按钮和页脚；字段固定为 title、url。得到5条后结束，不进入购买或结账。",
        5, "品牌电商｜产品卡、价格区间、服务说明和CTA混排",
        relevant_url_patterns=(r"apple\.com\.cn/iphone-[^/?#]+/?$", r"apple\.com\.cn/cn/shop/goto/buy_iphone"),
        minimum_chinese_title_ratio=0.0,
    ),
    "vmall_products": _case(
        "vmall_products", "华为商城 - 首页商品流", "https://www.vmall.com/",
        "打开 https://www.vmall.com/，等待商城首页商品区加载，从主要商品卡片中提取前10件不同商品，排除类目导航、轮播广告、权益、门店和服务入口；字段固定为 title、price、url。得到10条后结束，不加入购物车。",
        10, "大型电商｜异步商品楼层、促销标签、导航与轮播干扰",
        required_fields=(("title", "name"), ("price",), ("url", "href", "detail_url")),
        minimum_chinese_title_ratio=0.3,
    ),
    "suning_products": _case(
        "suning_products", "苏宁易购 - 首页商品推荐", "https://www.suning.com/",
        "打开 https://www.suning.com/，从首页主要商品推荐区提取前10件不同商品，排除账户、订单、类目导航、品牌文字链接和轮播广告；字段固定为 title、price、url。得到10条后结束，不登录、不加入购物车。",
        10, "综合电商｜高密度导航、广告位、商品楼层和价格标签",
        relevant_url_patterns=(r"product\.suning\.com/",),
        required_fields=(("title", "name"), ("price",), ("url", "href", "detail_url")),
    ),
    "dangdang_bestsellers": _case(
        "dangdang_bestsellers", "当当图书畅销榜", "https://bang.dangdang.com/books/bestsellers/01.00.00.00.00.00-recent7-0-0-1-1",
        "打开 https://bang.dangdang.com/books/bestsellers/01.00.00.00.00.00-recent7-0-0-1-1，从榜单主体提取排名前10本书，排除榜单分类、作者链接、出版社链接和广告；字段固定为 title、price、url。得到10条后结束，不进入详情页。",
        10, "电商榜单｜排名、书名、作者、价格与促销信息密集",
        relevant_url_patterns=(r"product\.dangdang\.com/\d+",),
        required_fields=(("title", "name"), ("price",), ("url", "href", "detail_url")),
    ),
    "honor_phones": _case(
        "honor_phones", "荣耀官网 - 手机产品矩阵", "https://www.honor.com/cn/phones/",
        "打开 https://www.honor.com/cn/phones/，从手机产品矩阵中提取前8个不同机型，排除全局导航、国补入口、产品比较、配件和页脚；字段固定为 title、description、url。得到8条后结束，不进入购买流程。",
        8, "品牌商城｜产品矩阵、系列分组、New标签与比较入口",
        relevant_url_patterns=(r"honor\.com/cn/phones/[^#?]+/",),
        minimum_chinese_title_ratio=0.3,
    ),
    "ikea_products": _case(
        "ikea_products", "宜家中国 - 沙发产品目录", "https://www.ikea.cn/cn/zh/cat/sha-fa-fu003/",
        "打开 https://www.ikea.cn/cn/zh/cat/sha-fa-fu003/，从沙发商品网格提取前10件不同商品，排除品类导航、灵感内容、服务、筛选按钮、商品对比、促销横幅和页脚；字段固定为 title、price、url。得到10条后结束，不登录、不加入购物袋。",
        10, "大型零售目录｜品类树、筛选、灵感内容、促销与商品卡片混排",
        relevant_url_patterns=(r"ikea\.cn/cn/zh/p/",),
        required_fields=(("title", "name"), ("price",), ("url", "href", "detail_url")),
        minimum_chinese_title_ratio=0.1,
    ),
    "autohome_cars": _case(
        "autohome_cars", "汽车之家 - 车型内容首页", "https://www.autohome.com.cn/",
        "打开 https://www.autohome.com.cn/，从主要车型或新车推荐内容区提取前10个不同车型条目，排除新闻、论坛、工具、城市选择和品牌字母导航；字段固定为 title、price、url。得到10条后结束。",
        10, "汽车商业门户｜车型、资讯、工具和品牌导航高度混杂",
        minimum_chinese_title_ratio=0.2,
    ),
    "baidu_hot_top10": _case(
        "baidu_hot_top10", "百度热搜实时榜", "https://top.baidu.com/board?tab=realtime",
        "打开 https://top.baidu.com/board?tab=realtime，从实时榜主体严格提取排名前10条，排除其他榜单标签、顶部导航、推荐词和页脚；字段固定为 title、url。得到10条后结束。",
        10, "实时热榜｜排名、热度、摘要和搜索链接密集",
        relevant_url_patterns=(r"baidu\.com/s\?",),
    ),
    "baidu_movie_top10": _case(
        "baidu_movie_top10", "百度热搜电影榜", "https://top.baidu.com/board?tab=movie",
        "打开 https://top.baidu.com/board?tab=movie，从电影榜主体严格提取排名前10部电影，排除实时榜等其他榜单标签、顶部导航、推荐词、摘要内链接和页脚；字段固定为 title、url。得到10条后结束。",
        10, "电影热榜｜排名、热度、剧情摘要、人物与搜索链接密集混排",
        relevant_url_patterns=(r"baidu\.com/s\?",),
    ),
    "ithome_news": _case(
        "ithome_news", "IT之家 - 高密度科技资讯", "https://www.ithome.com/",
        "打开 https://www.ithome.com/，从首页主要新闻列表中提取前10篇不同资讯，排除产品导航、专题、下载、排行榜和页脚重复链接；字段固定为 title、url。标题至少8个字符，得到10条后结束。",
        10, "科技门户｜多栏新闻、排行榜、专题与重复链接",
        relevant_url_patterns=(r"ithome\.com/0/\d+/\d+\.htm",),
        minimum_title_length=8,
    ),
    "douban_books": _case(
        "douban_books", "豆瓣读书排行榜", "https://book.douban.com/chart",
        "打开 https://book.douban.com/chart，从图书排行榜主体列表提取前10本不同图书，排除作者详情链接、分类导航、标签和榜单说明；字段固定为 title、url。得到10条后结束。",
        10, "图书榜单｜封面、书名、作者、评分和标签混排",
        relevant_url_patterns=(r"book\.douban\.com/subject/\d+",),
    ),
    "kr36_news": _case(
        "kr36_news", "36氪 - 商业科技混合内容流", "https://36kr.com/",
        "打开 https://36kr.com/，等待首页内容流渲染，从主内容卡片区提取前10条不同内容，保留普通图文文章、专题和原创视频三类内容；排除快讯导航、作者主页、企业服务、外链推广和广告。字段固定为 title、summary、url，后续按 URL 中的 /p/、/topics/、/v-video/ 区分内容类型。标题至少8个字符，得到10条后结束。",
        10, "商业资讯｜文章、专题、视频、快讯与广告异步混排",
        relevant_url_patterns=(r"36kr\.com/(?:p|topics|v-video)/\d+",),
        minimum_title_length=8,
    ),
    "oschina_news": _case(
        "oschina_news", "开源中国 - 高密度资讯流", "https://www.oschina.net/news",
        "打开 https://www.oschina.net/news，从页面主要资讯流提取前10篇不同新闻，排除软件分类、作者主页、标签、评论入口、右侧排行和广告；字段固定为 title、url。标题至少8个字符，得到10条后结束，不进入登录流程。",
        10, "技术资讯门户｜正文流、排行、标签、作者和软件入口密集混排",
        relevant_url_patterns=(r"oschina\.net/news/\d+",),
        minimum_title_length=8,
    ),
    "sina_tech_news": _case(
        "sina_tech_news", "新浪科技移动版 - 资讯流", "https://tech.sina.cn/",
        "打开 https://tech.sina.cn/，从页面主要科技资讯流提取前10篇不同文章，排除频道导航、股票行情、专题、视频入口、排行和广告；字段固定为 title、url。标题至少8个字符，得到10条后结束。",
        10, "大型资讯门户｜多栏新闻、行情、专题、视频和广告密集混排",
        relevant_url_patterns=(r"(?:tech|finance)\.sina\.cn/.+/doc-[^/?]+\.d\.html", r"sina\.cn/.+/doc-[^/?]+\.d\.html"),
        minimum_title_length=8,
    ),
    "gome_products": _case(
        "gome_products", "国美 - 商品分类与推荐", "https://list.gome.com.cn/",
        "打开 https://list.gome.com.cn/，从主要商品推荐或排行榜卡片区提取前10件不同商品，排除全部分类菜单、品牌导航、促销会场、帮助入口和广告；字段固定为 title、url。得到10条后结束，不登录、不加入购物车。",
        10, "综合电商｜分类树、品牌、促销会场、排行与商品卡片混排",
        relevant_url_patterns=(r"item\.gome\.com\.cn/", r"gome\.com\.cn/product-"),
        minimum_chinese_title_ratio=0.2,
    ),
    "iqiyi_movie_ranking": _case(
        "iqiyi_movie_ranking", "爱奇艺电影排行榜", "https://www.iqiyi.com/common/ranking_mv.html",
        "打开 https://www.iqiyi.com/common/ranking_mv.html，从电影排行榜主体提取前10部不同电影，排除电视剧、片花、动漫等频道标签、广告、游戏、页脚与合作入口；字段固定为 title、url。只采目录元数据，不播放，得到10条后结束。",
        10, "视频排行榜｜电影与其他内容标签、广告和播放入口混排",
        relevant_url_patterns=(r"iqiyi\.com/(?:v_|a_)", r"iqiyi\.com/tvg/to_page_url"),
        minimum_chinese_title_ratio=0.3,
    ),
    "steam_top_sellers": _case(
        "steam_top_sellers", "Steam 商店 - 热销商品", "https://store.steampowered.com/search/?filter=topsellers&l=schinese",
        "打开 https://store.steampowered.com/search/?filter=topsellers&l=schinese，从热销商品结果列表提取前10款不同游戏，排除顶部导航、标签、发行商链接、促销横幅、分页与推荐词；字段固定为 title、url。得到10条后结束，不登录、不加入购物车。",
        10, "国际游戏电商｜折扣、原价现价、平台图标、标签与商品卡片混排",
        relevant_url_patterns=(r"store\.steampowered\.com/app/\d+",),
        minimum_chinese_title_ratio=0.0,
    ),
    "github_trending": _case(
        "github_trending", "GitHub Trending - 热门仓库", "https://github.com/trending",
        "打开 https://github.com/trending，从 Trending 仓库主体列表提取前10个不同仓库，排除全站导航、语言筛选、开发者入口、登录按钮和页脚；字段固定为 title、url。得到10条后结束，不登录、不进入仓库详情。",
        10, "代码托管榜单｜仓库、作者、语言、星标、Fork 与导航链接混排",
        relevant_url_patterns=(r"github\.com/[^/]+/[^/?#]+$",),
        minimum_chinese_title_ratio=0.0,
    ),
    "github_trending_developers": _case(
        "github_trending_developers", "GitHub Trending Developers - 开发者榜", "https://github.com/trending/developers",
        "打开 https://github.com/trending/developers，从 Trending Developers 主体榜单卡片提取前10个不同的开发者主页或其热门仓库链接，排除全站导航、语言筛选、登录按钮和页脚；字段固定为 title、url。得到10条后结束，不登录、不进入个人或仓库详情。",
        10, "开发者榜单｜用户、热门仓库、语言筛选、关注按钮与导航链接混排",
        relevant_url_patterns=(r"github\.com/[^/?#]+$", r"github\.com/[^/?#]+/[^/?#]+$"),
        minimum_chinese_title_ratio=0.0,
    ),
}


if __name__ == "__main__":
    base.CASES = CASES
    raise SystemExit(base.main())
