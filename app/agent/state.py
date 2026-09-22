"""
================================================================================
模块名称：智能问数状态机数据契约 (DataAgentState)
模块路径：app/agent/state.py
核心职责：
    1. 定义整个 LangGraph 状态机在各个 Node 之间流动的数据总线格式
    2. 支持多意图路由、多路召回、SQL 生成自愈、物理数据拉取与 BI 报表出具
================================================================================
"""
from typing import Any, Literal, TypedDict
import yaml

from app.models.es.value_info_es import ValueInfoES
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant
from app.models.qdrant.sql_example_info_qdrant import SQLExampleQdrant


class MetricInfoState(TypedDict, total=False):
    """预定义指标数据结构"""
    name: str           # 指标名称，如 "销售额占比"
    description: str    # 指标描述，如 "各品类销售额占总销售额的比例"
    alias: list[str]    # 指标别名，如 ["销售占比", "占比"]
    metric_key: str     # 指标英文标识，例如 sales_amount
    formula: str        # 计算公式
    fact_table: str     # 主事实表
    measure_column: str # 核心度量字段
    aggregation: str    # 聚合方式
    time_column: str    # 默认时间字段
    filters: list[dict] # 固定过滤条件
    dimensions: list[dict]  # 允许分析维度


class ColumnInfoState(TypedDict):
    """数仓表中字段的数据结构"""
    name: str           # 字段物理名，如 "category"
    type: str           # 字段物理类型，如 "varchar"、"int"
    role: str           # 字段角色（主键/外键/维度/度量）
    description: str    # 字段业务含义描述
    alias: list[str]    # 别名候选词
    examples: list[Any] # 示例枚举值


class TableInfoState(TypedDict):
    """数仓表结构全貌"""
    name: str                       # 表名
    role: str                       # 表角色（事实表/维度表）
    description: str                # 表业务描述
    columns: list[ColumnInfoState]  # 表内各字段列表


class TableRelationState(TypedDict):
    """已确认的表间 Join 关系。"""
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    operator: str


class DateInfoState(TypedDict):
    """数仓当前时间基准锚点"""
    date: str       # 日期，如 "2026-09-08"
    weekday: str    # 星期，如 "Tuesday"
    quarter: str    # 季度，如 "Q3"


class DBInfoState(TypedDict):
    """数仓方言与版本"""
    dialect: str    # 如 "mysql"
    version: str    # 如 "8.0.32"


class DataAgentState(TypedDict):
    """
    智能问数全局状态总线 (Global State Pipeline)
    """
    # ---------------- 1. 用户输入层 ----------------
    query: str  # 用户的原始提问

    # ---------------- 2. 意图分流与直接对话层 (新增) ----------------
    intent: Literal["query_database", "direct_answer"] | None  # 意图标识
    final_answer: str | None                                   # 闲聊直接回答内容
    error_response: str | None                                 # 面向用户的业务错误提示
    is_valid_query: bool                                       # 是否为合法问数提问

    # ---------------- 3. 实体多路召回与上下文绑定 ----------------
    keywords: list[str]                        # 提取的关键词
    retrieved_metrics: list[MetricInfoQdrant]  # Qdrant 召回的指标
    retrieved_columns: list[ColumnInfoQdrant]  # Qdrant 召回的字段
    retrieved_values: list[ValueInfoES]        # ES 精确召回的值
    retrieved_examples: list[SQLExampleQdrant]
    table_infos: list[TableInfoState]          # 过滤组装后的候选表及字段元数据
    table_relations: list[TableRelationState]  # 候选表之间已确认的 Join 关系
    metric_infos: list[MetricInfoState]        # 过滤组装后的候选指标
    date_info: DateInfoState                   # 基准时间基准
    db_info: DBInfoState                       # 方言与版本信息

    # ---------------- 4. SQL 生成与自愈反思回路 ----------------
    sql: str                # 生成或修正后的执行 SQL
    error: str | None       # EXPLAIN 预检报错日志 (通过时为 None)
    retry_count: int        # 连续修正重试计数器

    # ================ 5. 数据执行与结果输出层 (新增) ================
    sql_result: list[dict[str, Any]] | str | None  # SQL 在业务库执行后返回的真实数据结果 (list[dict])


if __name__ == '__main__':
    columns = [
        ColumnInfoState(
            name="id",
            type="int",
            role="primary_key",
            description="编号",
            alias=["编号"],
            examples=["1", "2", "3"]
        ),
        ColumnInfoState(
            name="name",
            type="varchar",
            role="dimension",
            description="名称",
            alias=["名称"],
            examples=["张三", "李四", "王五"]
        ),
        ColumnInfoState(
            name="age",
            type="int",
            role="measure",
            description="年龄",
            alias=["年龄"],
            examples=["18", "19", "20"]
        )
    ]
    table = TableInfoState(
        name="user",
        role="fact",
        description="用户信息",
        columns=columns
    )

    print(yaml.safe_dump(table, allow_unicode=True, sort_keys=False))
