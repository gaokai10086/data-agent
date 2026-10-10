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
import html
import hashlib
import logging
import re
import requests
import streamlit as st
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.web.meta_governance_view import render_meta_governance_page
from app.web.metric_management_view import render_metric_management_page
from app.web.sql_example_management_view import render_sql_example_management_page
from app.web.chatbi.chart_renderer import render_chart
from app.core.llm import LLMFactory
from app.config.app_config import DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER

# 页面基础配置
st.set_page_config(
    page_title="ChatBI - 对话式数据分析",
    page_icon="📊",
    layout="wide",
)

API_BASE = "http://localhost:8000/api"
logger = logging.getLogger(__name__)


def _format_value(value) -> str:
    """统一 KPI 中的数字格式。"""
    if value is None:
        return "-"
    try:
        numeric = float(value)
        return f"{int(numeric):,}" if numeric.is_integer() else f"{numeric:,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _display_model_name(provider: str, model_tier: str) -> str:
    """把内部模型名转换成页面展示名，不展示 provider 与规格分隔符。"""
    model_name = LLMFactory.model_name(provider, model_tier)
    if provider.lower() == "deepseek":
        model_name = re.sub(r"^deepseek", "DeepSeek", model_name, flags=re.IGNORECASE)
        model_name = re.sub(r"-flash$", "-Flash", model_name, flags=re.IGNORECASE)
        model_name = re.sub(r"-pro$", "-Pro", model_name, flags=re.IGNORECASE)
    return model_name


def _split_top_level_csv(text: str) -> list[str]:
    """按括号层级拆分 SELECT 字段，避免拆开函数参数。"""
    fields, start, depth, quote = [], 0, 0, None
    for index, char in enumerate(text):
        if char in ("'", '"'):
            quote = None if quote == char else char if quote is None else quote
        elif quote is None:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                fields.append(text[start:index].strip())
                start = index + 1
    fields.append(text[start:].strip())
    return [field for field in fields if field]


def _format_sql_for_display(sql: str) -> str:
    """仅格式化前端展示的 SQL，不改变后端真正执行的 SQL。"""
    normalized = re.sub(r"\s+", " ", str(sql or "")).strip()
    if not normalized:
        return ""
    normalized = re.sub(
        r"\s+(FROM|WHERE|LEFT JOIN|RIGHT JOIN|INNER JOIN|FULL JOIN|GROUP BY|HAVING|ORDER BY|LIMIT|UNION)\s+",
        lambda match: f"\n{match.group(1).upper()} ",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\s+(AND|OR)\s+",
        lambda match: f"\n  {match.group(1).upper()} ",
        normalized,
        flags=re.IGNORECASE,
    )
    select_match = re.match(r"SELECT\s+(.*?)\nFROM\s+", normalized, flags=re.IGNORECASE | re.DOTALL)
    if select_match:
        select_fields = _split_top_level_csv(select_match.group(1))
        replacement = "SELECT\n  " + ",\n  ".join(select_fields) + "\nFROM "
        normalized = replacement + normalized[select_match.end():]
    return normalized


def _queue_follow_up(query: str) -> None:
    st.session_state.pending_query = query
    st.rerun()


def render_landing_capabilities() -> None:
    """空会话时展示产品能力入口与可直接执行的示例问题。"""
    st.markdown(
        """
        <style>
            .landing-intro { margin: .15rem 0 1rem; color: #667085; font-size: .92rem; }
            .landing-card-title { color: #172033; font-size: 1.08rem; font-weight: 720; margin-bottom: .35rem; }
            .landing-card-title .landing-icon { margin-right: .42rem; }
            .landing-card-copy { color: #667085; font-size: .84rem; line-height: 1.55; min-height: 2.7rem; }
            .landing-card-meta { color: #98a2b3; font-size: .76rem; margin-top: .8rem; }
            div[data-testid="stVerticalBlock"] div[data-testid="stButton"] button {
                border-radius: 10px;
                border: 1px solid #e4e7ec;
                color: #344054;
                background: #fff;
                text-align: left;
                font-size: .84rem;
                min-height: 2.35rem;
                transition: all .15s ease;
            }
            div[data-testid="stVerticalBlock"] div[data-testid="stButton"] button:hover {
                border-color: #9dbcf9;
                color: #175cd3;
                background: #f5f8ff;
            }
        </style>
        <p class="landing-intro">选择一个分析方向，或直接在下方输入你的业务问题。</p>
        """,
        unsafe_allow_html=True,
    )

    question_groups = [
        (
            "📊",
            "智能问数",
            "查询指标、拆分维度，并自动生成图表与分析结论。",
            [
                "2025年每个月的销售额是多少？",
                "各大区的销售额是多少？",
                "各门店的订单数量是多少？",
            ],
            "已上线 · 支持自然语言查询",
        ),
        (
            "🔎",
            "异动归因诊断",
            "发现指标异常，逐层下钻到维度、渠道和具体业务原因。",
            ["为什么2025年7月销售额比6月下降了？"],
            "测试版 · 当前先验证请求分流与解析",
        ),
    ]

    columns = st.columns(2, gap="large")
    for card_index, (column, (icon, title, description, examples, meta)) in enumerate(
        zip(columns, question_groups)
    ):
        with column:
            with st.container(border=True):
                st.markdown(
                    f"<div class='landing-card-title'><span class='landing-icon'>{icon}</span>{title}</div>"
                    f"<div class='landing-card-copy'>{description}</div>"
                    f"<div class='landing-card-meta'>{meta}</div>",
                    unsafe_allow_html=True,
                )
                if examples:
                    st.markdown("<div style='height:.55rem'></div>", unsafe_allow_html=True)
                    for index, example in enumerate(examples):
                        if st.button(
                            example,
                            key=f"landing_example_{card_index}_{index}",
                            use_container_width=True,
                        ):
                            _queue_follow_up(example)
                else:
                    st.markdown(
                        "<div style='margin-top:1.1rem;padding:.65rem .75rem;border-radius:10px;"
                        "background:#f8f9fc;color:#98a2b3;font-size:.82rem;'>"
                        "当前没有可用的示例问题</div>",
                        unsafe_allow_html=True,
                    )


# ==============================================================================
# 0. 结构化卡片渲染模板 (纯 HTML 绝对居中表格渲染)
# ==============================================================================
def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _render_anomaly_bar_chart(rows: list[dict], title: str, chart_key: str) -> None:
    """将异动归因数据适配为共享 ChatBI 图表协议。

    异动分析只负责提供“因素/变化金额”等业务字段，具体的 ECharts
    配色、排序、响应式尺寸和交互由智能问数共用的 ``render_chart`` 负责。
    """
    if not rows:
        return
    # Streamlit 自定义组件的 key 使用 ASCII 更稳妥，尤其是在同一组 tabs
    # 中连续挂载多个 ECharts 实例时，避免中文维度名导致组件复用或丢失。
    safe_chart_key = "anomaly_" + hashlib.md5(chart_key.encode("utf-8")).hexdigest()[:12]
    render_chart(
        {
            "chart_type": "bar",
            "title": title,
            "data": rows,
            "x_column": "因素",
            "y_columns": ["变化金额"],
            "metric_semantics": {"变化金额": {"display_type": "number"}},
            "presentation": {"orientation": "horizontal", "sort": "asc"},
        },
        chart_key=safe_chart_key,
    )


def render_anomaly_report_card(payload: dict, container=None, card_instance_key: str | None = None):
    """渲染异动分析的 KPI、归因表和多维对比图。"""
    ctx = container if container is not None else st.container()
    report = payload.get("report") or {}
    analysis = payload.get("analysis") or {}
    overall = report.get("overall") or analysis.get("overall") or {}
    attributions = analysis.get("attributions") or []
    summaries = analysis.get("dimension_summaries") or []
    request = payload.get("request") or {}

    with ctx:
        st.markdown("### 🔎 异动归因分析")
        target_period = request.get("target_period") or {}
        comparison = request.get("comparison") or {}
        comparison_period = comparison.get("period") if isinstance(comparison, dict) else {}
        target_label = (
            target_period.get("label") if isinstance(target_period, dict) else str(target_period)
        ) or "目标周期"
        comparison_label = (
            comparison_period.get("label") if isinstance(comparison_period, dict) else str(comparison_period)
        ) or "对比周期"
        st.caption(
            f"{request.get('metric') or '销售额'} · "
            f"{target_label} 对比 {comparison_label}"
        )

        kpi_columns = st.columns(4)
        current_value = _safe_float(overall.get("current_value"))
        comparison_value = _safe_float(overall.get("comparison_value"))
        change = _safe_float(overall.get("change"))
        change_rate = overall.get("change_rate")
        kpi_columns[0].metric("当前周期", _format_value(current_value))
        kpi_columns[1].metric("对比周期", _format_value(comparison_value))
        kpi_columns[2].metric("变化金额", _format_value(change), delta=_format_value(change))
        kpi_columns[3].metric(
            "变化率",
            f"{_safe_float(change_rate) * 100:.1f}%" if change_rate is not None else "-",
        )

        final_answer = report.get("final_answer") or payload.get("content")
        if final_answer:
            st.info(final_answer)

        level_one = [item for item in attributions if item.get("level", 1) == 1]
        if level_one:
            st.markdown("#### 第一层：大区贡献")
            rows = [
                {
                    "大区": item.get("value"),
                    "当前值": _safe_float(item.get("current_value")),
                    "对比值": _safe_float(item.get("comparison_value")),
                    "变化金额": _safe_float(item.get("change")),
                    "变化率": f"{_safe_float(item.get('change_rate')) * 100:.1f}%"
                    if item.get("change_rate") is not None else "-",
                    "净变化贡献": f"{_safe_float(item.get('contribution_rate')) * 100:.1f}%"
                    if item.get("contribution_rate") is not None else "-",
                }
                for item in level_one
            ]
            st.dataframe(rows, hide_index=True, use_container_width=True)
            _render_anomaly_bar_chart(
                [{"因素": row["大区"], "变化金额": row["变化金额"]} for row in rows],
                "各大区销售额变化",
                f"{card_instance_key or 'anomaly'}_level1_region",
            )

        level_two = [item for item in attributions if item.get("level") == 2]
        if level_two:
            st.markdown("#### 第二层：当前异常范围内的多维诊断")
            dimensions = list(dict.fromkeys(item.get("dimension") for item in level_two))
            tabs = st.tabs([f"{dimension}" for dimension in dimensions])
            for tab, dimension in zip(tabs, dimensions):
                with tab:
                    dimension_items = [item for item in level_two if item.get("dimension") == dimension]
                    dimension_items.sort(key=lambda item: abs(_safe_float(item.get("change"))), reverse=True)
                    table_rows = [
                        {
                            "因素": item.get("value"),
                            "当前值": _safe_float(item.get("current_value")),
                            "对比值": _safe_float(item.get("comparison_value")),
                            "变化金额": _safe_float(item.get("change")),
                            "局部贡献": f"{_safe_float(item.get('contribution_rate')) * 100:.1f}%"
                            if item.get("contribution_rate") is not None else "-",
                        }
                        for item in dimension_items
                    ]
                    st.dataframe(table_rows, hide_index=True, use_container_width=True)
                    _render_anomaly_bar_chart(
                        table_rows,
                        f"{dimension}维度变化",
                        f"{card_instance_key or 'anomaly'}_level2_{dimension}",
                    )

            if summaries:
                summary_rows = [
                    {
                        "维度": item.get("dimension"),
                        "Top1因素": item.get("top_value"),
                        "Top1影响占比": f"{_safe_float(item.get('top1_influence')) * 100:.1f}%",
                        "Top3覆盖率": f"{_safe_float(item.get('top3_coverage')) * 100:.1f}%",
                        "证据是否充分": "是" if item.get("sufficient") else "否",
                    }
                    for item in summaries if item.get("level") == 2
                ]
                st.markdown("##### 维度解释能力对比")
                st.dataframe(summary_rows, hide_index=True, use_container_width=True)

        with st.expander("查看结构化分析结果", expanded=False):
            st.json({
                "metric": payload.get("metric") or {},
                "execution": payload.get("execution") or {},
                "report": report,
            })


def render_bi_report_card(report_data: dict, container=None, card_instance_key: str | None = None):
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
        question = report_data.get("question") or "数据分析结果"
        facts = insight.get("kpis") or insight.get("facts") or []
        content_key = hashlib.md5(
            f"{question}:{row_count}:{sql_code}".encode("utf-8")
        ).hexdigest()[:10]
        # 内容摘要用于图表 key；卡片实例 key 负责区分历史中重复执行的同一问题。
        # 同一份报告可能同时出现在历史记录和当前流式响应中，不能只依赖内容摘要。
        report_key = card_instance_key or content_key

        st.markdown(
            f"<div class='bi-report-title'><span>📊</span><div><div class='bi-kicker'>CHATBI ANALYSIS</div>"
            f"<div class='bi-title'>{html.escape(str(question))}</div></div></div>",
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <style>
                .bi-report-title { display:flex; align-items:center; gap:12px; margin:4px 0 14px; }
                .bi-report-title > span { font-size:28px; }
                .bi-kicker { color:#7b8794; font-size:11px; letter-spacing:1.4px; font-weight:700; }
                .bi-title { color:#182230; font-size:22px; font-weight:700; line-height:1.35; }
                .bi-fact-label { color:#7b8794; font-size:12px; }
                .bi-fact-value { color:#182230; font-size:22px; font-weight:700; }
                .chatbi-hint { color:#667085; font-size:13px; margin:4px 0 12px; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        if facts:
            fact_columns = st.columns(min(4, len(facts)))
            for index, fact in enumerate(facts[:4]):
                with fact_columns[index % len(fact_columns)]:
                    label = fact.get("label") or fact.get("title", "指标")
                    value = fact.get("value")
                    unit = fact.get("unit") or ""
                    display_value = value if isinstance(value, str) else _format_value(value)
                    st.markdown(
                        f"<div class='bi-fact-label'>{html.escape(str(label))}</div>"
                        f"<div class='bi-fact-value'>{html.escape(str(display_value))} {html.escape(str(unit))}</div>"
                        f"<div class='bi-fact-label'>{html.escape(str(fact.get('description', '')))}</div>",
                        unsafe_allow_html=True,
                    )

        # ======================================================
        # 板块 1: 业务洞察
        # ======================================================
        if insight_summary:
            st.markdown(
                f"<div class='chatbi-hint'>💡 <b>分析结论</b>　{html.escape(str(insight_summary))}</div>",
                unsafe_allow_html=True,
            )

        # ======================================================
        # ChatBI 结果区：图表、数据、SQL 按用户需要展开
        # ======================================================
        chart_tab, data_tab, query_tab = st.tabs(["📈 可视化", "📋 数据明细", "🔍 查询详情"])
        with chart_tab:
            chart_config = report_data.get("chart_config") or {}
            chart_type = chart_config.get("chart_type") or "table"
            render_chart(chart_config, chart_key=f"{report_key}_{chart_type}")
            if chart_type == "table":
                st.caption("当前结果更适合以表格查看。")

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

            with data_tab:
                st.caption(f"共 {row_count} 条记录，默认展示全部查询结果。")
                st.markdown(html_table, unsafe_allow_html=True)
        else:
            with data_tab:
                st.warning("⚠️ 数仓中未检索到符合条件的业务记录。")

        with query_tab:
            tables_tag = "、".join([f"`{t}`" for t in source_tables]) if source_tables else "`数仓物理表`"
            st.markdown(f"**关联数仓表**：{tables_tag}")
            if sql_code:
                st.code(_format_sql_for_display(sql_code), language="sql")

        st.markdown("**继续分析**")
        actions = report_data.get("actions") or []
        follow_up_columns = st.columns(min(3, len(actions))) if actions else []
        for action_index, (column, action) in enumerate(zip(follow_up_columns, actions[:3])):
            label = action.get("label") or "继续分析"
            follow_up = action.get("query")
            if not follow_up:
                continue
            with column:
                # LLM 可能为不同建议返回相同 id（例如都叫 ranking）。
                # Streamlit 的 key 必须在整次页面运行中唯一，因此加入稳定的建议序号。
                action_id = action.get("id") or label
                button_key = f"follow_up_{report_key}_{action_index}_{action_id}"
                if st.button(label, key=button_key, use_container_width=True):
                    _queue_follow_up(follow_up)


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

st.markdown(
    """
    <style>
        .chatbi-hero {
            margin: 0.25rem 0 1.45rem;
            padding: 1.35rem 1.55rem 1.25rem;
            border: 1px solid #e4e7ec;
            border-radius: 20px;
            background:
                radial-gradient(circle at 88% 10%, rgba(37, 99, 235, .10), transparent 33%),
                linear-gradient(135deg, #ffffff 0%, #f8fbff 100%);
            box-shadow: 0 8px 26px rgba(16, 24, 40, .05);
        }
        .chatbi-brand-row {
            display: flex;
            align-items: center;
            gap: .8rem;
        }
        .chatbi-mark {
            display: flex;
            align-items: end;
            gap: 3px;
            width: 2.55rem;
            height: 2.55rem;
            padding: .42rem .48rem;
            box-sizing: border-box;
            border-radius: 11px;
            background: #eef4ff;
            border: 1px solid #d9e6ff;
        }
        .chatbi-mark span { display: block; width: 5px; border-radius: 3px 3px 1px 1px; }
        .chatbi-mark .bar-green { height: 1.0rem; background: #43b581; }
        .chatbi-mark .bar-red { height: 1.38rem; background: #ef5350; }
        .chatbi-mark .bar-blue { height: 1.72rem; background: #2563eb; }
        .chatbi-kicker {
            margin: 0 0 .12rem;
            color: #667085;
            font-size: .68rem;
            font-weight: 700;
            letter-spacing: .16em;
            text-transform: uppercase;
        }
        .chatbi-name {
            margin: 0;
            color: #172033;
            font-size: 1.65rem;
            line-height: 1.08;
            font-weight: 760;
            letter-spacing: -.035em;
        }
        .chatbi-name span { color: #2563eb; }
        .chatbi-pill {
            margin-left: auto;
            padding: .42rem .72rem;
            color: #175cd3;
            background: #eff6ff;
            border: 1px solid #dbeafe;
            border-radius: 999px;
            font-size: .78rem;
            font-weight: 600;
            white-space: nowrap;
        }
        .chatbi-copy {
            margin: 1.05rem 0 0;
            color: #344054;
            font-size: 1.16rem;
            font-weight: 650;
            letter-spacing: -.015em;
        }
        .chatbi-description {
            margin: .32rem 0 0;
            color: #667085;
            font-size: .9rem;
        }
        @media (max-width: 640px) {
            .chatbi-hero { padding: 1.1rem; border-radius: 16px; }
            .chatbi-pill { display: none; }
            .chatbi-copy { font-size: 1.02rem; }
        }
    </style>
    <section class="chatbi-hero">
        <div class="chatbi-brand-row">
            <div class="chatbi-mark" aria-hidden="true">
                <span class="bar-green"></span><span class="bar-red"></span><span class="bar-blue"></span>
            </div>
            <div>
                <p class="chatbi-kicker">DATA INTELLIGENCE</p>
                <p class="chatbi-name">Chat<span>BI</span></p>
            </div>
            <span class="chatbi-pill">对话式数据分析</span>
        </div>
        <p class="chatbi-copy">用自然语言探索业务数据</p>
        <p class="chatbi-description">查询指标、生成图表，并获得可解释的分析结论</p>
    </section>
    """,
    unsafe_allow_html=True,
)

if not st.session_state.messages:
    render_landing_capabilities()

# ==============================================================================
# 3. 渲染历史聊天记录（只展示业务结果，不展示内部节点轨迹）
# ============================================================================== 
for message_index, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        if msg.get("execution_details"):
            with st.expander("📊 执行结果详情", expanded=False):
                for detail in msg["execution_details"]:
                    st.write(f"🔹 {detail}")
        if msg.get("type") == "bi_report":
            render_bi_report_card(
                msg.get("report_data", {}),
                card_instance_key=f"history_{message_index}",
            )
        elif msg.get("type") == "anomaly_report":
            render_anomaly_report_card(
                msg.get("report_data", {}),
                card_instance_key=f"history_anomaly_{message_index}",
            )
        else:
            st.markdown(msg.get("content", ""))

# ==============================================================================
# 4. 处理用户输入与实时流式响应
# ============================================================================== 
provider_options = LLMFactory.available_providers()
model_choices = [
    f"{provider}:{tier}"
    for provider in provider_options
    for tier in LLMFactory.available_model_tiers(provider)
]
default_model_choice = f"{DEFAULT_LLM_PROVIDER}:{DEFAULT_LLM_MODEL}"
# Streamlit 会跨页面刷新保留 session_state；无选择或旧值失效时回到配置默认模型。
if st.session_state.get("selected_llm_choice") not in model_choices:
    st.session_state["selected_llm_choice"] = default_model_choice

pending_query = st.session_state.pop("pending_query", None)
if pending_query:
    # 推荐问题通过按钮触发时，沿用用户上一次选择的模型。
    selected_model = st.session_state.get("selected_llm_choice")
    if selected_model:
        selected_provider, selected_model_tier = selected_model.split(":", 1)
        user_query = pending_query
    else:
        st.warning("请先选择模型，再提交推荐问题。")
        user_query = None
else:
    if not st.session_state.messages:
        st.markdown(
            """
            <style>
                .free-query-box {
                    margin: 1.25rem 0 .45rem;
                    padding: .85rem 1rem .2rem;
                    border: 1px solid #e4e7ec;
                    border-radius: 16px;
                    background: linear-gradient(135deg, #ffffff 0%, #f8fbff 100%);
                }
                .free-query-title { color: #344054; font-size: .92rem; font-weight: 700; }
                .free-query-hint { color: #98a2b3; font-size: .78rem; margin-top: .18rem; }
            </style>
            <div class="free-query-box">
                <div class="free-query-title">💬 还有其他问题？</div>
                <div class="free-query-hint">推荐分析之外，也可以直接输入新的业务问题，继续和 ChatBI 对话。</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    model_labels = {
        f"{provider}:{tier}": _display_model_name(provider, tier)
        for provider in provider_options
        for tier in LLMFactory.available_model_tiers(provider)
    }

    # 输入框、模型选择和发送按钮使用同一个表单，确保三者真正处于同一行。
    with st.form("chat_input_form", clear_on_submit=True, border=False):
        input_col, model_col, send_col = st.columns(
            [8.0, 1.5, 0.5],
            gap="small",
            vertical_alignment="bottom",
        )
        with input_col:
            typed_query = st.text_input(
                "业务问题",
                placeholder="输入你的业务问题...",
                label_visibility="collapsed",
            )
        with model_col:
            selected_model = st.selectbox(
                "模型",
                options=model_choices,
                format_func=lambda value: model_labels.get(value, value),
                key="selected_llm_choice",
                label_visibility="collapsed",
            )
        with send_col:
            submitted = st.form_submit_button(
                "↑",
                type="primary",
                use_container_width=True,
            )

    if submitted and not selected_model:
        st.warning("请先选择模型，再发送问题。")
    selected_provider, selected_model_tier = (
        selected_model.split(":", 1) if selected_model else (None, None)
    )
    user_query = typed_query if submitted and typed_query.strip() and selected_model else None

if user_query:
    # 发送前再次读取当前会话选择，避免推荐问题或 Streamlit rerun 使用旧的局部变量。
    selected_model = st.session_state.get("selected_llm_choice")
    if not selected_model:
        st.error("当前请求未选择模型。")
        st.stop()
    selected_provider, selected_model_tier = selected_model.split(":", 1)
    st.session_state.messages.append({"role": "user", "type": "text", "content": user_query})
    with st.chat_message("user"):
        st.markdown(user_query)

    with st.chat_message("assistant"):
        # 进度采用单个状态条原地更新，不把每个节点逐条展开推送。
        status_container = st.status("🤔 正在处理请求...", expanded=False)
        card_container = st.container(border=True)
        text_placeholder = st.empty()

        full_text_answer = ""
        report_payload = None
        anomaly_payload = None
        error_message = None
        execution_details = []

        try:
            response = requests.post(
                f"{API_BASE}/query",
                json={
                    "query": user_query,
                    "provider": selected_provider,
                    "model_tier": selected_model_tier,
                },
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

                            if data.get("type") == "progress":
                                status_container.update(
                                    label=data.get("content") or "正在处理请求...",
                                    state="running",
                                    expanded=False,
                                )
                                continue

                            if data.get("type") == "node_result":
                                detail = data.get("content")
                                if detail:
                                    execution_details.append(str(detail))
                                    status_container.write(f"🔹 {detail}")
                                    status_container.update(
                                        label="📊 正在汇总查询结果...",
                                        state="running",
                                        expanded=True,
                                    )
                                continue

                            if data.get("type") == "bi_report":
                                status_container.update(label="✅ 数据分析完成", state="complete", expanded=False)
                                report_payload = data
                                render_bi_report_card(
                                    report_payload,
                                    container=card_container,
                                    card_instance_key="live_report",
                                )
                                continue

                            if data.get("type") == "anomaly_result":
                                status_container.update(label="✅ 异动分析完成", state="complete", expanded=False)
                                anomaly_payload = data
                                full_text_answer = data.get("content") or "异动分析已完成，但暂时没有形成可解释结论。"
                                render_anomaly_report_card(
                                    anomaly_payload,
                                    container=card_container,
                                    card_instance_key="live_anomaly_report",
                                )
                                continue

                            token = data.get("content") or data.get("token") or data.get("answer_chunk")
                            if token:
                                status_container.update(label="💬 正在回复...", state="running", expanded=False)
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
                        "execution_details": execution_details,
                    })
                elif anomaly_payload:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "type": "anomaly_report",
                        "report_data": anomaly_payload,
                        "execution_details": execution_details,
                    })
                elif full_text_answer:
                    text_placeholder.markdown(full_text_answer)
                    st.session_state.messages.append({
                        "role": "assistant",
                        "type": "text",
                        "content": full_text_answer,
                        "execution_details": execution_details,
                    })
                elif error_message:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "type": "error",
                        "content": error_message,
                        "execution_details": execution_details,
                    })

        except Exception as e:
            logger.exception("ChatBI 前端处理或渲染失败")
            if status_container:
                status_container.update(label="❌ 页面处理失败", state="error", expanded=True)
            error_message = "报告已返回，但页面处理失败。请查看 Streamlit 运行日志定位具体原因。"
            text_placeholder.error(error_message)
            st.session_state.messages.append({
                "role": "assistant",
                "type": "error",
                "content": error_message,
                "execution_details": execution_details,
            })

        # 查询完成后重新按“历史消息 → 输入框”的顺序渲染，确保自由提问入口
        # 位于最新回答之后，而不是停留在本次请求开始前的位置。
        st.rerun()
