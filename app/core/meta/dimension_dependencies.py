"""指标维度的唯一确定关系；不根据地理名称猜测层级。"""
import re


def validate_dimension_dependencies(dimensions: list[dict]) -> None:
    columns = [item["column"] for item in dimensions]
    if len(set(columns)) != len(columns):
        raise ValueError("指标维度字段不能重复")
    graph = {item["column"]: item.get("determines", []) for item in dimensions}
    for source, targets in graph.items():
        if targets and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", source):
            raise ValueError("维度依赖必须使用完整的 表名.字段名")
        for target in targets:
            if target not in graph:
                raise ValueError(f"依赖目标不属于当前指标维度：{target}")
            if target == source:
                raise ValueError(f"维度不能确定自身：{source}")
    visiting, visited = set(), set()

    def visit(column):
        if column in visiting:
            raise ValueError("维度依赖不能形成循环")
        if column in visited:
            return
        visiting.add(column)
        for target in graph[column]:
            visit(target)
        visiting.remove(column)
        visited.add(column)

    for column in graph:
        visit(column)
