"""
================================================================================
模块名称：MetricInfo ORM 实体模型 (标准齐平版)
模块路径：app/models/mysql/metric_info_mysql.py
================================================================================
"""
import uuid
from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.models.mysql.base import Base


def generate_uuid() -> str:
    return uuid.uuid4().hex


class MetricInfoMySQL(Base):
    __tablename__ = "metric_info"

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=generate_uuid,
        comment="指标唯一编号"
    )
    name: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        comment="指标名称"
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        default="",
        comment="指标口径描述"
    )
    # 核心补齐：加上 formula 列
    formula: Mapped[str | None] = mapped_column(
        Text,
        default="",
        comment="指标计算公式"
    )
    relevant_columns: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=list,
        comment="关联的物理字段列表"
    )
    alias: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=list,
        comment="业务同义词或口语别名"
    )
    metric_key: Mapped[str | None] = mapped_column(
        String(128),
        unique=True,
        index=True,
        comment="稳定英文标识，例如 sales_amount"
    )
    fact_table: Mapped[str | None] = mapped_column(
        String(128),
        comment="主事实表名"
    )
    measure_column: Mapped[str | None] = mapped_column(
        String(128),
        comment="核心度量字段名"
    )
    aggregation: Mapped[str | None] = mapped_column(
        String(32),
        comment="聚合方式，例如 SUM 或 COUNT_DISTINCT"
    )
    time_column: Mapped[str | None] = mapped_column(
        String(128),
        comment="默认时间字段名"
    )
    filters: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=list,
        comment="固定过滤条件"
    )
    dimensions: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=list,
        comment="允许分析的维度定义"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="draft",
        index=True,
        nullable=False,
        comment="状态：draft、published、deprecated"
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="指标版本"
    )
    created_at: Mapped[object] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )
    updated_at: Mapped[object] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间"
    )
