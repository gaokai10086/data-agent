"""
================================================================================
模块名称：系统元数据库仓储层 (Meta Repository)
模块路径：app/repositories/mysql/meta_mysql_repository.py
核心职责：
    提供对 meta 库中 table_info, column_info, metric_info 的原子读写操作
================================================================================
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.table_relation_mysql import TableRelationMySQL
from app.models.mysql.column_metric_mysql import ColumnMetricMySQL
from app.models.mysql.column_semantic_draft_mysql import ColumnSemanticDraftMySQL
from app.models.mysql.metric_example_mysql import MetricExampleMySQL


class MetaMySQLRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ==================== Table 元数据操作 ====================

    async def get_table_by_id(self, table_id: int) -> TableInfoMySQL | None:
        """根据主键 ID 获取表元数据"""
        result = await self.session.execute(
            select(TableInfoMySQL).where(TableInfoMySQL.id == table_id)
        )
        return result.scalars().first()

    async def get_table_by_name(self, table_name: str) -> TableInfoMySQL | None:
        """根据物理表名获取表元数据 (解决当前报错的核心方法)"""
        result = await self.session.execute(
            select(TableInfoMySQL).where(TableInfoMySQL.name == table_name)
        )
        return result.scalars().first()

    async def save_table(self, table: TableInfoMySQL) -> TableInfoMySQL:
        """保存或更新表元数据并持久化生成 ID"""
        self.session.add(table)
        await self.session.flush()
        return table

    # ==================== Column 元数据操作 ====================

    async def get_column(self, table_id: int, column_name: str) -> ColumnInfoMySQL | None:
        """根据表 ID 和物理列名获取字段元数据"""
        result = await self.session.execute(
            select(ColumnInfoMySQL).where(
                ColumnInfoMySQL.table_id == table_id,
                ColumnInfoMySQL.name == column_name
            )
        )
        return result.scalars().first()

    async def save_column(self, column: ColumnInfoMySQL) -> ColumnInfoMySQL:
        """保存或更新字段元数据并生成 ID"""
        self.session.add(column)
        await self.session.flush()
        return column

    async def list_qualified_columns(self) -> list[tuple[str, str]]:
        """按表名、字段名排序返回全部 Meta 字段，用于全量生成草稿。"""
        result = await self.session.execute(
            select(TableInfoMySQL.name, ColumnInfoMySQL.name)
            .join(ColumnInfoMySQL, ColumnInfoMySQL.table_id == TableInfoMySQL.id)
            .order_by(TableInfoMySQL.name, ColumnInfoMySQL.name)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def list_columns_with_tables(self) -> list[tuple[TableInfoMySQL, ColumnInfoMySQL]]:
        """返回全部表和字段 ORM 对象，供索引重建使用。"""
        result = await self.session.execute(
            select(TableInfoMySQL, ColumnInfoMySQL)
            .join(ColumnInfoMySQL, ColumnInfoMySQL.table_id == TableInfoMySQL.id)
            .order_by(TableInfoMySQL.name, ColumnInfoMySQL.name)
        )
        return [(row[0], row[1]) for row in result.all()]

    # ==================== 字段语义草稿操作 ====================

    async def get_pending_semantic_draft(self, column_id: str) -> ColumnSemanticDraftMySQL | None:
        """查询字段当前待审核的草稿；同一字段同时最多只保留一份 pending。"""
        result = await self.session.execute(
            select(ColumnSemanticDraftMySQL).where(
                ColumnSemanticDraftMySQL.column_id == column_id,
                ColumnSemanticDraftMySQL.status == "pending",
            )
        )
        return result.scalars().first()

    async def get_next_semantic_draft_revision(self, column_id: str) -> int:
        """计算字段草稿的下一个版本号。"""
        result = await self.session.execute(
            select(func.max(ColumnSemanticDraftMySQL.revision)).where(
                ColumnSemanticDraftMySQL.column_id == column_id
            )
        )
        current_max = result.scalar()
        return int(current_max or 0) + 1

    async def get_rejected_semantic_draft(
        self, column_id: str, prompt_version: str | None = None
    ) -> ColumnSemanticDraftMySQL | None:
        """查询字段是否已被驳回，用于避免重复提交同一版候选内容。"""
        statement = select(ColumnSemanticDraftMySQL).where(
            ColumnSemanticDraftMySQL.column_id == column_id,
            ColumnSemanticDraftMySQL.status == "rejected",
        )
        if prompt_version:
            statement = statement.where(ColumnSemanticDraftMySQL.prompt_version == prompt_version)
        result = await self.session.execute(
            statement.order_by(ColumnSemanticDraftMySQL.revision.desc())
        )
        return result.scalars().first()

    async def save_semantic_draft(
        self, draft: ColumnSemanticDraftMySQL
    ) -> ColumnSemanticDraftMySQL:
        self.session.add(draft)
        await self.session.flush()
        return draft

    async def get_semantic_draft_by_id(self, draft_id: str) -> ColumnSemanticDraftMySQL | None:
        result = await self.session.execute(
            select(ColumnSemanticDraftMySQL).where(ColumnSemanticDraftMySQL.id == draft_id)
        )
        return result.scalars().first()

    async def list_semantic_drafts(
        self, status: str | None = None
    ) -> list[ColumnSemanticDraftMySQL]:
        statement = select(ColumnSemanticDraftMySQL)
        if status:
            statement = statement.where(ColumnSemanticDraftMySQL.status == status)
        statement = statement.order_by(
            ColumnSemanticDraftMySQL.created_at.desc(),
            ColumnSemanticDraftMySQL.revision.desc(),
        )
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def get_column_by_id(self, column_id: str) -> ColumnInfoMySQL | None:
        """根据字段主键获取元数据。"""
        result = await self.session.execute(
            select(ColumnInfoMySQL).where(ColumnInfoMySQL.id == column_id)
        )
        return result.scalars().first()

    async def list_columns_by_table_id(self, table_id: int) -> list[ColumnInfoMySQL]:
        """返回某张表的全部字段，供候选 Schema 补齐主外键和指标字段。"""
        result = await self.session.execute(
            select(ColumnInfoMySQL)
            .where(ColumnInfoMySQL.table_id == table_id)
            .order_by(ColumnInfoMySQL.name)
        )
        return list(result.scalars().all())

    # ==================== Join 关系元数据操作 ====================

    async def get_table_relation(
        self,
        source_table_id: int,
        source_column_id: str,
        target_table_id: int,
        target_column_id: str,
    ) -> TableRelationMySQL | None:
        result = await self.session.execute(
            select(TableRelationMySQL).where(
                TableRelationMySQL.source_table_id == source_table_id,
                TableRelationMySQL.source_column_id == source_column_id,
                TableRelationMySQL.target_table_id == target_table_id,
                TableRelationMySQL.target_column_id == target_column_id,
            )
        )
        return result.scalars().first()

    async def save_table_relation(self, relation: TableRelationMySQL) -> TableRelationMySQL:
        self.session.add(relation)
        await self.session.flush()
        return relation

    async def list_relations_for_tables(self, table_names: list[str]) -> list[dict]:
        """返回候选表之间已确认的 Join 关系，字段以业务名形式展开。"""
        table_id_to_name: dict[int, str] = {}
        table_ids: list[int] = []
        for table_name in table_names:
            table = await self.get_table_by_name(table_name)
            if table is not None:
                table_ids.append(table.id)
                table_id_to_name[table.id] = table.name

        if len(table_ids) < 2:
            return []

        result = await self.session.execute(
            select(TableRelationMySQL).where(
                TableRelationMySQL.source_table_id.in_(table_ids),
                TableRelationMySQL.target_table_id.in_(table_ids),
            )
        )
        relations: list[dict] = []
        for relation in result.scalars().all():
            source_column = await self.get_column_by_id(relation.source_column_id)
            target_column = await self.get_column_by_id(relation.target_column_id)
            if source_column is None or target_column is None:
                continue
            relations.append(
                {
                    "source_table": table_id_to_name[relation.source_table_id],
                    "source_column": source_column.name,
                    "target_table": table_id_to_name[relation.target_table_id],
                    "target_column": target_column.name,
                    "operator": relation.join_operator or "=",
                }
            )
        return relations

    # ==================== Metric 元数据操作 ====================

    async def get_metric_by_name(self, metric_name: str) -> MetricInfoMySQL | None:
        """根据指标名称获取指标元数据"""
        result = await self.session.execute(
            select(MetricInfoMySQL).where(MetricInfoMySQL.name == metric_name)
        )
        return result.scalars().first()

    async def get_metric_by_key(self, metric_key: str) -> MetricInfoMySQL | None:
        """根据稳定英文标识获取指标，供发布脚本和后台使用。"""
        result = await self.session.execute(
            select(MetricInfoMySQL).where(MetricInfoMySQL.metric_key == metric_key)
        )
        return result.scalars().first()

    async def list_metrics_by_status(self, status: str) -> list[MetricInfoMySQL]:
        """按状态列出指标，例如只取 published 供 Agent 使用。"""
        result = await self.session.execute(
            select(MetricInfoMySQL)
            .where(MetricInfoMySQL.status == status)
            .order_by(MetricInfoMySQL.metric_key)
        )
        return list(result.scalars().all())

    async def list_all_metrics(self) -> list[MetricInfoMySQL]:
        """列出全部指标，供管理页面展示。"""
        result = await self.session.execute(
            select(MetricInfoMySQL).order_by(MetricInfoMySQL.metric_key)
        )
        return list(result.scalars().all())

    async def save_metric(self, metric: MetricInfoMySQL) -> MetricInfoMySQL:
        """保存或更新指标元数据"""
        self.session.add(metric)
        await self.session.flush()
        return metric

    async def get_column_metric(self, column_id: str, metric_id: str) -> ColumnMetricMySQL | None:
        """查询指标与字段的直接计算血缘。"""
        result = await self.session.execute(
            select(ColumnMetricMySQL).where(
                ColumnMetricMySQL.column_id == column_id,
                ColumnMetricMySQL.metric_id == metric_id,
            )
        )
        return result.scalars().first()

    async def save_column_metric(self, column_metric: ColumnMetricMySQL) -> ColumnMetricMySQL:
        self.session.add(column_metric)
        await self.session.flush()
        return column_metric

    async def get_metric_example(
        self,
        metric_id: str,
        example_type: str,
        question: str,
    ) -> MetricExampleMySQL | None:
        """按自然业务键查询示例，确保初始化脚本可重复执行。"""
        result = await self.session.execute(
            select(MetricExampleMySQL).where(
                MetricExampleMySQL.metric_id == metric_id,
                MetricExampleMySQL.example_type == example_type,
                MetricExampleMySQL.question == question,
            )
        )
        return result.scalars().first()

    async def save_metric_example(self, example: MetricExampleMySQL) -> MetricExampleMySQL:
        self.session.add(example)
        await self.session.flush()
        return example
