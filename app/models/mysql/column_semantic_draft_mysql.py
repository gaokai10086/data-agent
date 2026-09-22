"""字段语义草稿：保存大模型推导、等待人工审核的字段业务语义。

设计原则：column_info 只保存已发布且可信的字段知识；本表保存候选内容，
只有审核通过后才会把内容发布回 column_info。
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.mysql.base import Base


def generate_uuid() -> str:
    """生成 32 位无横杠 UUID。"""
    return uuid.uuid4().hex


class ColumnSemanticDraftMySQL(Base):
    __tablename__ = "column_semantic_draft"
    __table_args__ = (
        UniqueConstraint("column_id", "revision", name="uq_column_semantic_draft_revision"),
    )

    id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=generate_uuid,
        comment="草稿唯一编号",
    )
    column_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("column_info.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="对应的物理字段编号",
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="同一字段的草稿版本号，从 1 开始递增",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        nullable=False,
        index=True,
        comment="pending、approved、rejected、superseded",
    )
    candidate_description: Mapped[str | None] = mapped_column(
        Text,
        default="",
        comment="模型推导的字段业务说明（候选）",
    )
    candidate_alias: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=list,
        comment="模型推导的字段别名与同义词（候选）",
    )
    candidate_value_examples: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=list,
        comment="从 DW 安全采样的真实枚举值（候选，不来自模型）",
    )
    candidate_role: Mapped[str | None] = mapped_column(
        String(32),
        comment="模型建议的字段角色，仅供参考，不自动发布",
    )
    evidence: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=dict,
        comment="推导依据：物理注释、采样策略判定、真实样例等",
    )
    model_name: Mapped[str | None] = mapped_column(String(128), comment="生成草稿的模型")
    prompt_version: Mapped[str | None] = mapped_column(String(64), comment="生成草稿的 Prompt 版本")
    reviewer: Mapped[str | None] = mapped_column(String(64), comment="审核人")
    review_comment: Mapped[str | None] = mapped_column(Text, comment="审核意见")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="审核时间")
    published_content: Mapped[dict | list | None] = mapped_column(
        JSON,
        default=dict,
        comment="审核通过时实际写入 column_info 的内容，用于审计",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )
