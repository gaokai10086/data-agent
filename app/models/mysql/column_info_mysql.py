"""
================================================================================
模块名称：ColumnInfo ORM 实体模型 (UUID 字符串主键版)
模块路径：app/models/mysql/column_info_mysql.py
================================================================================
"""
import uuid
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


def generate_uuid() -> str:
    """生成 32 位唯一无横杠 UUID 字符串"""
    return uuid.uuid4().hex


class ColumnInfoMySQL(Base):
    __tablename__ = "column_info"
    __table_args__ = (
        UniqueConstraint("table_id", "name", name="uq_column_info_table_name"),
    )

    # 重点修改：添加 default=generate_uuid，实例化或入库时自动注入非空主键
    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=generate_uuid,
        comment="列编号"
    )
    name: Mapped[str | None] = mapped_column(
        String(128),
        comment="列名称"
    )
    type: Mapped[str | None] = mapped_column(
        String(64),
        comment="数据类型"
    )
    role: Mapped[str | None] = mapped_column(
        String(32),
        comment="列类型(primary_key,foreign_key,measure,dimension)"
    )
    examples: Mapped[dict | list | None] = mapped_column(
        JSON,
        comment="数据示例"
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        comment="列描述"
    )
    alias: Mapped[dict | list | None] = mapped_column(
        JSON,
        comment="列别名"
    )
    table_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("table_info.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属表编号"
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
