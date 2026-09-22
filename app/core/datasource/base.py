"""
================================================================================
模块名称：数据源统一抽象基类 (借鉴 DB-GPT Datasource SPI)
模块路径：app/core/datasource/base.py
核心职责：
    1. 定义物理表与字段画像的统一数据契约 (Pydantic Schema)
    2. 约束所有底层数据库连接器必须实现的标准接口
    3. 支撑未来无缝扩展 PostgreSQL, ClickHouse, Doris 等多源数仓
================================================================================
"""
from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field


class ColumnProfile(BaseModel):
    """字段元数据画像实体"""
    name: str = Field(description="物理列名")
    data_type: str = Field(description="物理数据类型，如 VARCHAR, INT, DECIMAL")
    is_nullable: bool = Field(default=True, description="是否允许为空")
    is_primary_key: bool = Field(default=False, description="是否为主键")
    comment: str = Field(default="", description="列注释业务含义")
    sample_values: list[Any] = Field(default_factory=list, description="该字段真实物理值采样(用于写入ES)")


class TableProfile(BaseModel):
    """数据表元数据画像实体"""
    table_name: str = Field(description="物理表名")
    comment: str = Field(default="", description="表级注释业务描述")
    columns: list[ColumnProfile] = Field(default_factory=list, description="表内所有字段画像列表")


class ForeignKeyProfile(BaseModel):
    """字段级外键关系；用于生成受约束的 Join 路径。"""
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    constraint_name: str = ""


class BaseDatasourceConnector(ABC):
    """
    通用数据源探查连接器基类 (面向接口编程，彻底解耦业务逻辑与具体数据库)
    """

    @abstractmethod
    async def get_all_table_names(self) -> list[str]:
        """扫描并返回数仓中所有可见的物理表名"""
        pass

    @abstractmethod
    async def get_table_profile(self, table_name: str) -> TableProfile:
        """
        通过字典表反射获取单表的完整元数据画像 (包含表注释、列类型、主键约束等)
        """
        pass

    @abstractmethod
    async def sample_column_values(self, table_name: str, column_name: str, limit: int = 15) -> list[Any]:
        """
        自动执行采样查询，嗅探代表性非空枚举值 (供 ES 倒排索引召回使用)
        """
        pass

    @abstractmethod
    async def probe_distinct_value_count(
        self, table_name: str, column_name: str, cap: int = 31
    ) -> int:
        """
        探测字段去重取值数量，最多探测到 cap 为止。

        返回值为 min(真实去重数量, cap)，用于判断字段基数是否适合作为枚举样例；
        调用方据此决定是否继续采样，避免对大表执行代价高昂的全量 COUNT(DISTINCT)。
        """
        pass

    @abstractmethod
    async def get_frequent_column_values(
        self, table_name: str, column_name: str, limit: int = 12
    ) -> list[Any]:
        """
        按出现频次从高到低返回字段的非空去重值，作为值召回样例。
        """
        pass

    @abstractmethod
    async def get_create_table_ddl(self, table_name: str) -> str:
        """获取真实的 SHOW CREATE TABLE 原始 DDL 语句"""
        pass

    @abstractmethod
    async def get_foreign_keys(self) -> list[ForeignKeyProfile]:
        """扫描当前数据源的真实外键关系。"""
        pass

    @abstractmethod
    async def close(self):
        """关闭并销毁底层连接池"""
        pass
