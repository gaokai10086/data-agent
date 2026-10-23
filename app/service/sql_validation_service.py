"""SQL 安全与 Schema 字段解析的共享校验能力。"""
import re

from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.core.meta.display_intent import DISPLAY_INTENT_RULES
from app.core.sql_safety import SQLSafetyPolicy, FORBIDDEN_SQL_PATTERN
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository

# 只把「表名.字段名」形式的引用纳入候选 Schema 校验，
# 避免把 CTE 或子查询产生的派生别名（如 order_count）误判成物理字段。
QUALIFIED_COLUMN_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")

# FROM / JOIN 后面可能被误识别为别名的关键字
RESERVED_ALIAS_WORDS = frozenset({
    "on", "where", "group", "order", "having", "limit", "as", "using",
    "left", "right", "inner", "outer", "full", "cross", "join", "and", "or",
    "natural", "straight_join", "union", "intersect", "except", "offset", "for",
    "window", "into",
})

# 必须在捕获别名前排除关键字，避免消耗 JOIN 后漏掉下一张表。
# 词边界确保 join_store、ordering 等合法别名不被关键字前缀误伤。
_RESERVED_ALIAS_PATTERN = "|".join(re.escape(word) for word in sorted(RESERVED_ALIAS_WORDS))
TABLE_ALIAS_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+(?:`?[A-Za-z_][A-Za-z0-9_]*`?\.)?`?([A-Za-z_][A-Za-z0-9_]*)`?"
    rf"(?:\s+(?:AS\s+)?(?!(?:{_RESERVED_ALIAS_PATTERN})\b)`?([A-Za-z_][A-Za-z0-9_]*)`?)?",
    flags=re.IGNORECASE,
)

class SQLValidationService:
    """统一提供表字段解析与 SQL 执行计划校验。"""

    def __init__(
        self,
        meta_repository: MetaMySQLRepository | None = None,
        dw_repository: DWMySQLRepository | None = None,
    ):
        self.meta_repository = meta_repository
        self.dw_repository = dw_repository

    @staticmethod
    def validate_city_filter(sql: str, query: str, used_columns: set[str]) -> list[str]:
        """保护标准查询中的城市筛选，防止把城市解释成大区。"""
        city_values = re.findall(r"城市\s*=\s*([^、，；;]+)", query or "")
        if not city_values:
            return []
        violations = []
        if "city" not in used_columns:
            violations.append("查询要求城市筛选，但 SQL 未使用城市字段 city；不能用大区 region 替代")
        for value in city_values:
            value = value.strip()
            if re.search(r"`?region`?\s*=\s*['\"]" + re.escape(value) + r"['\"]", sql, re.IGNORECASE):
                violations.append(f"城市 [{value}] 被错误用于大区 region 筛选，应按 Schema 使用 city 字段")
        return violations

    @staticmethod
    def validate_grounded_city_values(sql: str, values: list[dict]) -> list[str]:
        """校验常见单表/Join 聚合查询中的城市条件，保护字段对象及数据库原值。"""
        cleaned = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.DOTALL)
        where = re.search(r"\bWHERE\b(.*)", cleaned, re.IGNORECASE | re.DOTALL)
        predicates = where.group(1) if where else ""
        referenced = {}
        for match in TABLE_ALIAS_PATTERN.finditer(cleaned):
            table, alias = match.groups()
            referenced.setdefault(table.lower(), set()).add(table)
            if alias and alias.lower() not in RESERVED_ALIAS_WORDS:
                referenced[table.lower()].add(alias)
        violations = []
        for value in values:
            if value.get("column_name") != "city":
                continue
            table = str(value.get("table_name") or "")
            canonical = str(value.get("value") or "")
            aliases = referenced.get(table.lower(), set())
            qualifier = "|".join(re.escape(alias) for alias in aliases)
            column_pattern = rf"(?:`?(?:{qualifier})`?\.)`?city`?" if qualifier else r"(?!)"
            # 仅一张有 city 字段的表被引用时，允许不带表别名的写法。
            if table.lower() in referenced and not ({"dim_store", "dim_user"} - {table.lower()}) & referenced.keys():
                column_pattern = rf"(?:{column_pattern}|(?<![.\w`])`?city`?)"
            literal = re.escape(canonical.replace("'", "''"))
            expected = rf"{column_pattern}\s*(?:=\s*['\"]{literal}['\"]|IN\s*\(\s*['\"]{literal}['\"]\s*\))"
            if not re.search(expected, predicates, re.IGNORECASE):
                violations.append(
                    f"城市取值校验失败：用户表达 [{value.get('matched_text') or canonical}] "
                    f"应筛选 {table}.city = '{canonical}'；不能替换为门店名、大区、其他对象的城市或未登记别名"
                )
        return violations

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

        # 只校验「物理表别名.字段名」形式，避免把 CTE 或派生表的别名误判成非法字段。
        physical_tables = await self.dw_repository.get_all_table_names()
        table_aliases = self._collect_physical_table_aliases(sql, physical_tables)
        illegal_columns = sorted({
            column
            for alias, column in QUALIFIED_COLUMN_PATTERN.findall(sql)
            if alias.lower() in table_aliases and column not in allowed_columns
        })
        if illegal_columns:
            violations.append(f"引用了不在候选 Schema 中的字段: {', '.join(illegal_columns)}")

        query_text = (query or "").strip()
        violations.extend(self.validate_city_filter(sql, query_text, used_columns))
        violations.extend(self.validate_grounded_city_values(sql, retrieved_values or []))

        for keywords, column_patterns in DISPLAY_INTENT_RULES:
            if not any(keyword in query_text for keyword in keywords):
                continue
            matched = any(
                column.endswith(pattern) or column == pattern
                for column in used_columns
                for pattern in column_patterns
            )
            if not matched:
                violations.append(
                    f"问题要求返回「{'/'.join(keywords)}」，"
                    f"但 SQL 未使用对应的可读名称字段（如 {'/'.join(column_patterns)}），"
                    f"禁止只用编码字段代替"
                )

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

    @staticmethod
    def _collect_physical_table_aliases(sql: str, physical_tables: list[str]) -> set[str]:
        """收集 SQL 中真实物理表的别名（含表名自身），用于区分 CTE 别名。"""
        physical_lower = {name.lower() for name in physical_tables}
        aliases: set[str] = set()
        for match in TABLE_ALIAS_PATTERN.finditer(sql):
            table_name, alias = match.group(1), match.group(2)
            if table_name.lower() not in physical_lower:
                continue
            aliases.add(table_name.lower())
            if alias and alias.lower() not in RESERVED_ALIAS_WORDS:
                aliases.add(alias.lower())
        return aliases
