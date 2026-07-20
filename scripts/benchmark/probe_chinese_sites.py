"""Probe public Chinese sites before running the browser benchmark.

The probe is deliberately read-only: one robots.txt request and one page GET
per domain. Sites explicitly disallowed by robots.txt are excluded from the
browser benchmark.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "AutoWeb-Public-Benchmark/1.0"
BLOCK_PATTERNS = {
    "captcha": re.compile(r"验证码|安全验证|captcha|verify you are human", re.I),
    "login_wall": re.compile(r"登录后.{0,12}(?:查看|继续|访问)|login required", re.I),
    "rate_limit": re.compile(r"请求过于频繁|访问频繁|too many requests|rate limit", re.I),
    "access_denied": re.compile(r"访问被拒绝|access denied|forbidden", re.I),
}


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    category: str
    url: str


CANDIDATES = (
    Candidate("douban_movie", "豆瓣电影", "电影", "https://movie.douban.com/chart"),
    Candidate("maoyan_movie", "猫眼电影", "电影", "https://www.maoyan.com/films"),
    Candidate("movie_1905", "1905电影网", "电影", "https://www.1905.com/mdb/film/"),
    Candidate("mtime_movie", "Mtime时光网", "电影", "https://www.mtime.com/"),
    Candidate("bilibili_movie", "哔哩哔哩电影", "电影", "https://www.bilibili.com/movie/"),
    Candidate("sina_news", "新浪新闻", "新闻", "https://news.sina.com.cn/"),
    Candidate("netease_news", "网易新闻", "新闻", "https://news.163.com/"),
    Candidate("tencent_news", "腾讯新闻", "新闻", "https://news.qq.com/"),
    Candidate("sohu_news", "搜狐新闻", "新闻", "https://news.sohu.com/"),
    Candidate("ifeng_news", "凤凰网资讯", "新闻", "https://news.ifeng.com/"),
    Candidate("people_news", "人民网", "新闻", "http://www.people.com.cn/"),
    Candidate("xinhua_news", "新华网", "新闻", "http://www.news.cn/"),
    Candidate("csdn", "CSDN", "科技社区", "https://www.csdn.net/"),
    Candidate("cnblogs", "博客园", "科技社区", "https://www.cnblogs.com/"),
    Candidate("oschina", "开源中国", "科技社区", "https://www.oschina.net/news"),
    Candidate("36kr", "36氪", "科技资讯", "https://36kr.com/"),
    Candidate("juejin", "稀土掘金", "科技社区", "https://juejin.cn/"),
    Candidate("ithome", "IT之家", "科技资讯", "https://www.ithome.com/"),
    Candidate(
        "baidu_hot",
        "百度热搜",
        "热点",
        "https://top.baidu.com/board?tab=realtime",
    ),
    Candidate("weather", "中国天气网", "生活", "https://www.weather.com.cn/"),
    Candidate("xiachufang", "下厨房", "生活", "https://www.xiachufang.com/"),
    Candidate("smzdm", "什么值得买", "消费", "https://www.smzdm.com/"),
    Candidate("douban_book", "豆瓣读书", "文化", "https://book.douban.com/chart"),
    Candidate("gushiwen", "古诗文网", "文化", "https://www.gushiwen.cn/"),
    Candidate("autohome", "汽车之家", "汽车", "https://www.autohome.com.cn/"),
    Candidate("dangdang", "当当网", "电商", "https://www.dangdang.com/"),
)


def _decode_body(body: bytes) -> str:
    candidates = []
    for encoding in ("utf-8", "gb18030"):
        try:
            text = body.decode(encoding)
            replacements = 0
        except UnicodeDecodeError:
            text = body.decode(encoding, errors="replace")
            replacements = text.count("\ufffd")
        candidates.append((replacements, text))
    return min(candidates, key=lambda item: item[0])[1]


def _curl_get(url: str, timeout: float) -> dict:
    executable = shutil.which("curl.exe") or shutil.which("curl")
    if not executable:
        return {
            "status": None,
            "final_url": "",
            "text": "",
            "content_length": 0,
            "error": "curl executable not found",
            "transport": "curl",
        }
    marker = b"\n__AUTOWEB_CURL_META__"
    try:
        completed = subprocess.run(
            [
                executable,
                "--http1.1",
                "-L",
                "--compressed",
                "--max-time",
                str(max(1, int(timeout))),
                "-A",
                USER_AGENT,
                "-sS",
                "-w",
                "\n__AUTOWEB_CURL_META__%{http_code}\t%{url_effective}",
                url,
            ],
            capture_output=True,
            timeout=timeout + 5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "status": None,
            "final_url": "",
            "text": "",
            "content_length": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "transport": "curl",
        }
    body, separator, metadata = completed.stdout.rpartition(marker)
    if not separator:
        body = completed.stdout
        metadata = b""
    status_text, _, final_url = metadata.partition(b"\t")
    try:
        status = int(status_text)
    except ValueError:
        status = None
    error = (
        completed.stderr.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0
        else None
    )
    return {
        "status": status,
        "final_url": final_url.decode("utf-8", errors="replace").strip(),
        "text": _decode_body(body),
        "content_length": len(body),
        "error": error,
        "transport": "curl",
    }


def _get(url: str, timeout: float) -> dict:
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            },
            timeout=timeout,
            allow_redirects=True,
        )
        response.encoding = response.apparent_encoding or response.encoding
        return {
            "status": response.status_code,
            "final_url": response.url,
            "text": response.text,
            "content_length": len(response.content),
            "error": None,
            "transport": "requests",
        }
    except requests.RequestException as exc:
        fallback = _curl_get(url, timeout)
        if fallback["error"]:
            fallback["error"] = (
                f"requests={type(exc).__name__}: {exc}; "
                f"curl={fallback['error']}"
            )
        return fallback


def _robots(candidate: Candidate, timeout: float) -> dict:
    parsed = urlparse(candidate.url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    response = _get(robots_url, timeout)

    if response["status"] == 404:
        allowed = True
    elif response["status"] == 200:
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response["text"].splitlines())
        allowed = parser.can_fetch(USER_AGENT, candidate.url)
    else:
        allowed = None
    return {
        "url": robots_url,
        "status": response["status"],
        "allowed": allowed,
        "error": response["error"],
        "transport": response["transport"],
    }


def _title(markup: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.I | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:200]


def probe(candidate: Candidate, timeout: float) -> dict:
    robots = _robots(candidate, timeout)
    page: dict = {
        "status": None,
        "final_url": "",
        "title": "",
        "content_length": 0,
        "block_signal": None,
        "error": None,
    }
    if robots["allowed"] is False:
        return {
            **asdict(candidate),
            "robots": robots,
            "page": page,
            "eligible": False,
            "reason": "robots_disallow",
        }

    response = _get(candidate.url, timeout)
    if response["status"] is not None:
        text = response["text"][:500_000]
        block_signal = next(
            (name for name, pattern in BLOCK_PATTERNS.items() if pattern.search(text)),
            None,
        )
        page.update(
            {
                "status": response["status"],
                "final_url": response["final_url"],
                "title": _title(text),
                "content_length": response["content_length"],
                "block_signal": block_signal,
                "transport": response["transport"],
            }
        )
    page["error"] = response["error"]

    eligible = (
        robots["allowed"] is True
        and page["status"] == 200
        and page["content_length"] >= 500
        and page["block_signal"] is None
    )
    reason = "eligible" if eligible else (
        "robots_unknown"
        if robots["allowed"] is None
        else page["block_signal"]
        or f"http_{page['status']}"
        if page["status"] is not None
        else "request_error"
    )
    return {
        **asdict(candidate),
        "robots": robots,
        "page": page,
        "eligible": eligible,
        "reason": reason,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--output",
        default="output/benchmarks/chinese_sites_probe.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = (PROJECT_ROOT / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        futures = {
            pool.submit(probe, candidate, args.timeout): candidate
            for candidate in CANDIDATES
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "key": result["key"],
                        "eligible": result["eligible"],
                        "reason": result["reason"],
                        "status": result["page"]["status"],
                        "robots": result["robots"]["allowed"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    order = {candidate.key: index for index, candidate in enumerate(CANDIDATES)}
    results.sort(key=lambda item: order[item["key"]])
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "user_agent": USER_AGENT,
        "candidate_count": len(results),
        "eligible_count": sum(bool(item["eligible"]) for item in results),
        "results": results,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Probe result: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
