"""
Web 服务器入口 - 供 Railway/Vercel 等平台部署
使用 Flask 提供 REST API，前端页面触发日报生成
"""
import os
import sys
import json
import uuid
import threading
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_file, send_from_directory

# 确保项目根目录在 Python 路径中
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config_loader import ConfigLoader
from src.utils.logger import setup_logger
from src.fetchers.rss_fetcher import RSSFetcher
from src.fetchers.web_fetcher import WebFetcher
from src.fetchers.manual_fetcher import ManualURLFetcher
from src.filters.news_filter import NewsFilter
from src.generators.llm_generator import LLMGenerator
from src.generators.daily_report import DailyReportGenerator, CATEGORY_CONFIG

app = Flask(__name__, static_folder="public", static_url_path="")

# ============================================================
# 内存任务存储
# ============================================================
TASKS = {}

# 模块级 logger
logger = logging.getLogger("voyan_web")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# ============================================================
# 进度追踪
# ============================================================
class ProgressHandler(logging.Handler):
    """拦截日志消息来更新任务进度"""

    def __init__(self, task_id: str):
        super().__init__()
        self.task_id = task_id

    def emit(self, record):
        msg = record.getMessage()
        task = TASKS.get(self.task_id)
        if not task:
            return

        if "[步骤1/4]" in msg or "抓取新闻" in msg:
            self._update(5, "正在抓取新闻...")
        elif "共抓取" in msg and "条新闻" in msg:
            self._update(25, f"新闻抓取完成")
        elif "[步骤2/4]" in msg or "筛选新闻" in msg:
            self._update(30, "正在筛选新闻...")
        elif "筛选后保留" in msg:
            self._update(45, "筛选完成")
        elif "[步骤3/4]" in msg or "LLM分析" in msg:
            self._update(50, "正在调用 LLM 分析新闻...")
        elif "分析第" in msg and "条:" in msg:
            parts = msg.split("分析第 ")[1] if "分析第 " in msg else ""
            if "/" in parts:
                try:
                    current = int(parts.split("/")[0])
                    total = int(parts.split("/")[1].split(" ")[0])
                    pct = 50 + int((current / total) * 35)
                    self._update(pct, f"LLM 分析中: {current}/{total}")
                except (ValueError, IndexError):
                    pass
        elif "生成日报总结" in msg:
            self._update(88, "正在生成日报总结...")
        elif "[步骤4/4]" in msg or "生成日报文件" in msg:
            self._update(90, "正在生成日报文件...")
        elif "日报生成完成" in msg:
            self._update(100, "日报生成完成！")
        elif "未抓取到任何新闻" in msg:
            self._update(100, "未抓取到任何新闻")
        elif "筛选后无相关新闻" in msg:
            self._update(100, "筛选后无相关新闻")

    def _update(self, progress, step):
        TASKS[self.task_id]["progress"] = progress
        TASKS[self.task_id]["step"] = step


# ============================================================
# 日报生成核心逻辑
# ============================================================
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

        # 设置 logger
        app_logger = setup_logger(
            name=f"task_{task_id[:8]}",
            log_file=None,
            level="INFO",
        )
        progress_handler = ProgressHandler(task_id)
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
            task["result"] = "# 洞察信息收集日报\n\n**状态**：未抓取到任何新闻"
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
            task["result"] = "# 洞察信息收集日报\n\n**状态**：筛选后无相关新闻"
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
                import time
                interval = loader.get("llm.request_interval", 1)
                time.sleep(interval)

        app_logger.info("  生成日报总结...")
        summary = llm.generate_summary(filtered_articles, analysis_results)

        # 4. 生成 Markdown
        task["step"] = "正在生成日报文件..."
        report_gen = DailyReportGenerator(loader.config, app_logger)
        report_date = datetime.now()
        date_str = report_date.strftime("%Y%m%d")
        display_date = report_date.strftime("%Y年%m月%d日")

        grouped = report_gen._group_by_category(filtered_articles, analysis_results)
        md_content = _build_markdown(grouped, summary, display_date, date_str, filtered_articles)

        task["status"] = "done"
        task["progress"] = 100
        task["step"] = "日报生成完成！"
        task["result"] = md_content
        task["article_count"] = len(filtered_articles)
        task["date"] = date_str

        app_logger.info(f"日报生成完成！共收录 {len(filtered_articles)} 条新闻")

    except Exception as e:
        logger.error(f"[任务 {task_id[:8]}] 生成失败: {e}", exc_info=True)
        task["status"] = "error"
        task["step"] = f"生成失败: {str(e)}"
        task["error"] = str(e)


def _build_markdown(grouped, summary, display_date, date_str, articles):
    """构建 Markdown 内容"""
    lines = []
    lines.append(f"# 洞察信息收集日报")
    lines.append(f"\n**日期**：{display_date}\n")

    if summary and summary.get("要点概括"):
        lines.append("## 今日要点概括\n")
        for i, item in enumerate(summary["要点概括"], 1):
            lines.append(f"{i}. {item}")
        lines.append("")

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
            lines.append(
                f"| {time_str} | {event} | {parties} | {impact} "
                f"| {insight} | {suggestion} | [链接]({article.url}) |"
            )

    if summary:
        lines.append("\n---\n")
        lines.append("## 初步分析与预警\n")
        for section_title, key in [("战略意义", "战略意义"), ("风险预警", "风险预警"), ("近期关注", "近期关注")]:
            lines.append(f"### {section_title}\n")
            items = summary.get(key, [])
            if items:
                for i, item in enumerate(items, 1):
                    lines.append(f"{i}. {item}")
            else:
                lines.append("（暂无）")
            lines.append("")

    return "\n".join(lines)


# ============================================================
# API 路由
# ============================================================
@app.route("/")
def index():
    """前端页面"""
    return send_from_directory("public", "index.html")


@app.route("/api/generate", methods=["POST"])
def api_generate():
    """触发日报生成"""
    body = request.get_json(silent=True) or {}
    manual_urls = body.get("urls", None)

    task_id = str(uuid.uuid4())
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

    thread = threading.Thread(
        target=_run_report_generation,
        args=(task_id, manual_urls),
        daemon=True,
    )
    thread.start()

    return jsonify({
        "success": True,
        "task_id": task_id,
        "message": "日报生成任务已创建",
    })


@app.route("/api/status", methods=["GET"])
def api_status():
    """查询任务进度"""
    task_id = request.args.get("task_id", "")
    if not task_id or task_id not in TASKS:
        return jsonify({"error": "任务不存在"}), 404

    task = TASKS[task_id]
    return jsonify({
        "task_id": task["task_id"],
        "status": task["status"],
        "progress": task["progress"],
        "step": task["step"],
        "article_count": task.get("article_count", 0),
        "error": task.get("error"),
    })


@app.route("/api/download", methods=["GET"])
def api_download():
    """下载日报"""
    task_id = request.args.get("task_id", "")
    fmt = request.args.get("format", "md")

    if not task_id or task_id not in TASKS:
        return jsonify({"error": "任务不存在"}), 404

    task = TASKS[task_id]
    if task["status"] != "done" or not task.get("result"):
        return jsonify({"error": "日报尚未生成完成"}), 400

    if fmt == "json":
        return jsonify({"content": task["result"], "article_count": task.get("article_count", 0)})

    # 返回 Markdown 文件下载
    date_str = task.get("date", datetime.now().strftime("%Y%m%d"))
    from io import BytesIO
    buffer = BytesIO()
    buffer.write(task["result"].encode("utf-8"))
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"洞察信息收集日报_{date_str}.md",
        mimetype="text/markdown",
    )


@app.route("/api/tasks", methods=["GET"])
def api_tasks():
    """任务列表"""
    tasks = []
    for tid, task in TASKS.items():
        tasks.append({
            "task_id": task["task_id"],
            "status": task["status"],
            "progress": task["progress"],
            "step": task["step"],
            "article_count": task.get("article_count", 0),
            "created_at": task["created_at"],
        })
    tasks.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify({"tasks": tasks})


# 本地开发
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
