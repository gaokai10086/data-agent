import jieba.analyse
from langgraph.runtime import Runtime

from app.agent.data_agent.context import DataAgentContext
from app.agent.data_agent.state import DataAgentState
from app.core.logging import logger
"""
1.节点在系统中的位置：整个系统的第一个节点，负责从用户问题中提取关键词。
2.节点的核心作用：从用户问题中提取关键信息
例如：
用户问题："统计一下2025年1月份各品类的销售额占比"
↓ extract_keywords 节点处理
提取结果：
{
    "keywords": ["2025年1月", "品类", "销售额", "占比"],
    "time_range": "2025-01-01 to 2025-01-31",
    "metrics": ["销售额"],
    "dimensions": ["品类"]
}
3. 为什么需要提取关键词？
# 用户可能这样问：
"统计2025年1月各品类销售额"，"查一下去年各分类的销售金额"，"帮我分析品类销售情况"，"各品类销售额占比是多少"

# 系统需要理解：
- 时间范围：2025年1月 / 去年 / 2024年
- 维度：品类 / 分类
- 指标：销售额 / 销售金额 / GMV
- 计算方式：占比 / 汇总 / 排名

4. 解决LLM理解语义
# LLM 可以理解：
"销售额" ≈ "销售金额" ≈ "GMV" ≈ "营业额"
"品类" ≈ "分类" ≈ "类别"
"去年" ≈ "2024年"（根据当前日期推断）
# 输出标准化的关键词：
keywords: ["销售额", "品类", "2024年"]
"""

def is_numeric(s: str) -> bool:
    """判断字符串是否为数值"""
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False

#  state 参数包含所有的状态数据， runtime参数： 运行时的环境
async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """
    关键词提取节点：从用户问题中提取关键信息（时间、维度、指标、计算方式等）
    """
    #  获得流式输出工具，用于实时向前端发送进度信息
    writer = runtime.stream_writer

    """
    为什么需要流式输出？
    【传统方式】
        用户提问 → 等待... → 一次性返回结果
             ↑
        用户看到空白，不知道在做什么

    【流式输出】
        用户提问 → "抽取关键词..." → "召回表信息..." → "生成SQL..." → 返回结果
                 ↓                ↓               ↓
        用户实时看到进度，体验更好
    """
    #
    writer({"stage": "抽取关键词"})


    #  读取用户的问题
    query = state["query"]

    # 对查询进行分词，只提取指定词性的词
    allow_pos = (
        "n",  # 名词: 数据、服务器、表格
        "nr",  # 人名: 张三、李四
        "ns",  # 地名: 北京、上海
        "nt",  # 机构团体名: 政府、学校、某公司
        "nz",  # 其他专有名词: Unicode、哈希算法、诺贝尔奖
        "v",  # 动词: 运行、开发
        "vn",  # 名动词: 工作、研究
        "a",  # 形容词: 美丽、快速
        "an",  # 名形词: 难度、合法性、复杂度
        "eng",  # 英文
        "i",  # 成语
        "l",  # 常用固定短语
    )

    """
    extract_tags(): 参数1： 要处理的文本，参数 2： 是否返回权重值 参数 3：词性过滤规则
    withWeight=True（如果设置为True），返回关键词和权重值，("统计", 0.6543),("品类", 0.5678),("销售额", 0.4567)
    权重越高，词越重要
    【拆解】
    第一部分：jieba.analyse.extract_tags(...)
    → 返回：["统计", "品类", "销售额"]
    
    第二部分：+ [query]
    → 列表拼接：["统计", "品类", "销售额"] + ["统计2025年1月各品类销售额"]
    
    最终结果：
    keywords = [
        "统计",
        "品类",
        "销售额",
        "统计2025年1月各品类销售额"  ← 原始问题也加入了 ]
    #  为什么要把原始问题也加入？
    原因防止jieba分词遗漏重要信息，如果不加原始问题：
    keywords = ["统计", "销售额"]
    → 召回时可能找不到"昨天"相关的字段
    
    加入原始问题：
    keywords = ["统计", "销售额", "统计昨天的销售额"]
    → 召回时会匹配整个问题，不会遗漏
    """
    keywords = jieba.analyse.extract_tags(query, withWeight=False, allowPOS=allow_pos) + [query]

    #  主要针对keywords的数据进行 过滤(只需要字符)，去重，转成列表
    """
    为什么过滤数值？
    例如：keywords = ["统计", "2025", "1", "品类", "销售额"]
    原因：
    1.数字一般不是关键字
    2.这种数字对后续召回没有意义
    3.减少噪音，提高准确率
    使用 set去重，但后续代码需要列表格式
    """
    keywords = list(set([w for w in keywords if not is_numeric(w)]))

    #  输出到日志文件，2026-06-30 11:00:00 INFO [extract_keywords] 关键词提取: ['统计', '品类', '销售额']
    logger.info(f"关键词提取: {keywords}")

    """
    为什么需要日志？
    1. 调试：查看关键词提取是否正确，例如：发现“昨天”没提取出来，可能是词性过滤的问题
    2. 监控：生产环境查看处理结果，例如：用户反馈查询不准确，可以看日志排查
    3. 问题排查：出错时追溯处理过程，例如：查询结果为空，看日志发现关键词提取有问题
    """

    #  返回的字典会自动合并到 state
    """
    state = {
    "query": "统计2025年1月各品类销售额",
    # ← 本函数会添加这个字段
    "keywords": ["统计", "品类", "销售额", "统计2025年1月各品类销售额"],
    # ← 其他字段保持不变
    "tables": [],
    "sql": "",
    
    """
    return {"keywords": keywords}
