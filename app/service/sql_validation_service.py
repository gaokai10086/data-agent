"""SQL 安全与 Schema 字段解析的共享校验能力。"""
import re

from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository

FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|grant|revoke|load|call|lock|unlock)\b",
    flags=re.IGNORECASE,
)

# 只把「表名.字段名」形式的引用纳入候选 Schema 校验，
# 避免把 CTE 或子查询产生的派生别名（如 order_count）误判成物理字段。
QUALIFIED_COLUMN_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")


class SQLSafetyPolicy:
    """只读 SQL 安全规则，供所有需要录入或校验 SQL 的入口复用。"""

    @staticmethod
    def ensure_read_only_sql(sql: str) -> None:
        cleaned = sql.strip().rstrip(";").strip()
        if not cleaned:
            raise ValueError("SQL 不能为空")
        if ";" in cleaned:
            raise ValueError("只允许一条查询语句")
        if FORBIDDEN_SQL_PATTERN.search(cleaned):
            raise ValueError("只允许查询，禁止 INSERT、UPDATE、DELETE、DDL 等写操作")
        if not (cleaned.upper().startswith("SELECT") or cleaned.upper().startswith("WITH")):
            raise ValueError("SQL 必须以 SELECT 或 WITH 开头")


class SQLValidationService:
    """统一提供表字段解析与 SQL 执行计划校验。"""

    def __init__(
        self,
        meta_repository: MetaMySQLRepository | None = None,
        dw_repository: DWMySQLRepository | None = None,
    ):
        self.meta_repository = meta_repository
        self.dw_repository = dw_repository

    async def resolve_column(self, qualified_name: str, fact_table: str) -> ColumnInfoMySQL:
        """把「表名.字段名」或事实表内字段名解析为真实的 ColumnInfo。"""
        if self.meta_repository is None:
            raise RuntimeError("缺少 Meta 仓储，无法解析字段")
        if "." in qualified_name:
            table_name, column_name = qualified_name.split(".", 1)
        else:
            table_name, column_name = fact_table, qualified_name

        table = await self.meta_repository.get_table_by_name(table_name.strip())
        if table is None:
            raise ValueError(f"引用的表 [{table_name}] 不存在")
        column = await self.meta_repository.get_column(table.id, column_name.strip())
        if column is None:
            raise ValueError(f"引用的字段 [{qualified_name}] 不存在")
        return column

    async def validate_read_only_sql(self, sql: str) -> None:
        """先做只读安全判断，再用 EXPLAIN 校验 SQL 能在 DW 上生成执行计划。"""
        SQLSafetyPolicy.ensure_read_only_sql(sql)
        if self.dw_repository is None:
            raise RuntimeError("缺少 DW 仓储，无法执行 SQL 校验")
        await self.dw_repository.validate_sql(sql)

    async def validate_references(
        self,
        sql: str,
        table_infos: list[dict],
        table_relations: list[dict],
        retrieved_values: list[dict],
        query: str,
    ) -> list[str]:
        """检查 SQL 是否只使用候选 Schema，并保留已召回的值与 Join 约束。"""
        if self.dw_repository is None:
            raise RuntimeError("缺少 DW 仓储，无法提取 SQL 使用字段")

        used_columns = set(await self.dw_repository.get_used_columns_from_sql(sql))
        allowed_columns = {
            str(column.get("name"))
            for table in table_infos
            for column in table.get("columns", [])
            if column.get("name")
        }
        violations: list[str] = []

        qualified_columns = {
            match.group(2) for match in QUALIFIED_COLUMN_PATTERN.finditer(sql)
        }
        illegal_columns = sorted(
            column for column in (used_columns - allowed_columns) if column in qualified_columns
        )
        if illegal_columns:
            violations.append(f"引用了不在候选 Schema 中的字段: {', '.join(illegal_columns)}")

        query_text = (query or "").strip()
        for value in retrieved_values or []:
            column_name = str(value.get("column_name") or "")
            table_name = str(value.get("table_name") or "")
            value_text = str(value.get("value") or "")
            if (
                value_text
                and value_text in query_text
                and column_name
                and column_name not in used_columns
            ):
                violations.append(
                    f"问题涉及枚举值 [{value.get('value')}]，但 SQL 未使用字段 [{table_name}.{column_name}]"
                )

        table_names = {str(table.get("name")) for table in table_infos if table.get("name")}
        lowered_sql = sql.lower()
        for relation in table_relations or []:
            source_table = str(relation.get("source_table") or "")
            target_table = str(relation.get("target_table") or "")
            if source_table not in table_names or target_table not in table_names:
                continue
            if source_table.lower() not in lowered_sql or target_table.lower() not in lowered_sql:
                continue
            source_column = str(relation.get("source_column") or "")
            target_column = str(relation.get("target_column") or "")
            if source_column not in used_columns or target_column not in used_columns:
                violations.append(
                    f"SQL 使用多表但缺少确认的 Join 字段 [{source_table}.{source_column} = {target_table}.{target_column}]"
                )

        return list(dict.fromkeys(violations))
