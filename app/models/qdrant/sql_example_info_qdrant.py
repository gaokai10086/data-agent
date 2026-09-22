"""
================================================================================
模块名称：Few-Shot 金标 SQL 向量 Payload 实体契约 (TypedDict 规范) 📦
文件路径：app/models/qdrant/sql_example_qdrant.py
================================================================================
"""
from typing import TypedDict, List


# 金标 SQL 计算范式的固定枚举，供录入表单与后端校验共用。
CALCULATION_PATTERNS: tuple[str, ...] = (
    "单表切片与基础聚合",
    "跨表深度关联分析",
    "组内排序与排名截断",
    "开窗统计与累计占比",
    "时间序列与周期比对",
    "交叉透视与条件聚合",
)

# 当前智能问数只接入 MySQL，未来扩展 PostgreSQL、ClickHouse 时在此追加。
SQL_DIALECTS: tuple[str, ...] = ("mysql",)


class SQLExampleQdrant(TypedDict):
    """
    Qdrant 中 sql_examples 集合存储的完整 Payload 结构
    与 ColumnInfoQdrant 保持同构设计规范
    """
    id: str                 # 确定性 UUID (uuid5)
    question: str           # 业务自然语言提问 (向量化文本)
    sql: str                # 标准清洗后的金标 SQL 代码
    pattern: str            # 6 大通用数仓计算范式之一
    tables: List[str]       # 涉及的物理底表名单
    dialect: str            # SQL 方言 (默认 "mysql")
    difficulty: str         # 复杂度等级
