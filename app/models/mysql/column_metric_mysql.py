from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mysql.base import Base


class ColumnMetricMySQL(Base):
    __tablename__ = "column_metric"

    column_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("column_info.id", ondelete="CASCADE"),
        primary_key=True,
        comment="列编号"
    )
    metric_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("metric_info.id", ondelete="CASCADE"),
        primary_key=True,
        comment="指标编号"
    )
    created_at: Mapped[object] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        comment="创建时间"
    )
