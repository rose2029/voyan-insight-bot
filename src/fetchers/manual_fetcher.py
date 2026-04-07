"""
手动URL抓取器 - 支持用户粘贴任意URL
"""
import time
from typing import List, Optional
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .base import BaseFetcher, NewsArticle


class ManualURLFetcher(BaseFetcher):
    """手动URL抓取器"""

    def fetch(self, source_config: dict) -> List[NewsArticle]:
        """source_config中应包含 'urls' 列表"""
        urls = source_config.get("urls", [])
        articles = []

        for url in urls:
            try:
                article = self._fetch_single_url(url)
                if article:
                    articles.append(article)
                time.sleep(self.request_interval)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"[MANUAL] 抓取失败: {url} - {e}")

        return articles

    def _fetch_single_url(self, url: str) -> Optional[NewsArticle]:
        """抓取单个URL"""
        session = requests.Session()
        session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

        resp = session.get(url, timeout=self.timeout)
        resp.raise_for_status()

        # 检测编码
        encoding = resp.encoding
        if not encoding or encoding.lower() in ("iso-8859-1",):
            encoding = "utf-8"

        soup = BeautifulSoup(resp.content.decode(encoding, errors="replace"), "lxml")

        # 提取标题
        title = ""
        og_title = soup.find("meta", property="og:title")
        if og_title:
            title = og_title.get("content", "")
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        if not title and soup.title:
            title = soup.title.get_text(strip=True)

        # 提取正文
        content = self._extract_main_content(soup)

        # 提取时间
        publish_time = self._extract_time(soup)

        # 提取来源
        source = "手动添加"
        og_site = soup.find("meta", property="og:site_name")
        if og_site:
            source = og_site.get("content", "手动添加")

        return NewsArticle(
            title=title.strip(),
            url=url,
            source=source,
            publish_time=publish_time,
            content=content[:500],
            full_content=content,
        )

    @staticmethod
    def _extract_main_content(soup: BeautifulSoup) -> str:
        """提取正文内容"""
        # 移除无关标签
        for tag in soup.find_all(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        # 尝试常见正文容器
        selectors = [
            "article", ".article-content", ".content", "#content",
            ".article-body", ".post-content", ".news-content",
            ".detail-content", ".rich_media_content",  # 微信公众号
        ]
        for sel in selectors:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 100:
                    return text

        # 备用：提取所有p标签
        paragraphs = soup.find_all("p")
        if paragraphs:
            return "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

        body = soup.find("body")
        if body:
            return body.get_text(separator="\n", strip=True)[:3000]
        return ""

    @staticmethod
    def _extract_time(soup: BeautifulSoup) -> Optional[datetime]:
        """提取发布时间"""
        for attr in ["article:published_time", "publish-date", "pubdate"]:
            meta = soup.find("meta", property=attr)
            if not meta:
                meta = soup.find("meta", attrs={"name": attr})
            if meta:
                time_str = meta.get("content", "")[:19]
                try:
                    return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
                except ValueError:
                    try:
                        return datetime.strptime(time_str, "%Y-%m-%d")
                    except ValueError:
                        pass

        time_el = soup.find("time")
        if time_el:
            dt_str = time_el.get("datetime", "") or time_el.get_text(strip=True)
            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
                try:
                    return datetime.strptime(dt_str[:19], fmt)
                except ValueError:
                    continue
        return None
