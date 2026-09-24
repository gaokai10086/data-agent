"""
================================================================================
模块名称：Data Agent 可视化 Web 对话平台 🐋📊
模块路径：web_ui.py
核心职责：
    1. 实时流式打字机对话渲染（支持 SSE 协议与 Thought 思考轨迹）⚡
    2. 结构化渲染：数据来源（SQL 审计）、纯 HTML <table> 绝对居中高质感报表 📊
    3. 数值智能格式化：消除长尾零，添加标准千分位支持 💰
    4. 会话管理：重置会话、本地归档、Markdown 导出 📝
    5. 知识库运营：侧边栏 Few-Shot 金标 SQL 动态录入与向量化入库 📚💾
================================================================================
"""

import uuid
import json
import requests
import streamlit as st
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import altair as alt
import pandas as pd

from app.web.meta_governance_view import render_meta_governance_page
from app.web.metric_management_view import render_metric_management_page
from app.web.sql_example_management_view import render_sql_example_management_page

# 页面基础配置
st.set_page_config(
    page_title="Data Agent - 智能数据查询助手",
    page_icon="🐋",
    layout="wide",
)

API_BASE = "http://localhost:8000/api"


def _prepare_chart_df(rows: list[dict]) -> pd.DataFrame:
    """把图表数据转换成 Altair 可直接使用的 DataFrame。"""
    df = pd.DataFrame(rows)
    for column in df.columns:
        # SSE 传输时 Decimal 会被序列化成字符串，pandas 3.0 默认又是 StringDtype，
        # 这里统一尝试还原成数值，否则 Altair 会把金额列当成文本，图表无法绘制。
        if pd.api.types.is_numeric_dtype(df[column]):
            continue
        converted = pd.to_numeric(df[column], errors="coerce")
        if len(converted) and converted.notna().all():
            df[column] = converted
    return df


def _render_chart(chart_config: dict) -> None:
    """根据后端生成的 chart_config 渲染图表。"""
    if not chart_config:
        return
    chart_type = chart_config.get("chart_type") or "table"
    if chart_type == "table":
        return

    rows = chart_config.get("data") or []
    if not rows:
        return
    df = _prepare_chart_df(rows)

    title = chart_config.get("title") or ""
    x_column = chart_config.get("x_column")
    y_columns = chart_config.get("y_columns") or []
    if not x_column or x_column not in df.columns:
        return
    y_columns = [column for column in y_columns if column in df.columns]
    if not y_columns:
        return

    if chart_type == "pie":
        chart = (
            alt.Chart(df)
            .mark_arc()
            .encode(
                theta=alt.Theta(field=y_columns[0], type="quantitative"),
                color=alt.Color(field=x_column, type="nominal", legend=alt.Legend(title=x_column)),
                tooltip=[x_column, y_columns[0]],
            )
            .properties(width=420, height=420)
        )
        if title:
            chart = chart.properties(title=title)
        st.altair_chart(chart, width="stretch")
        return

    if len(y_columns) == 1:
        y_field = y_columns[0]
        base = (
            alt.Chart(df)
            .encode(
                x=alt.X(field=x_column, type="nominal", title=x_column),
                tooltip=[x_column, y_field],
            )
        )
        if chart_type == "line":
            chart = base.mark_line(point=True).encode(
                y=alt.Y(field=y_field, type="quantitative", title=y_field)
            )
        else:
            chart = base.mark_bar().encode(
                y=alt.Y(field=y_field, type="quantitative", title=y_field)
            )
    else:
        melted = df.melt(
            id_vars=[x_column],
            value_vars=y_columns,
            var_name="指标",
            value_name="数值",
        )
        base = (
            alt.Chart(melted)
            .encode(
                x=alt.X(field=x_column, type="nominal", title=x_column),
                y=alt.Y(field="数值", type="quantitative"),
                color=alt.Color(field="指标", type="nominal"),
                tooltip=[x_column, "指标", "数值"],
            )
        )
        chart = base.mark_line(point=True) if chart_type == "line" else base.mark_bar()

    if title:
        chart = chart.properties(title=title)
    st.altair_chart(chart, width="stretch")


# ==============================================================================
# 0. 结构化卡片渲染模板 (纯 HTML 绝对居中表格渲染)
# ==============================================================================
def render_bi_report_card(report_data: dict, container=None):
    """
    统一渲染结构化 BI 问数报告卡片
    板块 1: 📈 数据来源与底层 SQL 审计 (置于上方)
    板块 2: 📊 数据分析结果 (采用纯 HTML <table> 深度居中与数值格式化)
    """
    ctx = container if container is not None else st.container()
    with ctx:
        table_data = report_data.get("data", [])
        sql_code = report_data.get("sql", "")
        source_tables = report_data.get("source_tables", [])
        row_count = report_data.get("row_count", len(table_data))
        insight = report_data.get("insight") or {}
        insight_summary = insight.get("summary")

        # ======================================================
        # 板块 1: 📈 数据来源与底层 SQL 审计
        # ======================================================
        st.markdown("#### 📈 数据来源")
        tables_tag = "、".join([f"`{t}`" for t in source_tables]) if source_tables else "`数仓物理表`"
        st.markdown(f"**关联数仓表**：{tables_tag}")

        if sql_code:
            with st.expander("🔍 底层执行 SQL 详情", expanded=True):
                st.code(sql_code, language="sql")

        st.markdown("---")

        if insight_summary:
            st.markdown(f"> 💡 **业务洞察**：{insight_summary}")

        # ======================================================
        # 板块 2: 📊 数据分析结果 (纯 HTML <table> 居中高质感渲染)
        # ======================================================
        st.markdown("#### 📊 数据分析结果")
        _render_chart(report_data.get("chart_config"))
        if table_data and isinstance(table_data, list):
            headers = list(table_data[0].keys())

            # 注入专业现代化商业表格 CSS 样式
            html_table = """
            <style>
                .custom-bi-table {
                    width: 100%;
                    border-collapse: collapse;
                    margin: 14px 0;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                    font-size: 15px;
                    border-radius: 8px;
                    overflow: hidden;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                    border: 1px solid #e9ecef;
                }
                .custom-bi-table thead tr {
                    background-color: #f8f9fa;
                }
                .custom-bi-table th {
                    color: #212529;
                    font-weight: 600;
                    text-align: center !important;
                    padding: 13px 18px;
                    border-bottom: 2px solid #dee2e6;
                    border-right: 1px solid #e9ecef;
                    letter-spacing: 0.3px;
                }
                .custom-bi-table th:last-child {
                    border-right: none;
                }
                .custom-bi-table td {
                    text-align: center !important;
                    padding: 12px 18px;
                    border-bottom: 1px solid #e9ecef;
                    border-right: 1px solid #e9ecef;
                    color: #495057;
                }
                .custom-bi-table td:last-child {
                    border-right: none;
                }
                .custom-bi-table tbody tr:nth-of-type(even) {
                    background-color: #fdfdfd;
                }
                .custom-bi-table tbody tr:hover {
                    background-color: #f1f3f5;
                    transition: background-color 0.15s ease-in-out;
                }
            </style>
            <table class="custom-bi-table">
                <thead>
                    <tr>""" + "".join([f"<th>{h}</th>" for h in headers]) + """</tr>
                </thead>
                <tbody>"""

            for row in table_data:
                html_table += "<tr>"
                for h in headers:
                    val = row.get(h, "")
                    # 格式化数值：消除 000000 长尾零、添加千分位
                    if isinstance(val, (int, float)) and not isinstance(val, bool):
                        display_val = f"{int(val):,}" if float(val).is_integer() else f"{float(val):,.2f}"
                    else:
                        try:
                            f_val = float(val)
                            display_val = f"{int(f_val):,}" if f_val.is_integer() else f"{f_val:,.2f}"
                        except (ValueError, TypeError):
                            display_val = str(val)

                    html_table += f"<td>{display_val}</td>"
                html_table += "</tr>"

            html_table += "</tbody></table>"

            st.markdown(html_table, unsafe_allow_html=True)
            st.caption(f"💡 检索成功，共统计到 **{row_count}** 条记录。")
        else:
            st.warning("⚠️ 数仓中未检索到符合条件的业务记录。")


def save_conversation_to_local_archive(messages: list, session_id: str) -> Path:
    ARCHIVE_ROOT_DIR = Path("archives")
    ARCHIVE_ROOT_DIR.mkdir(parents=True, exist_ok=True)

    time_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = ARCHIVE_ROOT_DIR / f"DataAgent_数据分析报告_{time_tag}_{session_id[:8]}.md"

    md_content = f"# Data Agent 数据分析报告\n\n"
    md_content += f"**Session ID**: `{session_id}`  \n"
    md_content += f"**导出时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n\n---\n\n"

    for msg in messages:
        role_emoji = "👤" if msg.get("role") == "user" else "🐋"
        role_name = "用户" if msg.get("role") == "user" else "助手"
        md_content += f"## {role_emoji} {role_name}\n\n"
        if msg.get("thoughts"):
            md_content += f"**思考过程**:\n" + "\n".join(msg.get("thoughts")) + "\n\n"
        if msg.get("type") == "bi_report":
            r_data = msg.get("report_data", {})
            md_content += f"**关联表**: {r_data.get('source_tables')}\n\n```sql\n{r_data.get('sql')}\n```\n\n"
            md_content += f"**数据条数**: {r_data.get('row_count')}\n\n"
        else:
            md_content += f"{msg.get('content', '')}\n\n"
        md_content += "---\n\n"

    file_path.write_text(md_content, encoding="utf-8")
    return file_path


# ==============================================================================
# 1. 会话状态管理
# ==============================================================================
if "session_id" not in st.session_state:
    st.session_state.session_id = f"data_agent_{uuid.uuid4().hex[:8]}"

if "messages" not in st.session_state:
    st.session_state.messages = []

if "page" not in st.session_state:
    st.session_state.page = "🐋 智能问数"
if "governance_tab" not in st.session_state:
    st.session_state.governance_tab = "✅ 字段语义审批"

# ==============================================================================
# 2. 侧边栏控制面板：顶部是功能导航，问数页显示会话与知识库运营
# ==============================================================================
with st.sidebar:
    st.subheader("🧭 功能导航")
    pages = ["🐋 智能问数", "🗂️ 元数据管理"]
    current_page = st.radio(
        "选择页面",
        pages,
        index=pages.index(st.session_state.page) if st.session_state.page in pages else 0,
    )
    st.session_state.page = current_page

    if current_page == "🗂️ 元数据管理":
        st.markdown("---")
        st.subheader("元数据治理")
        governance_tabs = ["✅ 字段语义审批", "📐 指标管理", "📚 金标 SQL 管理"]
        st.radio(
            "选择模块",
            governance_tabs,
            index=(
                governance_tabs.index(st.session_state.governance_tab)
                if st.session_state.governance_tab in governance_tabs
                else 0
            ),
            key="governance_tab",
        )

if current_page == "🐋 智能问数":
    with st.sidebar:
        st.subheader("⚙️ 会话控制")
        st.info(f"🔑 当前 Session ID:\n`{st.session_state.session_id}`")

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("🔄 重置会话", use_container_width=True):
                st.session_state.session_id = f"data_agent_{uuid.uuid4().hex[:8]}"
                st.session_state.messages = []
                st.rerun()

        with col_btn2:
            if st.button("💾 归档到本地", use_container_width=True, disabled=not bool(st.session_state.messages)):
                try:
                    saved_file = save_conversation_to_local_archive(st.session_state.messages, st.session_state.session_id)
                    st.toast(f"✅ 已归档至: {saved_file.name}", icon="📁")
                except Exception as e:
                    st.error(f"❌ 归档异常: {e}")

if current_page == "🗂️ 元数据管理":
    if st.session_state.governance_tab == "📐 指标管理":
        render_metric_management_page(API_BASE, embedded=True)
    elif st.session_state.governance_tab == "📚 金标 SQL 管理":
        render_sql_example_management_page(API_BASE, embedded=True)
    else:
        render_meta_governance_page(API_BASE, embedded=True)
    st.stop()

st.title("🐋 Data Agent 智能数据查询助手")
st.caption("基于 FastAPI + LangGraph + DeepSeek 构建的实时流式数据分析智能体 📊")

# ==============================================================================
# 3. 渲染历史聊天记录 (思考过程默认收起)
# ==============================================================================
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("thoughts"):
            with st.expander("🧠 思考分析链路", expanded=False):
                for t in msg.get("thoughts"):
                    st.write(t)
        if msg.get("type") == "bi_report":
            render_bi_report_card(msg.get("report_data", {}))
        else:
            st.markdown(msg.get("content", ""))

# ==============================================================================
# 4. 处理用户输入与实时流式响应
# ==============================================================================
user_query = st.chat_input("请输入您的业务数据查询或问候问题...")

if user_query:
    st.session_state.messages.append({"role": "user", "type": "text", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        thought_logs = []
        status_container = st.status("🤔 智能体正在解析业务意图...", expanded=True)
        card_container = st.container()
        text_placeholder = st.empty()

        full_text_answer = ""
        report_payload = None
        error_message = None

        try:
            response = requests.post(
                f"{API_BASE}/query",
                json={"query": user_query},
                stream=True,
                timeout=120,
            )
            response.encoding = "utf-8"

            if response.status_code != 200:
                status_container.update(label="❌ 后端调用失败", state="error", expanded=True)
                error_message = "服务暂时不可用，请稍后重试；若问题持续存在，请联系管理员。"
                text_placeholder.error(error_message)
            else:
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue

                    line_str = line.strip()
                    if line_str.startswith("data: "):
                        data_str = line_str[6:].strip()
                        if not data_str:
                            continue

                        try:
                            data = json.loads(data_str)

                            if "error" in data:
                                status_container.update(label="❌ 执行异常中断", state="error", expanded=True)
                                error_message = f"❌ 错误: {data['error']}"
                                text_placeholder.error(error_message)
                                break

                            if data.get("type") == "error_response":
                                error_message = data.get("content") or "本次查询未能完成，请稍后重试。"
                                status_container.update(label="❌ 查询未完成", state="error", expanded=True)
                                text_placeholder.error(error_message)
                                continue

                            if "stage" in data or data.get("type") == "thought":
                                step_msg = data.get("content") or data.get("stage")
                                status_container.write(f"🔹 {step_msg}")
                                status_container.update(label=f"⚡ 正在进行: {step_msg}...", state="running", expanded=True)
                                thought_logs.append(f"🔹 {step_msg}")
                                continue

                            if data.get("type") == "bi_report":
                                status_container.update(label="✅ 数据分析完成", state="complete", expanded=False)
                                report_payload = data
                                render_bi_report_card(report_payload, container=card_container)
                                continue

                            token = data.get("content") or data.get("token") or data.get("answer_chunk")
                            if token:
                                status_container.update(label="💬 正在回复...", state="running", expanded=True)
                                full_text_answer += str(token)
                                text_placeholder.markdown(full_text_answer + "▌")

                        except json.JSONDecodeError:
                            continue

                if status_container and error_message is None:
                    status_container.update(label="✅ 任务执行完毕", state="complete", expanded=False)

                if report_payload:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "type": "bi_report",
                        "report_data": report_payload,
                        "thoughts": thought_logs
                    })
                elif full_text_answer:
                    text_placeholder.markdown(full_text_answer)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "type": "text",
                        "content": full_text_answer,
                        "thoughts": thought_logs
                    })
                elif error_message:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "type": "error",
                        "content": error_message,
                        "thoughts": thought_logs,
                    })

        except Exception as e:
            if status_container:
                status_container.update(label="❌ 通信中断", state="error", expanded=True)
            error_message = "与数据服务的连接中断，请确认后端服务已启动后重试。"
            text_placeholder.error(error_message)
            st.session_state.messages.append({
                "role": "assistant",
                "type": "error",
                "content": error_message,
                "thoughts": thought_logs,
            })
