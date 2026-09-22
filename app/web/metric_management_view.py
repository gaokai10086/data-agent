"""指标管理视图：业务人员提交指标，系统校验后直接发布。"""
import json

import requests
import streamlit as st


def _split_terms(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_json_list(raw: str, field_name: str) -> list:
    text = raw.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} 不是合法 JSON：{exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{field_name} 必须是 JSON 数组")
    return parsed


def _fetch_metrics(api_base: str, status: str) -> list[dict]:
    try:
        response = requests.get(
            f"{api_base}/metric-management/metrics",
            params={"status": status},
            timeout=15,
        )
        if response.status_code != 200:
            st.error(f"指标接口返回异常：{response.status_code} {response.text}")
            return []
        return response.json().get("items", [])
    except requests.RequestException as exc:
        st.error(f"无法连接指标接口，请确认 FastAPI 已启动：{exc}")
        return []


def _submit_metric(api_base: str, payload: dict) -> bool:
    try:
        response = requests.post(f"{api_base}/metric-management/metrics", json=payload, timeout=20)
        if response.status_code == 200:
            return True
        detail = response.json().get("detail", response.text)
        st.error(f"指标提交失败：{detail}")
        return False
    except requests.RequestException as exc:
        st.error(f"指标接口通信异常：{exc}")
        return False


def render_metric_management_page(
    api_base: str = "http://localhost:8000/api",
    embedded: bool = False,
) -> None:
    st.title("📐 指标管理")
    st.caption("业务人员定义指标口径；系统校验事实表与字段后直接发布。")

    st.markdown("---")
    st.subheader("新建指标")
    with st.form("metric_submit_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("指标名称", placeholder="销售额")
            metric_key = st.text_input("英文标识", placeholder="sales_amount")
            fact_table = st.text_input("事实表", placeholder="fact_order")
            measure_column = st.text_input("度量字段", placeholder="pay_amount")
        with col2:
            aggregation = st.selectbox(
                "聚合方式",
                ["SUM", "COUNT", "COUNT_DISTINCT", "AVG", "MIN", "MAX"],
            )
            time_column = st.text_input("默认时间字段（可空）", placeholder="pay_time")
            alias = st.text_input("别名（英文逗号分隔）", placeholder="销售金额,成交金额")

        description = st.text_area("业务定义", placeholder="订单状态为已完成的订单实际支付金额之和")
        formula = st.text_input("计算公式", placeholder="SUM(fact_order.pay_amount)")
        filters = st.text_area(
            "固定过滤条件（JSON 数组）",
            value='[{"column": "fact_order.order_status", "operator": "=", "value": "已完成"}]',
            height=90,
        )
        dimensions = st.text_area(
            "可分析维度（JSON 数组）",
            value='[{"column": "dim_store.region", "name": "大区"}]',
            height=90,
        )
        submitted = st.form_submit_button("提交并发布", use_container_width=True)
        if not submitted:
            return

        if not all([name.strip(), metric_key.strip(), fact_table.strip(), measure_column.strip(), description.strip(), formula.strip()]):
            st.error("指标名称、英文标识、事实表、度量字段、业务定义和计算公式均为必填。")
            return

        try:
            payload = {
                "metric_key": metric_key.strip(),
                "name": name.strip(),
                "description": description.strip(),
                "formula": formula.strip(),
                "fact_table": fact_table.strip(),
                "measure_column": measure_column.strip(),
                "aggregation": aggregation,
                "time_column": time_column.strip() or None,
                "filters": _parse_json_list(filters, "固定过滤条件"),
                "alias": _split_terms(alias),
                "dimensions": _parse_json_list(dimensions, "可分析维度"),
            }
        except ValueError as exc:
            st.error(str(exc))
            return

        if _submit_metric(api_base, payload):
            st.success("指标已发布，列表即将刷新。")
            st.rerun()

    st.markdown("---")
    status_labels = {
        "published": "已发布",
        "draft": "草稿",
        "deprecated": "已下线",
        "all": "全部",
    }
    if embedded:
        filter_col, refresh_col = st.columns([3, 1])
        status = filter_col.radio(
            "查看范围",
            list(status_labels.keys()),
            format_func=status_labels.get,
            horizontal=True,
            key="embedded_metric_status",
        )
        refresh_col.write("")
        if refresh_col.button("刷新指标", use_container_width=True):
            st.rerun()
    else:
        with st.sidebar:
            status = st.radio(
                "查看范围",
                list(status_labels.keys()),
                format_func=status_labels.get,
            )
            if st.button("刷新指标", use_container_width=True):
                st.rerun()

    metrics = _fetch_metrics(api_base, status)
    st.subheader(f"共 {len(metrics)} 个指标")
    if not metrics:
        st.info("当前没有对应状态的指标。")
    for metric in metrics:
        with st.expander(f"{metric['name']}　`{metric['metric_key']}`　「{metric['status']}」"):
            st.markdown(f"**业务定义**：{metric.get('description')}")
            st.markdown(f"**计算公式**：`{metric.get('formula')}`")
            st.markdown(
                f"**事实表 / 度量字段 / 聚合**：`{metric.get('fact_table')}.{metric.get('measure_column')}` "
                f"`{metric.get('aggregation')}`"
            )
            st.markdown(f"**默认时间字段**：`{metric.get('time_column') or '无'}`")
            st.markdown(f"**别名**：{metric.get('alias')}")
            st.markdown(f"**固定过滤**：{metric.get('filters')}")
            st.markdown(f"**可分析维度**：{metric.get('dimensions')}")
