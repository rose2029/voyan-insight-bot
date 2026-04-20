"""
Vercel Serverless Function - POST /api/generate
触发日报生成，立即返回 task_id，后台异步执行
"""
import json
import os
import sys
import uuid
import threading
import logging
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config_loader import ConfigLoader
from src.utils.logger import setup_logger
from src.fetchers.rss_fetcher import RSSFetcher
from src.fetchers.web_fetcher import WebFetcher
from src.fetchers.manual_fetcher import ManualURLFetcher
from src.filters.news_filter import NewsFilter
from src.generators.llm_generator import LLMGenerator
from src.generators.daily_report import DailyReportGenerator

# ============================================================
# 内存任务存储（适合单实例部署）
# ============================================================
TASKS = {}

# 模块级 logger（用于 Vercel 环境）
logger = logging.getLogger("voyan_insight_api")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


class ProgressCallback:
    """通过 logger 回调更新任务进度"""

    def __init__(self, task_id: str):
        self.task_id = task_id

    def update(self, progress: int, step: str):
        """更新任务进度"""
        if self.task_id in TASKS:
            TASKS[self.task_id]["progress"] = progress
            TASKS[self.task_id]["step"] = step
            logger.info(f"[任务 {self.task_id[:8]}] {progress}% - {step}")


def _create_progress_logger(task_id: str):
    """创建一个自定义 logger handler，拦截日志消息来更新进度"""
    callback = ProgressCallback(task_id)

    class ProgressHandler(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            # 解析步骤日志来更新进度
            if "[步骤1/4]" in msg or "抓取新闻" in msg:
                callback.update(5, "正在抓取新闻...")
            elif "共抓取" in msg and "条新闻" in msg:
                callback.update(25, f"新闻抓取完成: {msg}")
            elif "[步骤2/4]" in msg or "筛选新闻" in msg:
                callback.update(30, "正在筛选新闻...")
            elif "筛选后保留" in msg:
                callback.update(45, f"筛选完成: {msg}")
            elif "[步骤3/4]" in msg or "LLM分析" in msg:
                callback.update(50, "正在调用 LLM 分析新闻...")
            elif "分析第" in msg and "条:" in msg:
                # 提取进度数字
                parts = msg.split("分析第 ")[1] if "分析第 " in msg else ""
                if "/" in parts:
                    current = int(parts.split("/")[0])
                    total = int(parts.split("/")[1].split(" ")[0])
                    pct = 50 + int((current / total) * 35)
                    callback.update(pct, f"LLM 分析中: {current}/{total}")
            elif "生成日报总结" in msg:
                callback.update(88, "正在生成日报总结...")
            elif "[步骤4/4]" in msg or "生成日报文件" in msg:
                callback.update(90, "正在生成日报文件...")
            elif "日报生成完成" in msg:
                callback.update(100, "日报生成完成！")
            elif "未抓取到任何新闻" in msg:
                callback.update(100, "未抓取到任何新闻")
            elif "筛选后无相关新闻" in msg:
                callback.update(100, "筛选后无相关新闻")

    return ProgressHandler()


def _run_report_generation(task_id: str, manual_urls: list = None):
    """在后台线程中执行日报生成"""
    task = TASKS[task_id]
    task["status"] = "running"
    task["progress"] = 1
    task["step"] = "正在初始化..."

    try:
        # 初始化配置
        config_dir = str(PROJECT_ROOT / "config")
        loader = ConfigLoader(config_dir)
        loader.ensure_dirs()

        # 设置 logger 并添加进度 handler
        app_logger = setup_logger(
            name=f"task_{task_id[:8]}",
            log_file=None,  # Vercel 无文件系统，不写文件
            level="INFO",
        )
        progress_handler = _create_progress_logger(task_id)
        app_logger.addHandler(progress_handler)

        # 1. 抓取新闻
        task["step"] = "正在抓取新闻..."
        all_articles = []

        rss_fetcher = RSSFetcher(loader.config, app_logger)
        for source in loader.config.get("sources", {}).get("rss", []):
            if not source.get("enabled", True):
                continue
            try:
                articles = rss_fetcher.fetch(source)
                all_articles.extend(articles)
            except Exception as e:
                app_logger.error(f"RSS抓取失败 [{source.get('name')}]: {e}")

        web_fetcher = WebFetcher(loader.config, app_logger)
        for source_type in ["web", "portal", "government", "energy"]:
            for source in loader.config.get("sources", {}).get(source_type, []):
                if not source.get("enabled", True):
                    continue
                try:
                    articles = web_fetcher.fetch(source)
                    all_articles.extend(articles)
                except Exception as e:
                    app_logger.error(f"抓取失败 [{source.get('name')}]: {e}")

        if manual_urls:
            manual_fetcher = ManualURLFetcher(loader.config, app_logger)
            try:
                articles = manual_fetcher.fetch({"urls": manual_urls})
                all_articles.extend(articles)
            except Exception as e:
                app_logger.error(f"手动URL抓取失败: {e}")

        # 去重
        seen_urls = set()
        unique_articles = []
        for article in all_articles:
            if article.url not in seen_urls:
                seen_urls.add(article.url)
                unique_articles.append(article)

        app_logger.info(f"共抓取 {len(all_articles)} 条新闻，去重后 {len(unique_articles)} 条")

        if not unique_articles:
            task["status"] = "done"
            task["progress"] = 100
            task["step"] = "未抓取到任何新闻"
            task["result"] = "# 洞察信息收集日报\n\n**状态**：未抓取到任何新闻\n\n请检查数据源配置或稍后重试。"
            return

        # 2. 筛选新闻
        task["step"] = "正在筛选新闻..."
        keywords = loader.keywords
        category_rules = loader.get("category_rules", {})
        news_filter = NewsFilter(
            must_include=keywords.get("must_include", []),
            exclude=keywords.get("exclude", []),
            category_rules=category_rules,
            logger=app_logger,
            must_include_weak=keywords.get("must_include_weak", []),
        )
        filtered_articles = news_filter.get_passed_articles(unique_articles)
        app_logger.info(f"筛选后保留 {len(filtered_articles)} 条新闻")

        if not filtered_articles:
            task["status"] = "done"
            task["progress"] = 100
            task["step"] = "筛选后无相关新闻"
            task["result"] = "# 洞察信息收集日报\n\n**状态**：筛选后无相关新闻\n\n今日抓取的新闻中未找到与关键词匹配的内容。"
            return

        # 按相关性排序并限制
        MAX_ARTICLES = 20
        if len(filtered_articles) > MAX_ARTICLES:
            important = [a for a in filtered_articles if getattr(a, 'is_important', False)]
            normal = [a for a in filtered_articles if not getattr(a, 'is_important', False)]
            important.sort(key=lambda a: a.relevance_score, reverse=True)
            normal.sort(key=lambda a: a.relevance_score, reverse=True)
            filtered_articles = (important + normal)[:MAX_ARTICLES]

        # 3. LLM 分析
        task["step"] = "正在调用 LLM 分析新闻..."
        llm = LLMGenerator(loader.config, app_logger)

        analysis_results = []
        for i, article in enumerate(filtered_articles, 1):
            app_logger.info(f"  分析第 {i}/{len(filtered_articles)} 条: {article.title[:30]}...")
            analysis = llm.analyze_article(article)
            analysis_results.append(analysis)
            if llm.is_available():
                interval = loader.get("llm.request_interval", 1)
                import time
                time.sleep(interval)

        # 生成日报总结
        app_logger.info("  生成日报总结...")
        summary = llm.generate_summary(filtered_articles, analysis_results)

        # 4. 生成 Markdown（不生成 Word，Vercel 无文件系统）
        task["step"] = "正在生成日报文件..."
        report_gen = DailyReportGenerator(loader.config, app_logger)
        report_date = datetime.now()
        date_str = report_date.strftime(loader.get("output.date_format", "%Y%m%d"))
        display_date = report_date.strftime("%Y年%m月%d日")

        # 使用 DailyReportGenerator 的内部方法生成 Markdown 内容
        grouped = report_gen._group_by_category(filtered_articles, analysis_results)
        md_content = _build_markdown(grouped, summary, display_date, date_str, filtered_articles)

        task["status"] = "done"
        task["progress"] = 100
        task["step"] = "日报生成完成！"
        task["result"] = md_content
        task["article_count"] = len(filtered_articles)
        task["created_at"] = datetime.now().isoformat()

        app_logger.info(f"日报生成完成！共收录 {len(filtered_articles)} 条新闻")

    except Exception as e:
        logger.error(f"[任务 {task_id[:8]}] 生成失败: {e}", exc_info=True)
        task["status"] = "error"
        task["progress"] = task.get("progress", 0)
        task["step"] = f"生成失败: {str(e)}"
        task["error"] = str(e)


def _build_markdown(grouped: dict, summary: dict, display_date: str,
                    date_str: str, articles: list) -> str:
    """构建 Markdown 内容（内存中，不写文件）"""
    from src.generators.daily_report import CATEGORY_CONFIG

    lines = []
    lines.append(f"# 洞察信息收集日报")
    lines.append(f"\n**日期**：{display_date}\n")

    # 今日要点概括
    if summary and summary.get("要点概括"):
        lines.append("## 今日要点概括\n")
        for i, item in enumerate(summary["要点概括"], 1):
            lines.append(f"{i}. {item}")
        lines.append("")

    # 各层面分类新闻
    for category, items in grouped.items():
        lines.append(f"\n## {category}\n")

        if not items:
            lines.append("暂无相关新闻\n")
            continue

        sub_title = CATEGORY_CONFIG.get(category, "")
        if sub_title:
            lines.append(f"**{sub_title}**\n")

        lines.append("| 时间 | 事件内容 | 参与方 | 事件影响 | 事件洞察 | 对岚图的影响及启示 | 信息来源 |")
        lines.append("|------|----------|--------|----------|----------|-------------------|----------|")

        for article, analysis in items:
            time_str = article.publish_date_str or date_str[4:6] + "." + date_str[6:8]
            event = analysis.get("事件内容", article.title).replace("|", "\\|")
            parties = analysis.get("参与方", "").replace("|", "\\|")
            impact = analysis.get("事件影响", "").replace("|", "\\|")
            insight = analysis.get("事件洞察", "").replace("|", "\\|")
            suggestion = analysis.get("对岚图的影响及启示", "").replace("|", "\\|")
            url = article.url

            lines.append(
                f"| {time_str} | {event} | {parties} | {impact} "
                f"| {insight} | {suggestion} | [链接]({url}) |"
            )

    # 总结模块
    if summary:
        lines.append("\n---\n")
        lines.append("## 初步分析与预警\n")

        sections = [
            ("战略意义", summary.get("战略意义", [])),
            ("风险预警", summary.get("风险预警", [])),
            ("近期关注", summary.get("近期关注", [])),
        ]

        for section_title, items in sections:
            lines.append(f"### {section_title}\n")
            if items:
                for i, item in enumerate(items, 1):
                    lines.append(f"{i}. {item}")
            else:
                lines.append("（暂无）")
            lines.append("")

    return "\n".join(lines)


def handler(req, res):
    """Vercel Serverless Function 入口"""
    # 只处理 POST 请求
    if req.method != "POST":
        return res.status(405).json({"error": "只支持 POST 请求"})

    try:
        # 解析请求体
        body = req.json() or {}
        manual_urls = body.get("urls", None)

        # 检查 API Key
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            logger.warning("DEEPSEEK_API_KEY 环境变量未设置")

        # 生成任务 ID
        task_id = str(uuid.uuid4())

        # 创建任务记录
        TASKS[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "step": "任务已创建，等待执行...",
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
            "article_count": 0,
        }

        # 启动后台线程执行生成
        thread = threading.Thread(
            target=_run_report_generation,
            args=(task_id, manual_urls),
            daemon=True,
        )
        thread.start()

        logger.info(f"任务已创建: {task_id[:8]}")

        return res.status(200).json({
            "success": True,
            "task_id": task_id,
            "message": "日报生成任务已创建，请通过 /api/status 查询进度",
        })

    except Exception as e:
        logger.error(f"创建任务失败: {e}", exc_info=True)
        return res.status(500).json({
            "success": False,
            "error": str(e),
        })
