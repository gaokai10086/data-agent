"""
================================================================================
模块名称：TableInfo ORM 实体模型 (自增整数主键版)
模块路径：app/models/mysql/table_info_mysql.py
================================================================================
"""
from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.mysql.base import Base


class TableInfoMySQL(Base):
    __tablename__ = "table_info"

    # 1. 类型调整为 int，并显式加上 autoincrement=True
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="表自增主键ID"
    )
    name: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        comment="物理表名"
    )
    role: Mapped[str | None] = mapped_column(
        String(32),
        default="dimension",
        comment="表类型(fact/dim)"
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        default="",
        comment="表描述"
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
