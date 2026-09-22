"""指标的正确与错误示例，用于解释、测试和 Few-shot 参考。"""
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class MetricExampleMySQL(Base):
    __tablename__ = "metric_example"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    metric_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metric_info.id", ondelete="CASCADE"), nullable=False, index=True
    )
    example_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="示例类型：correct 或 wrong"
    )
    question: Mapped[str] = mapped_column(Text, nullable=False, comment="示例问题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="SQL 或指标解释")
    reason: Mapped[str | None] = mapped_column(Text, comment="正确或错误原因")
    created_at: Mapped[object] = mapped_column(DateTime, server_default=func.now(), nullable=False)
