"""将 DW 的可验证物理 Schema 幂等同步到 Meta 数据库。"""
from dataclasses import dataclass

from app.core.datasource.base import BaseDatasourceConnector, ForeignKeyProfile
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.mysql.table_relation_mysql import TableRelationMySQL
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


@dataclass
class SchemaSyncReport:
    tables_created: int = 0
    tables_updated: int = 0
    columns_created: int = 0
    columns_updated: int = 0
    relations_created: int = 0
    relations_existing: int = 0


class SchemaSyncService:
    """只同步物理事实，不覆盖人工维护的业务语义。"""

    def __init__(self, connector: BaseDatasourceConnector, meta_repository: MetaMySQLRepository):
        self.connector = connector
        self.meta_repository = meta_repository

    @staticmethod
    def _table_role(table_name: str) -> str:
        if table_name.startswith("fact_"):
            return "fact"
        if table_name.startswith("dim_"):
            return "dimension"
        return "unknown"

    @staticmethod
    def _column_role(
        table_name: str,
        column_name: str,
        is_primary_key: bool,
        foreign_key_columns: set[tuple[str, str]],
    ) -> str:
        if is_primary_key:
            return "primary_key"
        if (table_name, column_name) in foreign_key_columns:
            return "foreign_key"
        if any(name in column_name for name in ("amount", "price", "cost", "profit", "quantity", "count", "rate")):
            return "measure"
        return "dimension"

    async def sync(self) -> SchemaSyncReport:
        report = SchemaSyncReport()
        table_names = await self.connector.get_all_table_names()
        foreign_key_profiles = await self.connector.get_foreign_keys()
        foreign_key_columns = {(fk.source_table, fk.source_column) for fk in foreign_key_profiles}
        tables: dict[str, TableInfoMySQL] = {}
        columns: dict[tuple[str, str], ColumnInfoMySQL] = {}

        for table_name in table_names:
            profile = await self.connector.get_table_profile(table_name)
            table_record = await self.meta_repository.get_table_by_name(table_name)
            if table_record is None:
                table_record = TableInfoMySQL(
                    name=table_name,
                    role=self._table_role(table_name),
                    description=profile.comment or table_name,
                )
                await self.meta_repository.save_table(table_record)
                report.tables_created += 1
            else:
                # 表名和表级 DDL 注释属于物理事实；只在描述尚为空时补齐，避免覆盖人工语义。
                if not table_record.description and profile.comment:
                    table_record.description = profile.comment
                    await self.meta_repository.save_table(table_record)
                    report.tables_updated += 1
            tables[table_name] = table_record

            for column_profile in profile.columns:
                column_record = await self.meta_repository.get_column(table_record.id, column_profile.name)
                derived_role = self._column_role(
                    table_name,
                    column_profile.name,
                    column_profile.is_primary_key,
                    foreign_key_columns,
                )
                if column_record is None:
                    column_record = ColumnInfoMySQL(
                        name=column_profile.name,
                        type=column_profile.data_type,
                        role=derived_role,
                        examples=[],
                        description=column_profile.comment or column_profile.name,
                        alias=[],
                        table_id=table_record.id,
                    )
                    await self.meta_repository.save_column(column_record)
                    report.columns_created += 1
                else:
                    changed = False
                    if column_record.type != column_profile.data_type:
                        column_record.type = column_profile.data_type
                        changed = True
                    if column_profile.is_primary_key and column_record.role != "primary_key":
                        column_record.role = "primary_key"
                        changed = True
                    elif (table_name, column_profile.name) in foreign_key_columns and column_record.role != "primary_key" and column_record.role != "foreign_key":
                        column_record.role = "foreign_key"
                        changed = True
                    if not column_record.description and column_profile.comment:
                        column_record.description = column_profile.comment
                        changed = True
                    if changed:
                        await self.meta_repository.save_column(column_record)
                        report.columns_updated += 1
                columns[(table_name, column_profile.name)] = column_record

        for fk in foreign_key_profiles:
            source_table = tables.get(fk.source_table)
            target_table = tables.get(fk.target_table)
            source_column = columns.get((fk.source_table, fk.source_column))
            target_column = columns.get((fk.target_table, fk.target_column))
            if not all((source_table, target_table, source_column, target_column)):
                raise RuntimeError(
                    f"外键同步失败：无法定位 {fk.source_table}.{fk.source_column} → "
                    f"{fk.target_table}.{fk.target_column}"
                )
            relation = await self.meta_repository.get_table_relation(
                source_table.id, source_column.id, target_table.id, target_column.id
            )
            if relation is None:
                await self.meta_repository.save_table_relation(TableRelationMySQL(
                    source_table_id=source_table.id,
                    source_column_id=source_column.id,
                    target_table_id=target_table.id,
                    target_column_id=target_column.id,
                    relation_type="foreign_key",
                    join_operator="=",
                ))
                report.relations_created += 1
            else:
                report.relations_existing += 1

        return report
