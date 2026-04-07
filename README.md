# 岚图汽车 · 洞察信息收集日报 · 自动化程序

## 项目简介

为岚图汽车企业战略部开发的自动化日报生成工具。每日从多个公开汽车行业信息源抓取新闻，通过关键词筛选和LLM智能分析，自动生成结构化的「洞察信息收集日报」，支持Word和Markdown格式输出。

## 功能特性

- 📰 **多源数据抓取**：支持RSS订阅、网页爬取、手动URL输入
- 🔍 **智能关键词筛选**：必收/排除关键词库，支持动态编辑
- 🏷️ **自动层面分类**：国家/政策层、行业/市场层、技术/研发层、业务/竞争层
- 🤖 **LLM智能摘要**：基于DeepSeek API生成事件影响、洞察、启示
- 📄 **双格式输出**：Word(.docx) + Markdown
- 📊 **半月报汇总**：标记重要新闻，自动生成半月报
- ⏰ **定时调度**：支持每日定时自动执行
- 🔔 **通知推送**：支持企业微信Webhook、邮件通知

## 快速开始

### 1. 安装依赖

```bash
cd voyan_insight_bot
pip install -r requirements.txt
```

### 2. 配置

编辑 `config/config.yaml`，填入您的DeepSeek API Key：

```yaml
llm:
  api_key: "sk-your-api-key-here"
```

根据需要调整数据源、关键词等配置。

### 3. 运行

```bash
# 生成今日日报
python -m src.main daily

# 生成半月报
python -m src.main biweekly

# 添加手动URL并生成日报
python -m src.main daily --urls "https://example.com/news1" "https://example.com/news2"

# 标记重要新闻（添加到半月报）
python -m src.main mark --date 20260405 --index 3

# 查看配置状态
python -m src.main status
```

### 4. 定时执行（Linux）

```bash
# 添加到crontab，每天7:00执行
crontab -e
# 添加：0 7 * * * cd /path/to/voyan_insight_bot && python -m src.main daily >> logs/cron.log 2>&1
```

## 项目结构

```
voyan_insight_bot/
├── config/
│   ├── config.yaml          # 主配置文件
│   └── keywords.json        # 关键词库
├── src/
│   ├── main.py              # 主入口
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py          # 抓取基类
│   │   ├── rss_fetcher.py   # RSS抓取器
│   │   └── web_fetcher.py   # 网页抓取器
│   ├── filters/
│   │   ├── __init__.py
│   │   └── news_filter.py   # 关键词筛选与分类
│   ├── generators/
│   │   ├── __init__.py
│   │   ├── llm_generator.py # LLM摘要生成
│   │   ├── daily_report.py  # 日报生成
│   │   └── biweekly_report.py # 半月报生成
│   └── utils/
│       ├── __init__.py
│       ├── config_loader.py # 配置加载
│       ├── logger.py        # 日志工具
│       └── notification.py  # 通知推送
├── output/
│   ├── daily/               # 日报输出
│   ├── biweekly/            # 半月报输出
│   └── archive/             # 历史归档
├── data/
│   └── marked_news.json     # 标记的重要新闻
├── requirements.txt
└── README.md
```

## 配置说明

### 数据源管理

在 `config/config.yaml` 的 `sources` 部分管理数据源：

```yaml
sources:
  rss:
    - name: "数据源名称"
      url: "RSS链接"
      enabled: true/false
  web:
    - name: "网站名称"
      url: "网站URL"
      enabled: true/false
```

### 关键词管理

编辑 `config/keywords.json`：
- `must_include`：必收关键词列表
- `exclude`：排除关键词列表

## 注意事项

1. 首次运行前请确保已配置DeepSeek API Key
2. 网页抓取可能因网站结构调整而失效，需定期维护CSS选择器
3. 请遵守目标网站的robots协议
4. 建议首次运行时使用 `--dry-run` 参数测试，确认无误后再正式运行
