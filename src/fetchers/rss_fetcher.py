"""
RSS 抓取器
"""
import time
import feedparser
from typing import List, Optional
from datetime import datetime, timezone
from .base import BaseFetcher, NewsArticle


class RSSFetcher(BaseFetcher):
    """RSS/Atom 订阅源抓取器"""

    def fetch(self, source_config: dict) -> List[NewsArticle]:
        """从RSS源抓取新闻"""
        url = source_config.get("url", "")
        name = source_config.get("name", "未知来源")
        keyword_filter = source_config.get("rss_keyword_filter", [])
        articles = []

        if self.logger:
            self.logger.info(f"[RSS] 开始抓取: {name} ({url})")

        try:
            feed = feedparser.parse(url)

            if feed.bozo and not feed.entries:
                if self.logger:
                    self.logger.warning(f"[RSS] 解析失败: {name} - {feed.bozo_exception}")
                return articles

            for entry in feed.entries[:self.max_articles]:
                try:
                    article = self._parse_entry(entry, name)
                    if article is None:
                        continue

                    # 时间范围过滤
                    if not self._is_within_time_range(article.publish_time):
                        continue

                    # RSS源关键词预过滤（可选）
                    if keyword_filter:
                        text = f"{article.title} {article.content}"
                        if not any(kw in text for kw in keyword_filter):
                            continue

                    articles.append(article)

                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"[RSS] 解析条目异常: {e}")
                    continue

            if self.logger:
                self.logger.info(f"[RSS] {name}: 获取 {len(articles)} 条新闻")

        except Exception as e:
            if self.logger:
                self.logger.error(f"[RSS] 抓取异常 {name}: {e}")

        return articles

    def _parse_entry(self, entry, source_name: str) -> Optional[NewsArticle]:
        """解析RSS条目"""
        title = entry.get("title", "").strip()
        if not title:
            return None

        url = entry.get("link", "")
        if not url:
            return None

        # 提取发布时间
        publish_time = None
        time_str = entry.get("published", "") or entry.get("updated", "")
        if time_str:
            try:
                # feedparser 已解析的时间
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    publish_time = datetime(*entry.published_parsed[:6])
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    publish_time = datetime(*entry.updated_parsed[:6])
                else:
                    publish_time = datetime.strptime(time_str[:19], "%Y-%m-%dT%H:%M:%S")
            except (ValueError, TypeError, OverflowError):
                pass

        # 提取内容
        content = ""
        if entry.get("summary"):
            content = entry.get("summary")
        elif entry.get("content"):
            content = entry.get("content")[0].get("value", "")
        elif entry.get("description"):
            content = entry.get("description", "")

        # 清理HTML标签
        content = self._strip_html(content)

        # 截取前500字
        brief = content[:500] if content else title

        author = entry.get("author", "") or ""

        return NewsArticle(
            title=title,
            url=url,
            source=source_name,
            publish_time=publish_time,
            content=brief,
            full_content=content,
            author=author,
        )

    @staticmethod
    def _strip_html(html_text: str) -> str:
        """简单去除HTML标签"""
        import re
        clean = re.sub(r"<[^>]+>", "", html_text)
        clean = re.sub(r"&nbsp;", " ", clean)
        clean = re.sub(r"&amp;", "&", clean)
        clean = re.sub(r"&lt;", "<", clean)
        clean = re.sub(r"&gt;", ">", clean)
        clean = re.sub(r"&quot;", '"', clean)
        clean = re.sub(r"&#39;", "'", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean
