"""
数据抓取基类与数据模型
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
from abc import ABC, abstractmethod


@dataclass
class NewsArticle:
    """新闻文章数据模型"""
    title: str
    url: str
    source: str  # 来源名称
    publish_time: Optional[datetime] = None
    content: str = ""  # 正文内容（截取前500字）
    full_content: str = ""  # 完整正文
    author: str = ""
    category: str = ""  # 层面分类（筛选后填充）
    relevance_score: float = 0.0  # 相关性得分
    is_marked: bool = False  # 是否标记为重要（半月报用）
    is_important: bool = False  # 是否为重要来源（priority: high 的数据源）
    matched_keywords: List[str] = field(default_factory=list)

    @property
    def publish_date_str(self) -> str:
        if self.publish_time:
            return self.publish_time.strftime("%m.%d")
        return ""

    @property
    def publish_date_full(self) -> str:
        if self.publish_time:
            return self.publish_time.strftime("%Y-%m-%d")
        return ""

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "content": self.content,
            "author": self.author,
            "category": self.category,
            "relevance_score": self.relevance_score,
            "is_marked": self.is_marked,
            "matched_keywords": self.matched_keywords,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsArticle":
        if data.get("publish_time"):
            data["publish_time"] = datetime.fromisoformat(data["publish_time"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class BaseFetcher(ABC):
    """抓取器基类"""

    def __init__(self, config: dict, logger=None):
        self.config = config
        self.logger = logger
        self.user_agent = config.get("fetch", {}).get(
            "user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self.timeout = config.get("fetch", {}).get("timeout", 30)
        self.request_interval = config.get("fetch", {}).get("request_interval", 2)
        self.hours_range = config.get("fetch", {}).get("hours_range", 24)
        self.max_articles = config.get("fetch", {}).get("max_articles_per_source", 50)

    @abstractmethod
    def fetch(self, source_config: dict) -> List[NewsArticle]:
        """抓取指定数据源的新闻"""
        pass

    def _is_within_time_range(self, publish_time: Optional[datetime]) -> bool:
        """检查发布时间是否在指定范围内

        策略：有明确发布日期时按日期过滤；无日期时默认保留，
        因为文章出现在当前新闻列表页上，本身就说明是近期内容。
        """
        if publish_time is None:
            return True  # 无日期时保留（出现在列表页即代表近期）
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(hours=self.hours_range)
        return publish_time >= cutoff
