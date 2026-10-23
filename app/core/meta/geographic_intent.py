"""查询中的显式城市语义，用于保护候选 Schema；不负责地名值映射。"""
import re


def requires_city_fields(query: str) -> bool:
    return bool(
        "城市" in query
        or re.search(r"[\u4e00-\u9fff]{2,}市", query)
        or any(city in query for city in ("北京", "上海", "天津", "重庆"))
    )
