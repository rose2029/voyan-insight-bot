"""
网页抓取器 - 从汽车行业网站爬取新闻
"""
import time
import re
from typing import List, Optional
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .base import BaseFetcher, NewsArticle


class WebFetcher(BaseFetcher):
    """网页新闻抓取器"""

    def __init__(self, config: dict, logger=None):
        super().__init__(config, logger)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    def fetch(self, source_config: dict) -> List[NewsArticle]:
        """从网页源抓取新闻列表"""
        url = source_config.get("url", "")
        name = source_config.get("name", "未知来源")
        list_selector = source_config.get("list_selector", "a")
        articles = []

        if self.logger:
            self.logger.info(f"[WEB] 开始抓取: {name} ({url})")

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = self._detect_encoding(resp)

            soup = BeautifulSoup(resp.text, "lxml")

            # 查找新闻列表链接
            links = self._extract_links(soup, list_selector, url)

            if self.logger:
                self.logger.info(f"[WEB] {name}: 发现 {len(links)} 条链接")

            for link_url, link_title in links[:self.max_articles]:
                try:
                    article = self._fetch_article(link_url, link_title, name, source_config)
                    if article is None:
                        continue
                    if not self._is_within_time_range(article.publish_time):
                        continue
                    articles.append(article)
                    time.sleep(self.request_interval)
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"[WEB] 抓取文章异常: {link_url} - {e}")
                    continue

            if self.logger:
                self.logger.info(f"[WEB] {name}: 成功获取 {len(articles)} 条新闻")

        except Exception as e:
            if self.logger:
                self.logger.error(f"[WEB] 抓取异常 {name}: {e}")

        return articles

    def _extract_links(self, soup: BeautifulSoup, selector: str, base_url: str) -> List[tuple]:
        """从列表页提取新闻链接"""
        links = []
        seen_urls = set()

        # 尝试CSS选择器
        elements = soup.select(selector)
        for el in elements:
            href = el.get("href", "")
            if not href:
                continue
            full_url = urljoin(base_url, href)
            if full_url in seen_urls:
                continue
            # 过滤非文章链接
            if self._is_article_url(full_url):
                seen_urls.add(full_url)
                title = el.get_text(strip=True)
                if not title:
                    title = ""
                links.append((full_url, title))

        # 如果选择器没找到链接，尝试通用方法
        if not links:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                full_url = urljoin(base_url, href)
                if full_url in seen_urls:
                    continue
                if self._is_article_url(full_url):
                    title = a_tag.get_text(strip=True)
                    if title and len(title) > 8:  # 标题至少8个字符
                        seen_urls.add(full_url)
                        links.append((full_url, title))

        return links

    def _fetch_article(self, url: str, list_title: str, source_name: str,
                       source_config: dict) -> Optional[NewsArticle]:
        """抓取单篇文章详情"""
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = self._detect_encoding(resp)

            soup = BeautifulSoup(resp.text, "lxml")

            # 提取标题
            title = self._extract_title(soup, source_config)
            if not title:
                title = list_title
            if not title:
                return None

            # 提取正文
            content = self._extract_content(soup, source_config)
            if not content:
                content = title

            # 提取发布时间
            publish_time = self._extract_time(soup, url)

            return NewsArticle(
                title=title.strip(),
                url=url,
                source=source_name,
                publish_time=publish_time,
                content=content[:500],
                full_content=content,
            )

        except requests.RequestException as e:
            if self.logger:
                self.logger.debug(f"[WEB] 请求失败: {url} - {e}")
            return None

    def _extract_title(self, soup: BeautifulSoup, source_config: dict) -> str:
        """提取文章标题"""
        title_selector = source_config.get("title_selector", "h1")
        el = soup.select_one(title_selector)
        if el:
            title = el.get_text(strip=True)
        else:
            # 备用：og:title
            og = soup.find("meta", property="og:title")
            if og:
                title = og.get("content", "").strip()
            elif soup.title:
                title = soup.title.get_text(strip=True)
            else:
                return ""

        # 清理标题前缀（如 "中国储能网 -今日头条 - 实际标题"）
        import re
        # 去除 "网站名 - 分类 - " 前缀
        title = re.sub(r'^.*?-\s*(?:今日头条|最新资讯|新闻|头条|行业动态|企业动态)\s*-\s*', '', title)
        title = re.sub(r'^.*?-\s*(?:今日头条|最新资讯|新闻|头条|行业动态|企业动态)\s*$', '', title)
        # 去除开头的 "●" 符号
        title = re.sub(r'^[●\-\s]+', '', title)
        # 去除末尾的特殊符号
        title = re.sub(r'[\{\}\[\]]+$', '', title)
        return title.strip()

    def _extract_content(self, soup: BeautifulSoup, source_config: dict) -> str:
        """提取文章正文"""
        content_selector = source_config.get("content_selector", "")
        if content_selector:
            el = soup.select_one(content_selector)
            if el:
                return el.get_text(separator="\n", strip=True)

        # 备用：尝试常见正文选择器
        common_selectors = [
            ".article-content", ".content", ".article-body",
            "#article-content", "#content", ".news-content",
            "article .text", ".detail-content", ".news-detail",
        ]
        for sel in common_selectors:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 100:
                    return text

        # 最后手段：提取body文本
        body = soup.find("body")
        if body:
            # 移除script和style
            for tag in body.find_all(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            return body.get_text(separator="\n", strip=True)[:3000]

        return ""

    def _extract_time(self, soup: BeautifulSoup, url: str) -> Optional[datetime]:
        """提取发布时间"""
        # 尝试常见时间元素
        time_selectors = [
            ".pub-time", ".publish-time", ".date", ".time",
            ".article-date", ".post-date", ".news-time",
            "time", ".info-time", ".source-time",
            "span.date", "span.time",
        ]
        for sel in time_selectors:
            el = soup.select_one(sel)
            if el:
                time_str = el.get_text(strip=True)
                dt = self._parse_time_str(time_str)
                if dt:
                    return dt
                # 检查datetime属性
                dt_attr = el.get("datetime", "")
                if dt_attr:
                    dt = self._parse_time_str(dt_attr)
                    if dt:
                        return dt

        # 尝试meta标签
        for attr in ["article:published_time", "publish-date", "pubdate"]:
            meta = soup.find("meta", property=attr)
            if not meta:
                meta = soup.find("meta", attrs={"name": attr})
            if meta:
                dt = self._parse_time_str(meta.get("content", ""))
                if dt:
                    return dt

        # 备用：从URL中提取日期（如 /20260403/、/2026/04/03/、/202604/）
        import re
        url_patterns = [
            r'/(\d{4})/(\d{2})/(\d{2})',     # /2026/04/03/ (搜狐等)
            r'/(\d{4})(\d{2})/(\d{2})/',      # /202604/03/ (每日光伏)
            r'/(\d{4})(\d{2})(\d{2})[^\d]',   # /20260403/ (北极星，后面非数字)
            r'-(\d{4})(\d{2})(\d{2})-',       # -20260403-
        ]
        for pattern in url_patterns:
            m = re.search(pattern, url)
            if m:
                try:
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if 2024 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                        return datetime(y, mo, d)
                except (ValueError, IndexError):
                    continue

        # 备用2：只有年月的URL（如盖世汽车 /202604/5I704...）
        m = re.search(r'/(\d{4})(\d{2})/\w', url)
        if m:
            try:
                y, mo = int(m.group(1)), int(m.group(2))
                if 2024 <= y <= 2030 and 1 <= mo <= 12:
                    return datetime(y, mo, 1)  # 设为当月1日
            except (ValueError, IndexError):
                pass

        return None

    @staticmethod
    def _parse_time_str(time_str: str) -> Optional[datetime]:
        """解析各种时间格式"""
        time_str = time_str.strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S+08:00",
            "%Y-%m-%d",
            "%Y年%m月%d日 %H:%M",
            "%Y年%m月%d日",
            "%m-%d %H:%M",
            "%m/%d %H:%M",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(time_str[:19], fmt)
                # 如果没有年份，补充当前年份
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                return dt
            except ValueError:
                continue
        return None

    @staticmethod
    def _is_article_url(url: str) -> bool:
        """判断是否为文章URL（过滤非文章链接）"""
        skip_patterns = ["javascript:", "#", "mailto:", ".jpg", ".png", ".gif", ".css", ".js"]
        for p in skip_patterns:
            if p in url.lower():
                return False
        # URL应该有路径
        parsed = urlparse(url)
        path = parsed.path
        if not path or path == "/":
            return False
        return True

    @staticmethod
    def _detect_encoding(resp: requests.Response) -> str:
        """检测响应编码"""
        # 优先使用apparent_encoding（更准确）
        if resp.apparent_encoding and resp.apparent_encoding.lower() not in ("iso-8859-1",):
            return resp.apparent_encoding
        if resp.encoding and resp.encoding.lower() not in ("iso-8859-1",):
            return resp.encoding
        # 尝试从meta标签检测
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.content[:1024], "lxml")
        meta = soup.find("meta", charset=True)
        if meta:
            return meta.get("charset", "utf-8")
        return "utf-8"
