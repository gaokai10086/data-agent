"""发布/迁移时验证维度唯一归属；运行时不重复扫描数据。"""
from app.core.meta.dimension_dependencies import validate_dimension_dependencies
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository


async def validate_dependency_data(dimensions: list[dict], dw: DWMySQLRepository | None) -> None:
    validate_dimension_dependencies(dimensions)
    for dimension in dimensions:
        for target in dimension.get("determines", []):
            if dw is None:
                raise ValueError("发布维度依赖需要 DW 数据校验")
            if not await dw.check_dimension_dependency(dimension["column"], target):
                raise ValueError(f"维度依赖未通过数据校验：{dimension['column']} 不能唯一确定 {target}（多重归属、缺失或无有效数据）")
