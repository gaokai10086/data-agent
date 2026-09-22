import json
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DWMySQLRepository:
    def __init__(self, dw_session: AsyncSession):
        self.dw_session = dw_session

    #   让大模型在写 SQL 前知道哪些是文本、哪些是数值，防止模型对字符串字段直接使用 SUM() 导致报错
    async def get_column_types(self, table_name: str) -> dict[str, str]:
        """
        获取表中各字段类型
        :param table_name:表名称
        :return: 各字段类型字典
        """
        sql = text(f"show columns from {table_name}")
        result = await self.dw_session.execute(sql)
        return {row.Field: row.Type for row in result.fetchall()}

    #  在初始化或者离线构建索引时，把高频枚举值（如品类：“美妆”、“数码”、“母婴”）抽样出来存入 ES，支持后续精准值召回。
    async def get_column_values(self, table_name: str, column_name: str, limit: int) -> list[Any]:
        sql = text(f"""
            select
                {column_name} as column_value
            from {table_name}
            group by {column_name}
            limit {limit}
        """)
        result = await self.dw_session.execute(sql)
        return [row.column_value for row in result.fetchall()]

    #  （解决方言兼容性）
    async def get_db_info(self) -> dict[str, str]:
        """
        获取数据仓库信息
        :return: 数据仓库信息
        """
        dialect = self.dw_session.get_bind().dialect.name

        sql = text("select version() as version")
        result = await self.dw_session.execute(sql)
        version = result.scalar()
        return {"dialect": dialect, "version": version}

    #  （解决时间漂移）
    async def get_date_info(self) -> dict[str, str]:
        """
        获取日期信息
        :return: 日期信息
        """
        sql = text("select now() as now")
        result = await self.dw_session.execute(sql)
        now = result.scalar()

        # 修复: Python 不支持 %Q，使用数学逻辑计算季度 (1~4)
        quarter = f"Q{(now.month - 1) // 3 + 1}"

        return {
            "date": now.strftime("%Y-%m-%d"),
            "weekday": now.strftime("%A"),
            "quarter": quarter
        }

    async def validate_sql(self, query):
        await self.dw_session.execute(text(f"explain {query}"))

    async def get_used_columns_from_sql(self, query: str) -> list[str]:
        """从 EXPLAIN FORMAT=JSON 的执行计划中提取 SQL 实际使用的字段。"""
        result = await self.dw_session.execute(text(f"EXPLAIN FORMAT=JSON {query}"))
        raw = result.scalar()
        if not raw:
            return []
        plan = json.loads(raw)
        columns: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                used_columns = node.get("used_columns")
                if isinstance(used_columns, list):
                    for column in used_columns:
                        columns.add(str(column))
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(plan)
        return sorted(columns)

    async def extract_tables_from_sql(self, query: str) -> list[str]:
        """从 SQL 原文中识别 DW 真实物理表，避免把表别名误当成物理表。"""
        table_names = await self.get_all_table_names()
        if not table_names:
            return []
        pattern = re.compile(
            r"\b(" + "|".join(re.escape(name) for name in table_names) + r")\b",
            flags=re.IGNORECASE,
        )
        seen: set[str] = set()
        tables: list[str] = []
        for match in pattern.findall(query):
            normalized = match.lower()
            if normalized not in seen:
                seen.add(normalized)
                tables.append(match)
        return tables

    async def get_all_table_names(self) -> list[str]:
        """读取当前 DW 数据库下的全部物理基础表。"""
        result = await self.dw_session.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """))
        return [row[0] for row in result.fetchall()]

    async def execute_sql(self, sql):
        result = await self.dw_session.execute(text(sql))
        return [dict(row) for row in result.mappings().fetchall()]
