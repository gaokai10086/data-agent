"""结果格式化服务：把 SQL 原始结果加工成前端可展示数据。

这里集中处理：
    1. 中文字段映射
    2. 衍生列中文名推断
    3. 数值格式化和表头中文化
    4. 图表配置本地化

present_result 只负责调用本服务并输出 bi_report。
"""
import json
from typing import Any, Dict, List

from langchain_core.prompts import ChatPromptTemplate

from app.core.llm import get_llm
from app.core.logging import logger


class ResultFormatService:
    """结果格式化服务。"""

    @staticmethod
    def build_column_mapping(table_infos: List[Dict[str, Any]]) -> Dict[str, str]:
        """从已筛选的 table_infos 构建英文字段名到中文展示名的映射。"""
        dynamic_mapping: Dict[str, str] = {}
        for table in table_infos:
            for column in table.get("columns", []):
                name = column.get("name")
                aliases = column.get("alias", [])
                short_name = aliases[0] if aliases and isinstance(aliases, list) else None

                if not short_name:
                    description = column.get("description") or column.get("comment") or ""
                    short_name = (
                        description.split("，")[0].split("。")[0].strip()
                        if description
                        else name
                    )

                if name and short_name:
                    dynamic_mapping[name] = short_name
        return dynamic_mapping

    @staticmethod
    async def infer_column_names_by_llm(
        query: str,
        missing_columns: List[str],
        *,
        provider: str = "deepseek",
        model_tier: str = "deepseek-v4-flash",
    ) -> Dict[str, str]:
        """对 SQL 临时聚合别名调用 LLM 推断中文表头。"""
        llm = get_llm(provider=provider, model_tier=model_tier, temperature=0.0)
        prompt = ChatPromptTemplate.from_template(
            "你是一个专业的数据仓库工程师。请根据用户的提问语境，将 SQL 结果集中的英文字段/别名转换为简洁规范的中文业务表头名。\n\n"
            "【用户问题】：{query}\n"
            "【待命名的英文字段列表】：{columns}\n\n"
            "【约束规范】：\n"
            "1. 仅输出一个标准的合法 JSON 字典，键为原英文字段名，值为推断的中文业务表头。\n"
            "2. 表头必须语义专业（如 product_count 翻译为 '商品数量'，total_amount 翻译为 '销售总金额'）。\n"
            "3. 严禁包含 Markdown 格式标记（如 ```json 等），禁止输出任何多余的解释文本。"
        )

        try:
            response = await llm.ainvoke(
                prompt.format_messages(query=query, columns=str(missing_columns))
            )
            raw_text = response.content.strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.strip("`").replace("json", "").strip()
            return json.loads(raw_text)
        except Exception as exc:
            logger.warning(f"⚠️ [ResultFormatService] LLM 智能表头推断降级: {exc}")
            return {column: column for column in missing_columns}

    @staticmethod
    def format_rows(
        sql_result: List[Dict[str, Any]],
        column_mapping: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """把原始 SQL 结果转成中文表头、数值类型友好的展示数据。"""
        formatted_rows: List[Dict[str, Any]] = []
        for row in sql_result:
            chinese_row: Dict[str, Any] = {}
            for key, value in row.items():
                column_name = column_mapping.get(key, key)
                if hasattr(value, "as_integer_ratio") or isinstance(value, (int, float)):
                    num_value = float(value)
                    if num_value.is_integer():
                        chinese_row[column_name] = int(num_value)
                    else:
                        chinese_row[column_name] = round(num_value, 2)
                else:
                    chinese_row[column_name] = value
            formatted_rows.append(chinese_row)
        return formatted_rows

    @staticmethod
    def localize_chart_config(
        chart_config: dict,
        column_mapping: Dict[str, str],
        formatted_rows: List[Dict[str, Any]],
    ) -> dict:
        """把图表配置本地化：数据与坐标轴字段改成中文，并去掉冗余标题。"""
        chart_type = chart_config.get("chart_type")
        if not chart_type or chart_type == "table":
            return chart_config

        localized = dict(chart_config)
        localized["data"] = formatted_rows
        localized["title"] = ""

        x_column = chart_config.get("x_column")
        if x_column:
            localized["x_column"] = column_mapping.get(x_column, x_column)
        series_dimension = chart_config.get("series_dimension")
        if series_dimension:
            localized["series_dimension"] = column_mapping.get(series_dimension, series_dimension)
        localized["y_columns"] = [
            column_mapping.get(column, column)
            for column in (chart_config.get("y_columns") or [])
        ]
        localized["metric_semantics"] = {
            column_mapping.get(column, column): semantics
            for column, semantics in (chart_config.get("metric_semantics") or {}).items()
        }
        return localized

    async def format_report_data(
        self,
        query: str,
        table_infos: List[Dict[str, Any]],
        sql_result: List[Dict[str, Any]],
        chart_config: dict,
        *,
        provider: str = "deepseek",
        model_tier: str = "deepseek-v4-flash",
    ) -> Dict[str, Any]:
        """一次完成字段映射、格式化、图表本地化。"""
        column_mapping = self.build_column_mapping(table_infos)

        if sql_result and isinstance(sql_result, list):
            sample_keys = list(sql_result[0].keys())
            missing_keys = [key for key in sample_keys if key not in column_mapping]
            if missing_keys:
                inferred_mapping = await self.infer_column_names_by_llm(
                    query,
                    missing_keys,
                    provider=provider,
                    model_tier=model_tier,
                )
                column_mapping.update(inferred_mapping)

        formatted_rows = self.format_rows(sql_result, column_mapping)
        localized_chart_config = self.localize_chart_config(
            chart_config, column_mapping, formatted_rows
        )
        return {
            "formatted_data": formatted_rows,
            "column_mapping": column_mapping,
            "chart_config": localized_chart_config,
        }
