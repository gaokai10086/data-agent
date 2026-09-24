"""展示字段意图规则。

问题中出现「名称」「姓名」「品牌」等词时，SQL 必须返回对应的可读名称字段，
不能只用编码字段（如 product_id）代替。

该规则同时被两处使用，保证「压缩阶段不丢字段」和「校验阶段要求字段」一致：
    1. filter_table_info 压缩候选表结构时，保护这些字段不被裁掉
    2. SQLValidationService 校验引用时，要求这些字段出现在 SQL 中
"""

DISPLAY_INTENT_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("名称", "姓名"), ("_name",)),
    (("品牌",), ("brand",)),
)


def required_display_patterns(query: str) -> tuple[str, ...]:
    """返回当前问题要求的可读字段名模式；没有问题要求时返回空元组。"""
    text = query or ""
    patterns: list[str] = []
    for keywords, column_patterns in DISPLAY_INTENT_RULES:
        if any(keyword in text for keyword in keywords):
            patterns.extend(column_patterns)
    return tuple(dict.fromkeys(patterns))


def matches_display_pattern(column_name: str, patterns: tuple[str, ...]) -> bool:
    """判断字段名是否满足展示意图，例如 product_name 匹配 _name。"""
    if not column_name:
        return False
    return any(column_name.endswith(pattern) or column_name == pattern for pattern in patterns)
