"""Probe real commercial and media sites before complex browser crawls."""

from __future__ import annotations

try:
    from . import probe_chinese_sites as base
except ImportError:
    import probe_chinese_sites as base


base.CANDIDATES = (
    base.Candidate("iiice", "下次一定", "资源导航", "https://www.iiice.cn/"),
    base.Candidate("guozhi_lab", "果汁实验室", "网站导航", "http://guozhivip.com/lab/"),
    base.Candidate("wangfei", "网飞啦", "影视目录", "https://www.wangfei.la/"),
    base.Candidate("douban_movie", "豆瓣电影", "电影", "https://movie.douban.com/chart"),
    base.Candidate("maoyan_movie", "猫眼电影", "电影", "https://www.maoyan.com/films"),
    base.Candidate("mtime_movie", "Mtime时光网", "电影", "https://film.mtime.com/"),
    base.Candidate("bilibili_movie", "哔哩哔哩电影", "电影", "https://www.bilibili.com/movie/"),
    base.Candidate("movie_1905", "1905电影网", "电影", "https://www.1905.com/mdb/film/"),
    base.Candidate("iqiyi_movie", "爱奇艺电影", "电影", "https://www.iqiyi.com/dianying/"),
    base.Candidate("youku_movie", "优酷电影", "电影", "https://movie.youku.com/"),
    base.Candidate("tencent_movie", "腾讯视频电影", "电影", "https://v.qq.com/channel/movie"),
    base.Candidate("apple_iphone", "Apple中国iPhone", "电商", "https://www.apple.com.cn/shop/buy-iphone"),
    base.Candidate("xiaomi_shop", "小米商城", "电商", "https://www.mi.com/shop"),
    base.Candidate("vmall", "华为商城", "电商", "https://www.vmall.com/"),
    base.Candidate("lenovo_shop", "联想商城", "电商", "https://shop.lenovo.com.cn/"),
    base.Candidate("suning", "苏宁易购", "电商", "https://www.suning.com/"),
    base.Candidate("ikea", "宜家中国", "电商", "https://www.ikea.cn/cn/zh/cat/products-products/"),
    base.Candidate("oppo", "OPPO手机", "电商", "https://www.oppo.com/cn/smartphones/"),
    base.Candidate("vivo", "vivo产品", "电商", "https://www.vivo.com.cn/product"),
    base.Candidate("honor", "荣耀手机", "电商", "https://www.honor.com/cn/phones/"),
    base.Candidate("dangdang", "当当畅销榜", "电商", "https://bang.dangdang.com/books/bestsellers/01.00.00.00.00.00-recent7-0-0-1-1"),
    base.Candidate("autohome", "汽车之家", "汽车", "https://www.autohome.com.cn/"),
    base.Candidate("smzdm", "什么值得买", "消费", "https://www.smzdm.com/"),
    base.Candidate("baidu_hot", "百度热搜", "热点", "https://top.baidu.com/board?tab=realtime"),
    base.Candidate("36kr", "36氪", "科技资讯", "https://36kr.com/"),
    base.Candidate("ithome", "IT之家", "科技资讯", "https://www.ithome.com/"),
    base.Candidate("oschina", "开源中国", "科技资讯", "https://www.oschina.net/news"),
    base.Candidate("douban_book", "豆瓣读书", "图书", "https://book.douban.com/chart"),
    base.Candidate("sina_tech", "新浪科技", "科技资讯", "https://tech.sina.com.cn/"),
    base.Candidate("gome_list", "国美商品分类", "电商", "https://list.gome.com.cn/"),
    base.Candidate("iqiyi_ranking", "爱奇艺电影排行榜", "电影", "https://www.iqiyi.com/common/ranking_mv.html"),
)


if __name__ == "__main__":
    raise SystemExit(base.main())
