"""
新闻筛选模块 - 关键词匹配、排除过滤、层面分类、相关性排序
"""
import re
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

from ..fetchers.base import NewsArticle


# 岚图/东风相关高权重关键词
HIGH_PRIORITY_KEYWORDS = [
    "岚图", "东风", "梦想家", "追光", "FREE",
]

# 竞品关键词
COMPETITOR_KEYWORDS = [
    "理想", "蔚来", "小鹏", "问界", "腾势", "极氪",
    "比亚迪", "特斯拉", "阿维塔", "深蓝", "零跑", "哪吒", "高合",
]

# 通用行业关键词
GENERAL_KEYWORDS = [
    "新能源车", "电动车", "混动", "增程", "纯电", "MPV", "SUV",
    "销量", "交付", "新车", "发布会", "战略", "出海", "关税", "补贴",
    "电池", "固态电池", "钠电池", "智能驾驶", "自动驾驶", "芯片", "座舱",
    "大模型", "AI", "800V", "快充", "换电", "价格战", "降价", "涨价",
]


@dataclass
class FilterResult:
    """筛选结果"""
    article: NewsArticle
    passed: bool  # 是否通过筛选
    matched_keywords: List[str]  # 命中的关键词
    exclude_matched: List[str]  # 命中的排除关键词
    category: str  # 层面分类
    relevance_score: float  # 相关性得分
    needs_review: bool  # 是否需要人工审核


class NewsFilter:
    """新闻筛选器"""

    def __init__(self, must_include: List[str], exclude: List[str],
                 category_rules: Dict[str, List[str]], logger=None,
                 must_include_weak: List[str] = None):
        self.must_include = [kw.lower() for kw in must_include]
        self.must_include_weak = [kw.lower() for kw in (must_include_weak or [])]
        self.exclude = [kw.lower() for kw in exclude]
        self.category_rules = {
            cat: [kw.lower() for kw in kws]
            for cat, kws in category_rules.items()
        }
        self.logger = logger

        # 构建正则表达式用于高效匹配
        self._include_pattern = self._build_pattern(must_include)
        self._exclude_pattern = self._build_pattern(exclude)

    def filter_articles(self, articles: List[NewsArticle]) -> List[FilterResult]:
        """对文章列表进行筛选"""
        results = []
        for article in articles:
            result = self._filter_single(article)
            results.append(result)
        return results

    def get_passed_articles(self, articles: List[NewsArticle]) -> List[NewsArticle]:
        """获取通过筛选的文章，按相关性排序"""
        results = self.filter_articles(articles)
        passed = [r for r in results if r.passed]
        # 按相关性得分降序排序
        passed.sort(key=lambda r: r.relevance_score, reverse=True)
        # 将筛选结果写回文章对象
        for r in passed:
            r.article.category = r.category
            r.article.relevance_score = r.relevance_score
            r.article.matched_keywords = r.matched_keywords
        return [r.article for r in passed]

    def _filter_single(self, article: NewsArticle) -> FilterResult:
        """筛选单篇文章"""
        text = f"{article.title} {article.content}".lower()
        title_lower = article.title.lower()

        # 0. 过滤标题过短或无意义的新闻（如标题仅为"电动汽车"）
        if len(article.title.strip()) < 8:
            return FilterResult(
                article=article,
                passed=False,
                matched_keywords=[],
                exclude_matched=[],
                category="",
                relevance_score=0.0,
                needs_review=False,
            )

        # 1. 检查排除关键词
        exclude_matched = []
        for kw in self.exclude:
            if kw in text:
                exclude_matched.append(kw)

        if exclude_matched:
            return FilterResult(
                article=article,
                passed=False,
                matched_keywords=[],
                exclude_matched=exclude_matched,
                category="",
                relevance_score=0.0,
                needs_review=False,
            )

        # 2. 检查必收关键词（强相关 + 弱相关两级匹配）
        strong_matched = [kw for kw in self.must_include if kw in text]
        weak_matched = [kw for kw in self.must_include_weak if kw in text]

        # 强相关词命中1个即通过，弱相关词需命中2个才通过
        passed = len(strong_matched) > 0 or len(weak_matched) >= 2
        matched_keywords = strong_matched + weak_matched
        needs_review = not passed and len(article.title) > 10

        # 4. 计算相关性得分
        relevance_score = self._calculate_relevance(
            title_lower, text, matched_keywords
        )

        # 5. 层面分类
        category = self._classify_category(text)

        return FilterResult(
            article=article,
            passed=passed,
            matched_keywords=matched_keywords,
            exclude_matched=[],
            category=category,
            relevance_score=relevance_score,
            needs_review=needs_review,
        )

    def _calculate_relevance(self, title_lower: str, text: str,
                             matched_keywords: List[str]) -> float:
        """计算相关性得分"""
        score = 0.0

        for kw in matched_keywords:
            # 标题中命中：权重更高
            if kw in title_lower:
                if kw in [k.lower() for k in HIGH_PRIORITY_KEYWORDS]:
                    score += 10.0  # 岚图/东风在标题中
                elif kw in [k.lower() for k in COMPETITOR_KEYWORDS]:
                    score += 7.0  # 竞品在标题中
                else:
                    score += 5.0  # 通用词在标题中
            else:
                if kw in [k.lower() for k in HIGH_PRIORITY_KEYWORDS]:
                    score += 5.0  # 岚图/东风在正文中
                elif kw in [k.lower() for k in COMPETITOR_KEYWORDS]:
                    score += 3.0  # 竞品在正文中
                else:
                    score += 1.0  # 通用词在正文中

        return score

    def _classify_category(self, text: str) -> str:
        """根据关键词进行层面分类，政策层优先于公司层"""
        # 第一步：检查国家/政策层关键词（最高优先级）
        policy_keywords = self.category_rules.get("国家/政策层", [])
        policy_hits = sum(1 for kw in policy_keywords if kw in text)
        if policy_hits > 0:
            return "国家/政策层"

        # 第二步：检查技术/研发层关键词
        tech_keywords = self.category_rules.get("技术/研发层", [])
        tech_hits = sum(1 for kw in tech_keywords if kw in text)
        if tech_hits > 0:
            return "技术/研发层"

        # 第三步：检查是否涉及具体公司/品牌 → 业务/竞争层
        company_keywords = [
            "岚图", "东风", "梦想家", "追光", "free",
            "理想", "蔚来", "小鹏", "问界", "腾势", "极氪",
            "比亚迪", "特斯拉", "阿维塔", "深蓝", "零跑", "哪吒", "高合",
            "奇瑞", "吉利", "长安", "长城", "上汽", "一汽", "广汽", "北汽",
            "奥迪", "宝马", "奔驰", "丰田", "本田", "大众", "现代", "福特",
            "日产", "沃尔沃", "别克", "雪佛兰", "jeep", "路虎", "保时捷",
            "lucid", "rivian", "faraday", "ff",
            "神龙", "标致", "雪铁龙", "stellantis",
            "何小鹏", "李想", "李斌", "雷军", "马斯克", "朱江明", "卢放",
        ]
        company_hits = sum(1 for kw in company_keywords if kw in text)
        if company_hits > 0:
            return "业务/竞争层"

        # 第四步：按分类规则得分判断（行业/市场层 vs 其他）
        scores = {}
        for category, keywords in self.category_rules.items():
            score = sum(1 for kw in keywords if kw in text)
            scores[category] = score

        if not scores or max(scores.values()) == 0:
            return "行业/市场层"  # 默认分类

        return max(scores, key=scores.get)

    @staticmethod
    def _build_pattern(keywords: List[str]) -> Optional[re.Pattern]:
        """构建正则表达式（备用，当前使用简单字符串匹配）"""
        if not keywords:
            return None
        escaped = [re.escape(kw) for kw in keywords]
        return re.compile("|".join(escaped), re.IGNORECASE)
