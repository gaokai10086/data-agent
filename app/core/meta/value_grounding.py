"""将用户表达关联到已登记的字段取值，不根据字符相似度猜测业务值。"""
from __future__ import annotations

import re


def city_value_aliases(value: str) -> list[str]:
    """只为已登记城市生成常见的“市”后缀别名，数据库原值始终保留。"""
    aliases = [value]
    if re.fullmatch(r"[\u4e00-\u9fff]{2,}", value):
        if value.endswith("市"):
            aliases.append(value[:-1])
        elif not value.endswith(("区", "县", "州", "盟", "地区")):
            aliases.append(value + "市")
    return list(dict.fromkeys(aliases))


def match_value_expression(query: str, item: dict) -> str | None:
    """全文检索只给候选；必须能在问题中找到完整值或已登记别名才接受。"""
    query = query.strip()
    value = str(item.get("value") or "").strip()
    if not query or not value:
        return None
    expressions = list(dict.fromkeys([value, *(item.get("aliases") or [])]))
    for expression in sorted(expressions, key=len, reverse=True):
        if not expression:
            continue
        if query.casefold() == expression.casefold():
            return expression
        if item.get("column_name") == "city":
            # 不把“北京王府井旗舰店”中的“北京”当作城市筛选。
            suffix = r"(?=的|地区|销售|订单|门店|用户|客户|买家|会员|渠道|[0-9年月\s=，,；;]|$)"
            if re.search(re.escape(expression) + suffix, query, re.IGNORECASE):
                return expression
        elif re.search(re.escape(expression), query, re.IGNORECASE):
            return expression
    return None


def select_value_evidence(query: str, values: list[dict], metrics: list[dict]) -> list[dict]:
    """消除重复证据；同名城市以显式对象优先，其次使用指标声明的城市维度。"""
    unique = {str(v.get("id") or (v.get("table_name"), v.get("column_name"), v.get("value"))): v
              for v in values if match_value_expression(query, v)}
    city_values = [v for v in unique.values() if v.get("column_name") == "city"]
    preferred = set()
    if re.search(r"(?:用户|买家|会员|客户)(?:所在|居住|所在地|城市)", query):
        preferred = {"dim_user.city"}
    elif any(word in query for word in ("门店", "店铺")):
        preferred = {"dim_store.city"}
    else:
        preferred = {str(d.get("column")) for m in metrics for d in (m.get("dimensions") or [])
                     if isinstance(d, dict) and str(d.get("column", "")).endswith(".city")}
    chosen = [v for v in city_values if f"{v.get('table_name')}.{v.get('column_name')}" in preferred]
    if chosen:
        city_values = chosen
    return [v for v in unique.values() if v.get("column_name") != "city"] + city_values


def prompt_value_evidence(values: list[dict]) -> list[dict]:
    return [{"column": f"{v.get('table_name')}.{v.get('column_name')}",
             "user_expression": v.get("matched_text") or v.get("value"),
             "database_value": v.get("value")} for v in values]
