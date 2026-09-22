"""DW 表间 Join 关系的结构化元数据。"""
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class TableRelationMySQL(Base):
    """只保存经确认的字段级关联，供 SQL 生成与校验使用。"""
    __tablename__ = "table_relation"
    __table_args__ = (
        UniqueConstraint(
            "source_table_id", "source_column_id", "target_table_id", "target_column_id",
            name="uq_table_relation_columns",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("table_info.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_column_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("column_info.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("table_info.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_column_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("column_info.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(
        String(32), default="foreign_key", nullable=False, comment="关系类型，例如 foreign_key"
    )
    join_operator: Mapped[str] = mapped_column(
        String(8), default="=", nullable=False, comment="Join 操作符"
    )
    created_at: Mapped[object] = mapped_column(DateTime, server_default=func.now(), nullable=False)
