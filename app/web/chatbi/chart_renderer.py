"""可复用的 ChatBI 图表渲染器。

该模块只负责将统一 chart_config 渲染为 Streamlit + ECharts，不依赖具体 Agent。
"""

from __future__ import annotations

import pandas as pd
from streamlit_echarts import st_echarts


def _prepare_chart_df(rows: list[dict]) -> pd.DataFrame:
    """把报告协议中的行数据转换成 ECharts 使用的 DataFrame。

    SSE/JSON 传输可能把 Decimal 数值变成字符串；这里仅做展示层类型恢复，
    不修改后端执行结果。
    """
    df = pd.DataFrame(rows)
    for column in df.columns:
        if pd.api.types.is_numeric_dtype(df[column]):
            continue
        converted = pd.to_numeric(df[column], errors="coerce")
        if len(converted) and converted.notna().all():
            df[column] = converted
    return df


def _compact_chart_value(value, percent: bool = False) -> str:
    """生成紧凑的数据标签，避免大数或百分比遮挡图表。"""
    if value is None:
        return ""
    number = float(value)
    if percent:
        return f"{number:.1f}%"
    absolute = abs(number)
    if absolute >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if absolute >= 10_000:
        return f"{number / 10_000:.1f}万"
    return f"{number:,.0f}" if number.is_integer() else f"{number:,.2f}"


def _chart_presentation(chart_config: dict, df: pd.DataFrame, x_column: str) -> dict:
    """根据数据形状补齐展示策略，同时保留后端传入的显式配置。"""
    presentation = dict(chart_config.get("presentation") or {})
    y_columns = chart_config.get("y_columns") or []
    labels = df[x_column].astype(str).tolist() if x_column in df.columns else []
    average_label_length = sum(map(len, labels)) / len(labels) if labels else 0
    if chart_config.get("chart_type") == "bar":
        presentation.setdefault("orientation", "horizontal" if len(labels) > 8 or average_label_length > 8 else "vertical")
        presentation.setdefault("sort", "desc")
        presentation.setdefault("limit", 12)
    if chart_config.get("chart_type") == "pie":
        presentation.setdefault("sort", "desc")
    if y_columns:
        presentation.setdefault("color_scheme", "blues")
    return presentation


def _is_rate_metric(column: str) -> bool:
    """通过字段名识别比例/增长类指标，仅作为没有显式元数据时的兜底规则。"""
    name = str(column).lower()
    return any(token in name for token in ("率", "同比", "环比", "增长", "rate", "growth", "%"))


def _is_percentage_metric(column: str, metric_semantics: dict) -> bool:
    """优先采用 LLM 展示语义；语义缺失时再回退字段名识别。"""
    semantics = metric_semantics.get(column) or {}
    return (
        semantics.get("display_type") in {"percentage", "ratio"}
        or semantics.get("unit") == "%"
        or semantics.get("axis") == "right"
        or (not semantics and _is_rate_metric(column))
    )


def _metric_label(column: str, metric_semantics: dict) -> str:
    """把 LLM 推断的单位附加到图例/坐标轴名称。"""
    unit = str((metric_semantics.get(column) or {}).get("unit") or "").strip()
    column_name = str(column)
    # 字段本身可能已经带有单位，例如「环比增长率(%)」；单位格式化必须幂等，
    # 避免图例和 Tooltip 变成「环比增长率(%) (%)」。
    if not unit or unit in column_name or f"({unit})" in column_name or f"（{unit}）" in column_name:
        return column_name
    return f"{column_name} ({unit})"


def _echarts_values(series: pd.Series) -> list:
    """把 pandas/numpy 标量转换成可 JSON 序列化的 Python 值。"""
    values = []
    for value in series.tolist():
        if pd.isna(value):
            values.append(None)
        elif hasattr(value, "item"):
            values.append(value.item())
        else:
            values.append(value)
    return values


def _normalize_rate_values(values: list, value_scale: str | None = None) -> list:
    """统一比例指标的展示单位为“百分数值”。

    查询结果可能返回两种形态：
    - 0.8637：比例值，展示为 86.37%
    - 86.37：已经是百分数值，展示仍为 86.37%

    当前根据数值范围兼容两种结果；后续如果指标注册中心提供 unit，
    应优先使用显式单位而不是启发式判断。
    """
    numeric_values = [float(value) for value in values if value is not None]
    if value_scale == "fraction":
        is_fraction = True
    elif value_scale in {"percent", "absolute"}:
        is_fraction = False
    else:
        # LLM 语义不可用时保留兼容逻辑。
        is_fraction = bool(numeric_values) and max(abs(value) for value in numeric_values) <= 1
    return [
        None if value is None else round(float(value) * 100, 2) if is_fraction else float(value)
        for value in values
    ]


def _render_line_panel(
    df,
    x_column,
    y_columns,
    title,
    percent_axis=False,
    series_dimension=None,
    metric_semantics=None,
    chart_key="line",
):
    """渲染普通折线图；series_dimension 存在时按分类维度拆分多条线。"""
    categories = [str(value) for value in df[x_column].tolist()]
    groups = []
    if series_dimension and series_dimension in df.columns:
        categories = list(dict.fromkeys(categories))
        for group in df[series_dimension].astype(str).drop_duplicates().tolist():
            groups.append((group, df[df[series_dimension].astype(str) == group]))
    else:
        groups = [(column, df) for column in y_columns]

    series = []
    show_labels = len(categories) <= 18
    metric_semantics = metric_semantics or {}
    metric_unit = str((metric_semantics.get(y_columns[0]) or {}).get("unit") or "").strip()
    for name, series_df in groups:
        column = y_columns[0]
        if series_dimension:
            value_by_category = {str(row[x_column]): row[column] for _, row in series_df.iterrows()}
            values = [value_by_category.get(category) for category in categories]
        else:
            values = _echarts_values(series_df[column])
        if percent_axis:
            semantics = (metric_semantics or {}).get(column) or {}
            values = _normalize_rate_values(values, semantics.get("value_scale"))
        series.append({
            "name": name if series_dimension else _metric_label(name, metric_semantics),
            "type": "line", "smooth": True, "symbol": "circle", "symbolSize": 7,
            "showSymbol": True, "lineStyle": {"width": 3}, "areaStyle": {"opacity": 0.05},
            "label": {"show": show_labels, "position": "top", "fontSize": 10, "color": "#344054",
                      "formatter": "{c}%" if percent_axis else "{c}", "distance": 8},
            "labelLayout": {"hideOverlap": True},
            "data": [{"value": value, "label": {"formatter": _compact_chart_value(value, percent_axis)}} for value in values],
        })
    options = {
        "color": ["#2563EB", "#F97316", "#14B8A6", "#8B5CF6"],
        "title": {"text": title, "left": 8, "textStyle": {"fontSize": 16, "fontWeight": 600}},
        "tooltip": {"trigger": "axis", "backgroundColor": "rgba(17,24,39,.92)", "textStyle": {"color": "#fff"}},
        "legend": {"top": 6, "right": 10, "icon": "roundRect"},
        "grid": {"left": 18, "right": 24, "top": 58, "bottom": 24, "containLabel": True},
        "xAxis": {"type": "category", "boundaryGap": False, "data": categories,
                  "axisLine": {"lineStyle": {"color": "#D0D5DD"}}, "axisLabel": {"color": "#667085"}},
        "yAxis": {"type": "value", "name": _metric_label(y_columns[0], metric_semantics),
                  "axisLabel": {"formatter": "{value}%" if percent_axis else f"{{value}}{metric_unit}", "color": "#667085"},
                  "splitLine": {"lineStyle": {"color": "#EAECF0", "type": "dashed"}}},
        "series": series, "animationDuration": 650,
    }
    st_echarts(options=options, height="330px", key=chart_key)


def _render_dual_axis_line_chart(df, x_column, value_columns, rate_columns, title, metric_semantics, chart_key):
    """渲染双 Y 轴折线图，将不同量纲的指标绑定到左右坐标轴。"""
    categories = [str(value) for value in df[x_column].tolist()]
    series = []
    show_labels = len(categories) <= 18
    value_axis_name = " / ".join(_metric_label(column, metric_semantics) for column in value_columns) or "指标"
    rate_axis_name = " / ".join(_metric_label(column, metric_semantics) for column in rate_columns) or "比例"
    for column in value_columns:
        values = _echarts_values(df[column])
        series.append({"name": _metric_label(column, metric_semantics), "type": "line", "yAxisIndex": 0, "smooth": True, "symbol": "circle",
                       "symbolSize": 7, "lineStyle": {"width": 3}, "areaStyle": {"opacity": 0.04},
                       "label": {"show": show_labels, "position": "top", "color": "#344054", "formatter": "{c}"},
                       "data": [{"value": value, "label": {"formatter": _compact_chart_value(value)}} for value in values]})
    for column in rate_columns:
        raw_values = _echarts_values(df[column])
        semantics = metric_semantics.get(column) or {}
        values = _normalize_rate_values(raw_values, semantics.get("value_scale"))
        series.append({"name": _metric_label(column, metric_semantics), "type": "line", "yAxisIndex": 1, "smooth": True, "symbol": "circle",
                       "symbolSize": 7, "lineStyle": {"width": 3, "type": "dashed"},
                       "label": {"show": show_labels, "position": "bottom", "color": "#344054", "formatter": "{c}%"},
                       "data": [{"value": value, "label": {"formatter": _compact_chart_value(value, percent=True)}} for value in values]})
    options = {
        "color": ["#2563EB", "#F97316", "#14B8A6", "#8B5CF6"],
        "title": {"text": title, "left": 8, "textStyle": {"fontSize": 16, "fontWeight": 600}},
        "tooltip": {"trigger": "axis", "backgroundColor": "rgba(17,24,39,.92)", "textStyle": {"color": "#fff"}},
        "legend": {"top": 6, "right": 10, "icon": "roundRect"},
        "grid": {"left": 58, "right": 58, "top": 58, "bottom": 28, "containLabel": True},
        "xAxis": {"type": "category", "boundaryGap": False, "data": categories,
                  "axisLine": {"lineStyle": {"color": "#D0D5DD"}}, "axisLabel": {"color": "#667085"}},
        "yAxis": [{"type": "value", "name": value_axis_name, "nameTextStyle": {"color": "#667085"},
                   "axisLabel": {"color": "#667085"}, "splitLine": {"lineStyle": {"color": "#EAECF0", "type": "dashed"}}},
                  {"type": "value", "name": rate_axis_name, "axisLabel": {"formatter": "{value}%", "color": "#667085"},
                   "splitLine": {"show": False}}],
        "series": series, "animationDuration": 650,
    }
    st_echarts(options=options, height="430px", key=chart_key)


def _render_dual_axis_bar_chart(
    df,
    x_column,
    value_columns,
    rate_columns,
    title,
    metric_semantics,
    chart_key,
):
    """渲染双轴柱线组合图：绝对值用柱状图，比例类指标用右轴折线图。"""
    categories = [str(value) for value in df[x_column].tolist()]
    series = []

    for column in value_columns:
        series.append({
            "name": _metric_label(column, metric_semantics),
            "type": "bar",
            "yAxisIndex": 0,
            "barMaxWidth": 34,
            "itemStyle": {"borderRadius": [6, 6, 0, 0]},
            "label": {"show": len(categories) <= 12, "position": "top", "color": "#344054", "formatter": "{c}"},
            "data": _echarts_values(df[column]),
        })

    for column in rate_columns:
        semantics = metric_semantics.get(column) or {}
        values = _normalize_rate_values(_echarts_values(df[column]), semantics.get("value_scale"))
        series.append({
            "name": _metric_label(column, metric_semantics),
            "type": "line",
            "yAxisIndex": 1,
            "smooth": True,
            "symbol": "circle",
            "symbolSize": 8,
            "lineStyle": {"width": 3, "type": "dashed"},
            "label": {"show": len(categories) <= 12, "position": "top", "color": "#344054", "formatter": "{c}%"},
            "data": [{"value": value, "label": {"formatter": _compact_chart_value(value, percent=True)}} for value in values],
        })

    value_axis_name = " / ".join(_metric_label(column, metric_semantics) for column in value_columns) or "指标"
    rate_axis_name = " / ".join(_metric_label(column, metric_semantics) for column in rate_columns) or "比例"
    options = {
        "color": ["#2563EB", "#14B8A6", "#F97316", "#8B5CF6"],
        "title": {"text": title, "left": 8, "textStyle": {"fontSize": 16, "fontWeight": 600}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"},
                    "backgroundColor": "rgba(17,24,39,.92)", "textStyle": {"color": "#fff"}},
        "legend": {"top": 6, "right": 10, "icon": "roundRect"},
        "grid": {"left": 58, "right": 58, "top": 58, "bottom": 32, "containLabel": True},
        "xAxis": {"type": "category", "data": categories,
                  "axisLine": {"lineStyle": {"color": "#D0D5DD"}}, "axisLabel": {"color": "#667085"}},
        "yAxis": [
            {"type": "value", "name": value_axis_name, "axisLabel": {"color": "#667085"},
             "splitLine": {"lineStyle": {"color": "#EAECF0", "type": "dashed"}}},
            {"type": "value", "name": rate_axis_name, "axisLabel": {"formatter": "{value}%", "color": "#667085"},
             "splitLine": {"show": False}},
        ],
        "series": series,
    }
    st_echarts(options=options, height="430px", key=chart_key)


def render_chart(chart_config: dict, chart_key: str = "chart") -> None:
    """渲染统一 chart_config。

    当前支持 line、bar、pie；table 由上层数据表组件负责展示。
    新增图表类型时，通常在这里增加分支和对应的 ECharts options，
    同时再到后端 chart_decision/chart_generate 中增加类型约束和决策逻辑。
    """
    if not chart_config or chart_config.get("chart_type", "table") == "table":
        return
    chart_type = chart_config.get("chart_type") or "table"
    rows = chart_config.get("data") or []
    if not rows:
        return
    df = _prepare_chart_df(rows)
    title = chart_config.get("title") or ""
    x_column = chart_config.get("x_column")
    y_columns = [column for column in (chart_config.get("y_columns") or []) if column in df.columns]
    metric_semantics = chart_config.get("metric_semantics") or {}
    if not x_column or x_column not in df.columns or not y_columns:
        return

    # 折线图优先处理多指标双轴场景，避免比例类指标被绝对值指标压扁。
    if chart_type == "line" and len(y_columns) > 1:
        rate_columns = [column for column in y_columns if _is_percentage_metric(column, metric_semantics)]
        value_columns = [column for column in y_columns if column not in rate_columns]
        if rate_columns and value_columns:
            _render_dual_axis_line_chart(
                df,
                x_column,
                value_columns,
                rate_columns,
                title or "指标趋势",
                metric_semantics,
                f"{chart_key}_dual_axis",
            )
            return
    # 单量纲趋势，或包含 series_dimension 的多系列趋势。
    if chart_type == "line":
        _render_line_panel(df, x_column, y_columns, title or "指标趋势",
                           percent_axis=len(y_columns) == 1 and _is_percentage_metric(y_columns[0], metric_semantics),
                           series_dimension=chart_config.get("series_dimension"),
                           metric_semantics=metric_semantics,
                           chart_key=f"{chart_key}_line")
        return

    # 柱状图的多量纲场景使用柱线组合图，避免比例指标与绝对值共用同一刻度。
    if chart_type == "bar" and len(y_columns) > 1:
        rate_columns = [column for column in y_columns if _is_percentage_metric(column, metric_semantics)]
        value_columns = [column for column in y_columns if column not in rate_columns]
        if rate_columns and value_columns:
            _render_dual_axis_bar_chart(
                df,
                x_column,
                value_columns,
                rate_columns,
                title or "指标对比",
                metric_semantics,
                f"{chart_key}_dual_axis_bar",
            )
            return

    presentation = _chart_presentation(chart_config, df, x_column)
    y_field = y_columns[0]
    # 柱状图默认按指标降序并限制 Top 12，避免分类过多导致页面不可读。
    if chart_type == "bar":
        limit = presentation.get("limit")
        if isinstance(limit, int) and limit > 0 and len(df) > limit:
            df = df.nlargest(limit, y_field)
        elif presentation.get("sort") == "desc":
            df = df.sort_values(y_field, ascending=False)
        elif presentation.get("sort") == "asc":
            df = df.sort_values(y_field, ascending=True)
    # 饼图同样按指标降序，保证扇区和图例从大到小排列。
    if chart_type == "pie":
        if presentation.get("sort") == "desc":
            df = df.sort_values(y_field, ascending=False, kind="stable")
        elif presentation.get("sort") == "asc":
            df = df.sort_values(y_field, ascending=True, kind="stable")
        values = _echarts_values(df[y_field])
        options = {"color": ["#2563EB", "#14B8A6", "#F59E0B", "#8B5CF6", "#EC4899", "#06B6D4"],
                   "title": {"text": title, "left": 8}, "tooltip": {"trigger": "item", "formatter": "{b}<br/>{c} ({d}%)"},
                   "legend": {"type": "scroll", "orient": "vertical", "right": 10, "top": "middle"},
                   "series": [{"name": y_field, "type": "pie", "radius": ["45%", "70%"], "center": ["42%", "52%"],
                               "padAngle": 2, "itemStyle": {"borderRadius": 6, "borderColor": "#fff", "borderWidth": 2},
                               "label": {"formatter": "{b}\n{d}%"},
                               "data": [{"name": str(name), "value": value} for name, value in zip(df[x_column].tolist(), values)]}],}
        st_echarts(options=options, height="430px", key=f"{chart_key}_pie")
        return

    if len(y_columns) == 1 and chart_type == "bar" and presentation.get("orientation", "vertical") == "horizontal":
        options = {"color": ["#2563EB"], "title": {"text": title, "left": 8},
                   "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
                   "grid": {"left": 18, "right": 28, "top": 45, "bottom": 20, "containLabel": True},
                   "xAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#EAECF0", "type": "dashed"}}},
                   "yAxis": {"type": "category", "data": [str(value) for value in df[x_column].tolist()], "inverse": True,
                             "axisLabel": {"width": 180, "overflow": "truncate", "color": "#475467"}},
                   "series": [{"name": y_field, "type": "bar", "barMaxWidth": 22, "itemStyle": {"borderRadius": [0, 6, 6, 0]},
                               "label": {"show": True, "position": "right", "color": "#344054", "formatter": "{c}"},
                               "data": _echarts_values(df[y_field])}]}
        st_echarts(options=options, height=f"{max(300, min(650, 42 * len(df) + 80))}px", key=f"{chart_key}_hbar")
        return

    categories = [str(value) for value in df[x_column].tolist()]
    series = [{"name": column, "type": "bar", "barMaxWidth": 36, "itemStyle": {"borderRadius": [6, 6, 0, 0]},
               "label": {"show": True, "position": "top", "color": "#344054", "formatter": "{c}"},
               "data": _echarts_values(df[column])} for column in y_columns]
    options = {"color": ["#2563EB", "#14B8A6", "#F59E0B", "#8B5CF6"], "title": {"text": title, "left": 8},
               "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}}, "legend": {"top": 5, "right": 10},
               "grid": {"left": 18, "right": 24, "top": 50, "bottom": 30, "containLabel": True},
               "xAxis": {"type": "category", "data": categories, "axisLabel": {"rotate": 0 if len(categories) <= 8 else 30, "color": "#667085"}},
               "yAxis": {"type": "value", "splitLine": {"lineStyle": {"color": "#EAECF0", "type": "dashed"}}}, "series": series}
    st_echarts(options=options, height="400px", key=f"{chart_key}_bar")
