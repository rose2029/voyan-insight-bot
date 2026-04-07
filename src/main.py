"""
岚图汽车 · 洞察信息收集日报 · 主程序入口

用法：
    python -m src.main daily              # 生成今日日报
    python -m src.main daily --urls ...   # 添加手动URL后生成日报
    python -m src.main biweekly           # 生成半月报
    python -m src.main mark --date ... --index ...  # 标记重要新闻
    python -m src.main status             # 查看配置状态
"""
import sys
import json
import time
from datetime import datetime
from pathlib import Path

# 确保项目根目录在Python路径中
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import click
from src.utils.config_loader import ConfigLoader
from src.utils.logger import setup_logger
from src.utils.notification import NotificationManager
from src.fetchers.rss_fetcher import RSSFetcher
from src.fetchers.web_fetcher import WebFetcher
from src.fetchers.manual_fetcher import ManualURLFetcher
from src.filters.news_filter import NewsFilter
from src.generators.llm_generator import LLMGenerator
from src.generators.daily_report import DailyReportGenerator
from src.generators.biweekly_report import BiweeklyReportGenerator


def create_components(config_dir: str = None):
    """创建所有核心组件"""
    loader = ConfigLoader(config_dir)
    loader.ensure_dirs()

    log_file = str(loader.project_root / "logs" / "app.log")
    logger = setup_logger(log_file=log_file, level=loader.get("logging.level", "INFO"))

    notification = NotificationManager(loader.config)

    return loader, logger, notification


def fetch_all_news(loader: ConfigLoader, logger, manual_urls: list = None) -> list:
    """从所有配置的数据源抓取新闻"""
    all_articles = []

    # RSS源
    rss_fetcher = RSSFetcher(loader.config, logger)
    for source in loader.config.get("sources", {}).get("rss", []):
        if not source.get("enabled", True):
            continue
        try:
            articles = rss_fetcher.fetch(source)
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"RSS抓取失败 [{source.get('name')}]: {e}")

    # 网页源（垂直媒体）
    web_fetcher = WebFetcher(loader.config, logger)
    for source in loader.config.get("sources", {}).get("web", []):
        if not source.get("enabled", True):
            continue
        try:
            articles = web_fetcher.fetch(source)
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"网页抓取失败 [{source.get('name')}]: {e}")

    # 综合门户汽车频道
    for source in loader.config.get("sources", {}).get("portal", []):
        if not source.get("enabled", True):
            continue
        try:
            articles = web_fetcher.fetch(source)
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"门户抓取失败 [{source.get('name')}]: {e}")

    # 政府/协会官网源
    for source in loader.config.get("sources", {}).get("government", []):
        if not source.get("enabled", True):
            continue
        try:
            articles = web_fetcher.fetch(source)
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"政府网站抓取失败 [{source.get('name')}]: {e}")

    # 能源/储能/光伏媒体源（补充国家/政策层 + 技术/研发层）
    for source in loader.config.get("sources", {}).get("energy", []):
        if not source.get("enabled", True):
            continue
        try:
            articles = web_fetcher.fetch(source)
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"能源媒体抓取失败 [{source.get('name')}]: {e}")

    # 手动URL
    if manual_urls:
        manual_fetcher = ManualURLFetcher(loader.config, logger)
        try:
            articles = manual_fetcher.fetch({"urls": manual_urls})
            all_articles.extend(articles)
        except Exception as e:
            logger.error(f"手动URL抓取失败: {e}")

    # 去重（基于URL）
    seen_urls = set()
    unique_articles = []
    for article in all_articles:
        if article.url not in seen_urls:
            seen_urls.add(article.url)
            unique_articles.append(article)

    logger.info(f"共抓取 {len(all_articles)} 条新闻，去重后 {len(unique_articles)} 条")
    return unique_articles


def run_daily_report(loader: ConfigLoader, logger, notification,
                     manual_urls: list = None, dry_run: bool = False,
                     report_date: datetime = None):
    """执行日报生成流程"""
    logger.info("=" * 60)
    logger.info("开始生成日报")
    logger.info("=" * 60)

    start_time = time.time()

    # 1. 抓取新闻
    logger.info("[步骤1/4] 抓取新闻...")
    articles = fetch_all_news(loader, logger, manual_urls)

    if not articles:
        logger.warning("未抓取到任何新闻，日报生成终止")
        return

    # 2. 筛选新闻
    logger.info("[步骤2/4] 筛选新闻...")
    keywords = loader.keywords
    category_rules = loader.get("category_rules", {})
    news_filter = NewsFilter(
        must_include=keywords.get("must_include", []),
        exclude=keywords.get("exclude", []),
        category_rules=category_rules,
        logger=logger,
        must_include_weak=keywords.get("must_include_weak", []),
    )
    filtered_articles = news_filter.get_passed_articles(articles)
    logger.info(f"筛选后保留 {len(filtered_articles)} 条新闻")

    if not filtered_articles:
        logger.warning("筛选后无相关新闻，日报生成终止")
        return

    # 2.5 按相关性排序并限制最多20条
    MAX_ARTICLES = 20
    if len(filtered_articles) > MAX_ARTICLES:
        # 按相关性评分降序排列
        filtered_articles.sort(key=lambda a: a.relevance_score, reverse=True)
        filtered_articles = filtered_articles[:MAX_ARTICLES]
        logger.info(f"按相关性筛选后保留 {len(filtered_articles)} 条新闻（上限{MAX_ARTICLES}条）")

    # 打印筛选结果概览
    for article in filtered_articles:
        logger.info(f"  ✓ [{article.category}] {article.title[:50]}")

    # 检查四个层面是否都有新闻
    category_order = ["国家/政策层", "行业/市场层", "技术/研发层", "业务/竞争层"]
    found_categories = set(a.category for a in filtered_articles)
    for cat in category_order:
        if cat not in found_categories:
            logger.warning(f"⚠️ 「{cat}」暂无新闻，建议添加更多数据源或手动输入URL补充")

    if dry_run:
        logger.info("[DRY RUN] 仅展示筛选结果，不生成日报")
        return

    # 3. LLM分析
    logger.info("[步骤3/4] LLM分析生成摘要...")
    llm = LLMGenerator(loader.config, logger)

    analysis_results = []
    for i, article in enumerate(filtered_articles, 1):
        logger.info(f"  分析第 {i}/{len(filtered_articles)} 条: {article.title[:30]}...")
        analysis = llm.analyze_article(article)
        analysis_results.append(analysis)
        # 请求间隔
        if llm.is_available():
            interval = loader.get("llm.request_interval", 1)
            time.sleep(interval)

    # 生成日报总结（传入分析结果以获得更高质量的总结）
    logger.info("  生成日报总结...")
    summary = llm.generate_summary(filtered_articles, analysis_results)

    # 4. 生成日报文件
    logger.info("[步骤4/4] 生成日报文件...")
    report_gen = DailyReportGenerator(loader.config, logger)
    if report_date is None:
        report_date = datetime.now()
    output_files = report_gen.generate(filtered_articles, analysis_results, summary, report_date)

    elapsed = time.time() - start_time
    logger.info(f"日报生成完成！耗时 {elapsed:.1f} 秒")
    logger.info(f"  Word: {output_files.get('docx', 'N/A')}")
    logger.info(f"  Markdown: {output_files.get('md', 'N/A')}")

    # 发送通知
    if output_files:
        notification.notify(
            title="洞察信息收集日报",
            content=f"今日日报已生成，共收录 {len(filtered_articles)} 条新闻。\n\n"
                    f"**要点概括：**\n" +
                    "\n".join(f"{i+1}. {p}" for i, p in enumerate(summary.get("要点概括", [])[:3])),
            file_path=output_files.get("docx"),
        )

    return output_files


def run_biweekly_report(loader: ConfigLoader, logger, notification):
    """执行半月报生成流程"""
    logger.info("=" * 60)
    logger.info("开始生成半月报")
    logger.info("=" * 60)

    biweekly_gen = BiweeklyReportGenerator(loader.config, logger)

    # 获取标记的新闻
    marked = biweekly_gen.get_marked_news()
    if not marked:
        logger.warning("无标记的重要新闻，半月报生成终止")
        logger.info("提示：使用 'python -m src.main mark --date YYYYMMDD --index N' 标记重要新闻")
        return

    logger.info(f"共 {len(marked)} 条标记新闻")

    output_files = biweekly_gen.generate()
    if output_files:
        logger.info(f"半月报生成完成！")
        logger.info(f"  Word: {output_files.get('docx', 'N/A')}")
        logger.info(f"  Markdown: {output_files.get('md', 'N/A')}")

        notification.notify(
            title="洞察信息半月报",
            content=f"半月报已生成，共汇总 {len(marked)} 条重要新闻。",
            file_path=output_files.get("docx"),
        )

    return output_files


def run_mark_news(loader: ConfigLoader, logger, date_str: str, index: int, unmark: bool = False):
    """标记/取消标记重要新闻"""
    biweekly_gen = BiweeklyReportGenerator(loader.config, logger)

    if unmark:
        # 取消标记需要URL，这里简化处理
        logger.info("取消标记功能请直接编辑 data/marked_news.json")
        return

    # 加载最近的日报数据来查找新闻
    date_format = loader.get("output.date_format", "%Y%m%d")
    daily_dir = Path(loader.get("output.daily_dir", "output/daily"))

    # 查找对应的日报Markdown文件
    md_file = daily_dir / f"洞察信息收集日报_{date_str}.md"
    if not md_file.exists():
        logger.error(f"未找到日期 {date_str} 的日报文件: {md_file}")
        return

    # 解析Markdown文件中的新闻
    content = md_file.read_text(encoding="utf-8")
    lines = content.split("\n")

    news_items = []
    current_category = ""
    for line in lines:
        if line.startswith("## "):
            current_category = line[3:].strip()
        elif line.startswith("|") and not line.startswith("| 时间") and not line.startswith("|------"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 8:
                news_items.append({
                    "category": current_category,
                    "time": parts[1],
                    "event": parts[2],
                    "url": parts[7].strip("[]()").replace("链接", "").strip(),
                })

    if index < 1 or index > len(news_items):
        logger.error(f"索引超出范围，共 {len(news_items)} 条新闻（索引从1开始）")
        return

    item = news_items[index - 1]
    article_data = {
        "title": item["event"],
        "url": item["url"],
        "source": "日报标记",
        "category": item["category"],
        "publish_time": None,
        "content": item["event"],
    }

    from src.fetchers.base import NewsArticle
    article = NewsArticle(**{k: v for k, v in article_data.items() if k in NewsArticle.__dataclass_fields__})
    biweekly_gen.mark_news(article)

    logger.info(f"已标记第 {index} 条新闻为重要：{item['event'][:50]}")
    logger.info("该新闻已添加到半月报候选列表")


@click.group()
@click.option("--config-dir", default=None, help="配置文件目录路径")
@click.pass_context
def cli(ctx, config_dir):
    """岚图汽车 · 洞察信息收集日报 · 自动化程序"""
    ctx.ensure_object(dict)
    loader, logger, notification = create_components(config_dir)
    ctx.obj["loader"] = loader
    ctx.obj["logger"] = logger
    ctx.obj["notification"] = notification


@cli.command()
@click.option("--urls", multiple=True, help="手动添加的新闻URL")
@click.option("--dry-run", is_flag=True, help="仅展示筛选结果，不生成日报")
@click.option("--date", "date_str", default=None, help="指定日报日期（YYYYMMDD格式），默认今天")
@click.pass_context
def daily(ctx, urls, dry_run, date_str):
    """生成日报"""
    loader = ctx.obj["loader"]
    logger = ctx.obj["logger"]
    notification = ctx.obj["notification"]

    report_date = None
    if date_str:
        try:
            report_date = datetime.strptime(date_str, "%Y%m%d")
        except ValueError:
            logger.error(f"日期格式错误，请使用YYYYMMDD格式，如：20260403")
            return

    manual_urls = list(urls) if urls else None
    run_daily_report(loader, logger, notification, manual_urls, dry_run, report_date)


@cli.command()
@click.pass_context
def biweekly(ctx):
    """生成半月报"""
    loader = ctx.obj["loader"]
    logger = ctx.obj["logger"]
    notification = ctx.obj["notification"]

    run_biweekly_report(loader, logger, notification)


@cli.command()
@click.option("--date", "date_str", required=True, help="日报日期（YYYYMMDD格式）")
@click.option("--index", type=int, required=True, help="新闻索引（从1开始）")
@click.option("--unmark", is_flag=True, help="取消标记")
@click.pass_context
def mark(ctx, date_str, index, unmark):
    """标记重要新闻（添加到半月报）"""
    loader = ctx.obj["loader"]
    logger = ctx.obj["logger"]

    run_mark_news(loader, logger, date_str, index, unmark)


@cli.command()
@click.pass_context
def status(ctx):
    """查看配置状态"""
    loader = ctx.obj["loader"]
    logger = ctx.obj["logger"]

    click.echo("=" * 50)
    click.echo("岚图洞察日报 · 配置状态")
    click.echo("=" * 50)

    # LLM状态
    api_key = loader.get("llm.api_key", "")
    if api_key and api_key != "YOUR_DEEPSEEK_API_KEY":
        click.echo(f"✅ LLM API: 已配置 (model={loader.get('llm.model')})")
    else:
        click.echo("⚠️  LLM API: 未配置（将使用模板填充模式）")

    # 数据源状态
    rss_sources = loader.get("sources.rss", [])
    web_sources = loader.get("sources.web", [])
    enabled_rss = [s["name"] for s in rss_sources if s.get("enabled", True)]
    enabled_web = [s["name"] for s in web_sources if s.get("enabled", True)]

    click.echo(f"\n📡 RSS源 ({len(enabled_rss)}个已启用):")
    for name in enabled_rss:
        click.echo(f"   • {name}")

    click.echo(f"\n🌐 网页源 ({len(enabled_web)}个已启用):")
    for name in enabled_web:
        click.echo(f"   • {name}")

    # 关键词状态
    keywords = loader.keywords
    click.echo(f"\n🔑 必收关键词: {len(keywords.get('must_include', []))}个")
    click.echo(f"🚫 排除关键词: {len(keywords.get('exclude', []))}个")

    # 输出目录
    click.echo(f"\n📁 输出目录: {loader.output_dir}")
    click.echo(f"📁 数据目录: {loader.data_dir}")

    # 标记新闻
    marked_file = loader.data_dir / "marked_news.json"
    if marked_file.exists():
        with open(marked_file, "r", encoding="utf-8") as f:
            marked = json.load(f)
        click.echo(f"\n⭐ 已标记重要新闻: {len(marked)}条")
    else:
        click.echo("\n⭐ 已标记重要新闻: 0条")

    click.echo("\n" + "=" * 50)


@cli.command()
@click.pass_context
def schedule(ctx):
    """启动定时调度（持续运行）"""
    import schedule as sched
    import time

    loader = ctx.obj["loader"]
    logger = ctx.obj["logger"]
    notification = ctx.obj["notification"]

    daily_time = loader.get("schedule.daily_time", "07:00")
    biweekly_days = loader.get("schedule.biweekly_days", [1, 16])

    click.echo(f"定时调度已启动")
    click.echo(f"  日报生成时间: 每天 {daily_time}")
    click.echo(f"  半月报生成: 每月 {biweekly_days} 号")
    click.echo("  按 Ctrl+C 停止\n")

    # 配置每日任务
    sched.every().day.at(daily_time).do(
        lambda: run_daily_report(loader, logger, notification)
    )

    # 配置半月报任务
    today = datetime.now()
    for day in biweekly_days:
        try:
            sched.every().month.on(day).at(daily_time).do(
                lambda: run_biweekly_report(loader, logger, notification)
            )
        except Exception:
            pass

    # 运行调度循环
    while True:
        try:
            sched.run_pending()
            time.sleep(60)
        except KeyboardInterrupt:
            click.echo("\n调度已停止")
            break
        except Exception as e:
            logger.error(f"调度异常: {e}")
            time.sleep(60)


if __name__ == "__main__":
    cli()
