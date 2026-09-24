"""从 Meta 已发布字段重建 Elasticsearch 枚举值索引。"""
from dataclasses import dataclass, field

from app.core.meta.value_sampling_policy import is_safe_to_index
from app.models.es.value_info_es import ValueInfoES
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.repositories.es.value_es_repository import ValueESRepository


@dataclass
class ValueIndexRebuildReport:
    indexed_values: int = 0
    skipped_columns: list[str] = field(default_factory=list)


class ValueIndexRebuildService:
    """重建 ES 枚举值索引，并继续遵守字段采样安全策略。"""

    def __init__(
        self,
        meta_repository: MetaMySQLRepository,
        es_client,
        index_name: str = "data_agent",
    ):
        self.meta_repository = meta_repository
        self.es_client = es_client
        self.index_name = index_name

    async def rebuild(self) -> ValueIndexRebuildReport:
        table_columns = await self.meta_repository.list_columns_with_tables()
        documents: list[ValueInfoES] = []
        report = ValueIndexRebuildReport()

        for table, column in table_columns:
            qualified_name = f"{table.name}.{column.name}"
            if (column.value_type or "open") != "closed":
                report.skipped_columns.append(f"{qualified_name}: 非封闭枚举字段，不建立值索引")
                continue

            safety = is_safe_to_index(table.name, column.name or "", column.type or "")
            if not safety.allowed:
                report.skipped_columns.append(f"{qualified_name}: {safety.reason}")
                continue

            examples = column.examples or []
            if isinstance(examples, str):
                examples = [examples]
            if not examples:
                report.skipped_columns.append(f"{qualified_name}: 已标记封闭枚举但未维护枚举值")
                continue
            for raw_value in examples:
                value = str(raw_value).strip()
                if not value:
                    continue
                documents.append(
                    ValueInfoES(
                        id=f"{column.id}:{value}",
                        value=value,
                        type="enum",
                        column_id=column.id,
                        column_name=column.name or "",
                        table_id=str(table.id),
                        table_name=table.name,
                    )
                )

        await self.es_client.indices.delete(index=self.index_name, ignore=[400, 404])
        repository = ValueESRepository(self.es_client)
        await repository.ensure_index()
        if documents:
            await repository.batch_index(documents)
        report.indexed_values = len(documents)
        return report
