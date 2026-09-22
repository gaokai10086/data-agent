"""执行结果规范化与比对。

第一版按「行内取值集合 + 行集合」比较，忽略 SQL 别名差异。
这样可以避免同一条正确 SQL 因为列别名不同而被判错。
"""
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

NUMERIC_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")


def _normalize_number(value: Any, numeric_scale: int) -> str:
    quantum = Decimal(1).scaleb(-numeric_scale)
    quantized = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    # 去掉多余的尾随零，让 5621.00、5621、'5621' 三种写法等价
    return format(quantized.normalize(), "f")


def _normalize_value(value: Any, numeric_scale: int) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return _normalize_number(value, numeric_scale)
    text = str(value).strip()
    if NUMERIC_PATTERN.match(text):
        return _normalize_number(text, numeric_scale)
    return text


def canonicalize_rows(rows: list, numeric_scale: int = 2) -> list[tuple]:
    """把结果集转换成可比较的规范形式：行内值排序、行间排序。"""
    if not rows:
        return []
    canonical: list[tuple] = []
    for row in rows:
        values = row.values() if isinstance(row, dict) else row
        normalized = [_normalize_value(value, numeric_scale) for value in values]
        canonical.append(tuple(sorted(normalized, key=lambda item: (item is None, str(item)))))
    return sorted(canonical, key=lambda item: [str(part) for part in item])


def results_match(actual: list, expected: list, numeric_scale: int = 2) -> bool:
    return canonicalize_rows(actual, numeric_scale) == canonicalize_rows(expected, numeric_scale)


def jsonify_rows(rows: list[dict]) -> list[dict]:
    """把结果集中的 Decimal、datetime 等类型转成可 JSON 序列化的字符串。"""
    return [
        {key: (None if value is None else str(value)) for key, value in row.items()}
        for row in rows
    ]
