"""
LLM生成模块 - 通过DeepSeek API生成摘要、洞察、影响分析
"""
import json
import time
from typing import Optional
from openai import OpenAI

from ..fetchers.base import NewsArticle


# 单条新闻分析 Prompt（基于参考模板优化）
SINGLE_NEWS_PROMPT = """你是一位资深汽车行业战略分析师，服务于岚图汽车（VOYAH）企业战略部。请根据以下新闻信息，生成日报所需的结构化分析字段。

## 新闻信息
- 标题：{title}
- 正文：{content}
- 来源：{source}
- 层面分类：{category}

## 输出要求
请严格按以下JSON格式输出，每个字段都必须认真填写，不可留空或输出占位符：

{{
  "事件内容": "50-70字。要求：简洁叙述事件核心事实，必须包含关键数据（销量数字、金额、百分比等）和涉及的产品/技术名称。不要只写标题，但严禁冗长展开，控制在70字以内。",
  "参与方": "列出新闻中涉及的所有组织、企业、政府部门、关键人物名称，用顿号（、）分隔。例如：比亚迪、工信部、朱江明（零跑汽车创始人）。不可遗漏重要参与方。",
  "事件影响": "50-70字。要求：简洁分析该事件对行业/产业链/市场的影响，使用具体描述。严禁超过70字。",
  "事件洞察": "50-70字。要求：简洁提炼事件背后的深层逻辑或趋势变化。严禁超过70字。",
  "对岚图的影响及启示": "不超过40字。要求：以行动建议形式输出，直接关联业务。例如：'需加速XX布局''可借鉴XX模式''应关注XX风险'。不要提及'岚图'二字。严禁超过40字，这是硬性要求。"
}}

## 质量标准（参考示例）

事件内容示例："比亚迪3月销量环比增57.85%至30万辆重回销冠，闪充2.0（二代刀片电池+2万座闪充站）及新车发布带动订单回暖，出口目标提至150万辆。"
参与方示例："比亚迪"
事件影响示例："比亚迪国内销量回稳、出口创新高，闪充2.0推动订单回暖，预计4月继续上扬，巩固行业销冠地位。"
事件洞察示例："技术迭代（闪充2.0）是销量反转关键，出口成为增长新引擎，国内市场竞争因比亚迪回稳更趋激烈。"
对岚图的影响及启示示例："面临比亚迪技术与市场双重挤压，需加速技术迭代、拓展出口以提升竞争力。"

## 注意事项
1. 每个字段都必须认真填写，绝不可输出"需人工补充"等占位符
2. 必须保留所有关键实体名称（企业名、人名、政策名、产品名、技术名），不可用"某企业""某品牌"替代
3. 事件内容应基于新闻事实，简洁精炼
4. **字数是硬性要求**：事件内容/事件影响/事件洞察严格控制在50-70字，对岚图启示严格不超过40字。超出字数视为不合格输出
5. 对岚图的启示要具体、可执行，一句话说清"""

# 日报总结 Prompt（基于参考模板优化）
DAILY_SUMMARY_PROMPT = """你是一位资深汽车行业战略分析师，服务于岚图汽车企业战略部。基于以下今日新闻分析结果，生成日报的总结模块。

## 今日新闻分析结果（共{count}条）

{detailed_summaries}

## 输出要求
请严格按以下JSON格式输出，每个字段都必须认真填写：

{{
  "要点概括": [
    "第1条要点概括，50字左右，简洁精炼，包含关键数据",
    "第2条要点概括，50字左右",
    "第3条要点概括，50字左右",
    "第4条要点概括，50字左右",
    "第5条要点概括，50字左右",
    "第6条要点概括，50字左右"
  ],
  "战略意义": [
    "第1个战略方向，100-150字。要求：提炼对行业或岚图有长期战略价值的方向，结合具体事件分析其深层含义和长期影响。例如：'XX政策通过减税激励核心技术攻关，直接服务于汽车产业链降本与装备自主化，旨在降低对进口设备的依赖，从制造根基上提升产业安全性。'",
    "第2个战略方向，100-150字",
    "第3个战略方向，100-150字"
  ],
  "风险预警": [
    "第1个风险点，100-150字。要求：识别具体风险场景，描述风险触发条件和可能后果，并给出应对方向建议。例如：'XX事件暴露了XX领域的系统性风险，此类事件不仅直接影响用户体验与品牌信任，更可能引发监管收紧，需提前构建冗余保障。'",
    "第2个风险点，100-150字"
  ],
  "近期关注": [
    "第1个建议跟踪的动态，50字左右。要求：明确跟踪对象和预判方向。例如：'关注奥迪A6L e-tron上市后订单情况，预判30-45万市场格局变化。'",
    "第2个建议跟踪的动态，50字左右"
  ]
}}

## 质量标准（参考示例）

要点概括示例："3月制造业PMI重返扩张区间至50.4%，经济景气回升，生产和消费信心恢复，为汽车行业提供积极宏观信号。"
战略意义示例："产业链自主强化：工业母机加计扣除政策通过减税激励核心技术攻关，直接服务于汽车产业链降本与装备自主化。此举旨在降低对进口设备的依赖，从制造根基上提升产业安全性与竞争力，为应对复杂国际环境储备基础能力。"
风险预警示例："海外市场高壁垒与政策波动风险：加拿大电动车市场虽出现关税松动，但配额限制、CMVSS认证成本及本土产业保护政策构成实际进入门槛。此外，美欧等主要市场仍存在关税上调或政策反复的可能，海外拓展面临显著的政策不确定性与合规成本压力。"
近期关注示例："北京车展新产品集中发布与市场策略动向，关注传统车企与合资品牌在新能源领域的竞争动作，预判二季度市场格局变化。"

## 注意事项
1. 要点概括应覆盖今日最重要的6条信息，每条50字左右，简洁精炼
2. 战略意义应从政策、技术、市场、全球化等维度提炼，每条100-150字
3. 风险预警应识别具体可感知的风险，给出应对方向，每条100-150字
4. 近期关注应明确跟踪对象和预判方向，每条50字左右
5. 绝不可输出"需人工补充"等占位符
6. 不要出现事实错误，语言应通顺专业"""


class LLMGenerator:
    """LLM内容生成器"""

    def __init__(self, config: dict, logger=None):
        self.logger = logger
        llm_config = config.get("llm", {})
        self.model = llm_config.get("model", "deepseek-chat")
        self.temperature = llm_config.get("temperature", 0.3)
        self.max_tokens = llm_config.get("max_tokens", 2000)
        self.timeout = llm_config.get("timeout", 60)
        self.request_interval = llm_config.get("request_interval", 1)

        # 初始化OpenAI客户端（兼容DeepSeek API）
        api_key = llm_config.get("api_key", "")
        base_url = llm_config.get("base_url", "https://api.deepseek.com/v1")

        if not api_key or api_key == "YOUR_DEEPSEEK_API_KEY":
            self.client = None
            if self.logger:
                self.logger.warning("[LLM] API Key未配置，将使用模板填充模式")
        else:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self.timeout,
            )

    def is_available(self) -> bool:
        """检查LLM是否可用"""
        return self.client is not None

    def analyze_article(self, article: NewsArticle) -> Optional[dict]:
        """分析单条新闻，生成结构化字段"""
        if not self.client:
            return self._template_fill(article)

        # 使用完整正文以获得更好的分析质量
        content = article.full_content or article.content
        content_for_prompt = content[:1000] if len(content) > 1000 else content

        prompt = SINGLE_NEWS_PROMPT.format(
            title=article.title,
            content=content_for_prompt,
            source=article.source,
            category=article.category,
        )

        try:
            result = self._call_api(prompt, max_tokens=1500)
            if result:
                # 验证所有字段都已填写
                validated = self._validate_analysis(result)
                return validated
            return self._template_fill(article)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[LLM] 分析文章失败: {article.title[:30]}... - {e}")
            return self._template_fill(article)

    def generate_summary(self, articles: list, analysis_results: list = None) -> Optional[dict]:
        """基于所有新闻生成日报总结"""
        if not self.client:
            return self._template_summary(articles)

        # 构建详细摘要（包含分析结果，供LLM参考）
        detailed_summaries = []
        for i, art in enumerate(articles):
            summary_line = f"### 新闻{i+1}：{art.title}"
            summary_line += f"\n- 层面分类：{art.category}"
            summary_line += f"\n- 来源：{art.source}"
            if analysis_results and i < len(analysis_results):
                analysis = analysis_results[i]
                summary_line += f"\n- 事件内容：{analysis.get('事件内容', '')}"
                summary_line += f"\n- 参与方：{analysis.get('参与方', '')}"
                summary_line += f"\n- 事件影响：{analysis.get('事件影响', '')}"
                summary_line += f"\n- 事件洞察：{analysis.get('事件洞察', '')}"
                summary_line += f"\n- 对岚图启示：{analysis.get('对岚图的影响及启示', '')}"
            detailed_summaries.append(summary_line)

        prompt = DAILY_SUMMARY_PROMPT.format(
            count=len(articles),
            detailed_summaries="\n\n".join(detailed_summaries),
        )

        try:
            result = self._call_api(prompt, max_tokens=4000)
            if result:
                validated = self._validate_summary(result)
                return validated
            return self._template_summary(articles)
        except Exception as e:
            if self.logger:
                self.logger.error(f"[LLM] 生成总结失败: {e}")
            return self._template_summary(articles)

    def _call_api(self, prompt: str, max_tokens: int = None) -> Optional[dict]:
        """调用DeepSeek API"""
        if max_tokens is None:
            max_tokens = self.max_tokens

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": "你是一位服务于岚图汽车企业战略部的资深汽车行业战略分析师。你的任务是基于新闻信息生成高质量的结构化分析。请始终以JSON格式输出，每个字段都必须认真填写完整内容，绝不可输出占位符或留空。"},
                {"role": "user", "content": prompt},
            ],
            temperature=self.temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content.strip()
        # 提取JSON（可能被markdown代码块包裹）
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        return json.loads(content)

    @staticmethod
    def _validate_analysis(result: dict) -> dict:
        """验证分析结果，确保所有字段都已填写"""
        required_fields = ["事件内容", "参与方", "事件影响", "事件洞察", "对岚图的影响及启示"]
        placeholders = ["需人工补充", "暂无", "无", "N/A", "待补充", "（", "（("]

        for field in required_fields:
            value = result.get(field, "")
            if not value or any(p in value for p in placeholders):
                # 如果字段为空或含占位符，尝试补充
                if field == "事件内容" and result.get("事件内容"):
                    pass  # 事件内容至少有标题
                elif field == "参与方":
                    result[field] = result.get("参与方") or "（待补充）"
                else:
                    result[field] = result.get(field) or "（待补充）"

        return result

    @staticmethod
    def _validate_summary(result: dict) -> dict:
        """验证总结结果"""
        placeholders = ["需人工补充", "暂无", "待补充"]

        for section in ["要点概括", "战略意义", "风险预警", "近期关注"]:
            items = result.get(section, [])
            validated = []
            for item in items:
                if item and not any(p in item for p in placeholders):
                    validated.append(item)
            if validated:
                result[section] = validated

        return result

    @staticmethod
    def _template_fill(article: NewsArticle) -> dict:
        """模板填充模式（LLM不可用时的降级方案）"""
        # 基于标题和内容生成尽可能完整的模板
        title = article.title
        content = article.content or ""

        # 事件内容：标题 + 内容前100字
        event_content = title
        if content and len(content) > 20:
            supplement = content[:150].replace("\n", " ").strip()
            if supplement != title:
                event_content = f"{title}。{supplement}"

        return {
            "事件内容": event_content,
            "参与方": "（配置API Key后自动提取）",
            "事件影响": "（配置DeepSeek API Key后自动生成）",
            "事件洞察": "（配置DeepSeek API Key后自动生成）",
            "对岚图的影响及启示": "（配置DeepSeek API Key后自动生成）",
        }

    @staticmethod
    def _template_summary(articles: list) -> dict:
        """模板填充模式的总结"""
        key_points = []
        for a in articles[:6]:
            point = a.title
            if a.content and len(a.content) > 20:
                point = f"{a.title}。{a.content[:80].replace(chr(10), ' ').strip()}"
            key_points.append(point)

        return {
            "要点概括": key_points if key_points else ["（配置API Key后自动生成）"],
            "战略意义": ["（配置DeepSeek API Key后自动生成）", "（配置DeepSeek API Key后自动生成）", "（配置DeepSeek API Key后自动生成）"],
            "风险预警": ["（配置DeepSeek API Key后自动生成）", "（配置DeepSeek API Key后自动生成）"],
            "近期关注": ["（配置DeepSeek API Key后自动生成）", "（配置DeepSeek API Key后自动生成）"],
        }
