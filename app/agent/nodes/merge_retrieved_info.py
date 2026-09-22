import yaml
from langgraph.runtime import Runtime

"""
合并召回信息节点的主函数
功能：将三路召回结果整合成完整的表结构信息
"""
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, TableInfoState, ColumnInfoState, MetricInfoState
from app.core.logging import logger
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository


async def merge_retrieved_info(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    #   向用户展示当前阶段
    writer({"stage": "合并召回信息"})
    #  获取三路召回结果 - 防御性获取
    retrieved_columns = state.get("retrieved_columns", [])
    retrieved_values = state.get("retrieved_values", [])
    retrieved_metrics = state.get("retrieved_metrics", [])

    #  从运行时上下文获取元数据仓库 - 防御性获取
    meta_mysql_repository = None
    if runtime is not None:
        context = getattr(runtime, "context", None)
        if isinstance(context, dict):
            meta_mysql_repository = context.get("meta_mysql_repository")

    #  如果没有提供 repository，尝试自愈初始化
    if meta_mysql_repository is None:
        from app.clients.mysql_client import meta_client_manager
        from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository

        meta_client_manager.init()
        meta_session = meta_client_manager.session_factory()
        meta_mysql_repository = MetaMySQLRepository(meta_session)
        logger.warning("⚠️ [自愈] 未检测到 meta_mysql_repository，已启用自愈初始化")

    #  初始化结果容器
    table_infos: list[TableInfoState] = []
    metric_infos: list[MetricInfoState] = []

    """"""""""""""""""""" 1.值信息合并到字段"""""""""""""""""""""""
    #  构建字段 ID 到字段信息的映射
    id_to_column_map: dict[str, ColumnInfoQdrant] = {value.get('id'): value for value in retrieved_columns if
                                                     value.get('id')}

    #  遍历每个召回的值
    for retrieved_value in retrieved_values:
        column_id = retrieved_value.get('column_id')  # 获取所属字段ID - 防御性获取
        value = retrieved_value.get('value')  # 获取值 - 防御性获取

        if not column_id or value is None:
            continue

        #  如果字段已存在，添加实例值
        if column_id in id_to_column_map:
            if 'examples' not in id_to_column_map[column_id]:
                id_to_column_map[column_id]['examples'] = []
            if value not in id_to_column_map[column_id]['examples']:
                id_to_column_map[column_id]['examples'].append(value)
        #  如果不存在，从 MySQL 加载并添加实例值
        else:
            try:
                column_info: ColumnInfoMySQL = await meta_mysql_repository.get_column_by_id(column_id)
                if value not in column_info.examples:
                    column_info.examples.append(value)
                id_to_column_map[column_id] = _convert_column_info_from_mysql_to_qdrant(column_info)
            except Exception as e:
                logger.warning(f"⚠️ [MergeInfo] 加载字段 [{column_id}] 失败: {e}")

    """
    场景1：字段已召回
    用户问题："查询张三的订单"
    ├── 字段召回：[user_id, order_id, ...]
    ├── 值召回：[{column_id: "user_id", value: "张三"}]
    └── 合并后：user_id 的 examples = ["张三"]

    场景2：字段未召回
    用户问题："查询订单状态为已支付的订单"
    ├── 字段召回：[order_id, ...]（未包含 order_status）
    ├── 值召回：[{column_id: "order_status", value: "已支付"}]
    └── 合并后：
        ├── 从 MySQL 加载 order_status 字段
        └── order_status 的 examples = ["已支付"]
    """
    """"""""""""""""""""" 1.值信息合并到字段 end"""""""""""""""""""""""

    """"""""""""""""""""" 2.补充字段相关字段"""""""""""""""""""""""
    #  确保计算所需的所有字段都已加载
    # 指标引用的字段会在合并完成后统一通过 _supplement_table_infos 从 Meta 补齐。

    """
    为什么要补充？
    用户问题："查询张三的GMV"
    指标召回：
      GMV = {
        "name": "GMV",
        "formula": "SUM(order_amount)",
        "relevant_columns": ["order_amount"]  ← 必须包含这个字段
      }

    字段召回可能遗漏 order_amount：
      retrieved_columns = [user_id, order_time]  ← 遗漏了 order_amount

    补充逻辑：
      检测到 order_amount 不在字段列表中
      → 从 MySQL 加载 order_amount 字段信息
      → 确保后续 SQL 生成包含完整字段
    """
    """"""""""""""""""""" 2.补充字段相关字段 end"""""""""""""""""""""""

    """"""""""""""""""""" 3.将字段按所属表分组"""""""""""""""""""""""
    # 表名是稳定且唯一的业务标识；table_id 的底层类型曾发生过变更，
    # 这里不再依赖 table_id 去查主键，避免旧索引中的字符串 UUID 与当前整数主键不匹配。
    table_to_columns_map: dict[str, list[ColumnInfoQdrant]] = {}
    for column in id_to_column_map.values():
        table_name = column.get('table_name')
        if not table_name:
            continue
        if table_name not in table_to_columns_map:
            table_to_columns_map[table_name] = []
        table_to_columns_map[table_name].append(column)

    """
    用户问题："查询张三的订单和支付信息"
    字段召回：
      [user_id, order_id, pay_amount]

    按表分组：
      table_to_columns_map = {
        "orders": [user_id, order_id],
        "payments": [pay_amount]
      }
    后续处理：
      遍历每个表，构建完整的表结构信息
    """
    """"""""""""""""""""" 3.将字段按所属表分组 end"""""""""""""""""""""""

    """"""""""""""""""""" 4.为每张表构建完整的结果信息 """""""""""""""""""""""
    for table_name, columns in table_to_columns_map.items():
        try:
            #  加载表信息
            table_info: TableInfoMySQL = await meta_mysql_repository.get_table_by_name(table_name)
            if table_info is None:
                raise ValueError(f"Meta 中缺少表 [{table_name}]")

            #  构建字段状态列表
            column_states: list[ColumnInfoState] = []
            for column in columns:
                column_state = _convert_column_info_from_qdrant_to_state(column)
                column_states.append(column_state)

            #  构建表信息状态
            table_info_state = TableInfoState(
                name=table_info.name,
                role=table_info.role,
                description=table_info.description,
                columns=column_states
            )
            table_infos.append(table_info_state)
        except Exception as e:
            logger.warning(f"⚠️ [MergeInfo] 加载表 [{table_name}] 失败: {e}")

    """
    为什么补充主键字段？
    用户问题："查询张三的订单金额"

    字段召回：
      [user_id, order_amount]  ← 缺少主键字段

    补充主键：
      order_id（主键）  ← 自动添加

    为什么需要主键？
      - 确保 SQL 查询的完整性
      - 多表 JOIN 时需要主键关联
      - 分组聚合时需要主键去重
    """
    """"""""""""""""""""" 4.为每张表构建完整的结果信息 """""""""""""""""""""""

    selected_metrics = _select_metrics_by_relevance(retrieved_metrics, state.get("keywords", []))

    for metric in selected_metrics:
        metric_infos.append(
            await _build_metric_info_state(metric, meta_mysql_repository)
        )

    table_infos = await _supplement_table_infos(table_infos, meta_mysql_repository, metric_infos)

    logger.info(f"召回信息合并成功")
    return {"table_infos": table_infos, "metric_infos": metric_infos}


def _select_metrics_by_relevance(retrieved_metrics: list[dict], keywords: list[str]) -> list[dict]:
    """用关键词命中与召回分数确定性筛选指标，不再调用 LLM。"""
    unique_metrics: dict[str, dict] = {}
    for metric in retrieved_metrics:
        name = metric.get('name')
        if not name:
            continue
        current = unique_metrics.get(name)
        if current is None or float(metric.get('score', 0.0)) > float(current.get('score', 0.0)):
            unique_metrics[name] = metric

    if not unique_metrics:
        return []

    ranked: list[tuple[tuple[int, float], dict]] = []
    keyword_hits: list[tuple[float, dict]] = []
    for metric in unique_metrics.values():
        name = str(metric.get('name', ''))
        aliases = [str(alias) for alias in metric.get('alias', []) if alias]
        terms = [name, *aliases]
        keyword_hit = any(
            keyword and any(keyword in term for term in terms)
            for keyword in keywords
        )
        score = float(metric.get('score', 0.0))
        ranked.append(((1 if keyword_hit else 0, score), metric))
        if keyword_hit:
            keyword_hits.append((score, metric))

    ranked.sort(key=lambda item: item[0], reverse=True)
    if keyword_hits:
        keyword_hits.sort(key=lambda item: item[0], reverse=True)
        return [metric for _, metric in keyword_hits[:3]]
    return [metric for _, metric in ranked[:1]]


async def _build_metric_info_state(metric: dict, meta_repository: MetaMySQLRepository) -> MetricInfoState:
    """优先从 Meta 取完整指标定义，向量召回 payload 只作为兜底。"""
    name = str(metric.get('name', ''))
    metric_record = await meta_repository.get_metric_by_name(name)
    if metric_record is None and metric.get('metric_key'):
        metric_record = await meta_repository.get_metric_by_key(metric.get('metric_key'))

    if metric_record is not None:
        return MetricInfoState(
            name=metric_record.name,
            description=metric_record.description or '',
            alias=list(metric_record.alias or []),
            metric_key=metric_record.metric_key or '',
            formula=metric_record.formula or '',
            fact_table=metric_record.fact_table or '',
            measure_column=metric_record.measure_column or '',
            aggregation=metric_record.aggregation or '',
            time_column=metric_record.time_column or '',
            filters=list(metric_record.filters or []),
            dimensions=list(metric_record.dimensions or []),
        )
    return MetricInfoState(
        name=name,
        description=str(metric.get('description', '')),
        alias=list(metric.get('alias', []) or []),
    )


async def _supplement_table_infos(
    table_infos: list[dict],
    meta_repository: MetaMySQLRepository,
    metric_infos: list[dict],
) -> list[dict]:
    """补齐候选表中的主外键与指标直接引用字段，避免后续压缩丢失关键列。"""
    required_by_table: dict[str, set[str]] = {}
    for metric in metric_infos:
        fact_table = str(metric.get("fact_table") or "").strip()
        for column in (
            metric.get("measure_column"),
            metric.get("time_column"),
        ):
            if fact_table and column:
                required_by_table.setdefault(fact_table, set()).add(str(column))
        for item in metric.get("filters") or []:
            if isinstance(item, dict) and item.get("column"):
                reference = str(item["column"])
                table_name, column_name = (
                    reference.split(".", 1) if "." in reference else (fact_table, reference)
                )
                required_by_table.setdefault(table_name.strip(), set()).add(column_name.strip())
        for item in metric.get("dimensions") or []:
            if isinstance(item, dict) and item.get("column"):
                reference = str(item["column"])
                if "." in reference:
                    table_name, column_name = reference.split(".", 1)
                    required_by_table.setdefault(table_name.strip(), set()).add(column_name.strip())

    supplemented: list[dict] = []
    for table in table_infos:
        table_name = str(table.get("name") or "")
        existing_names = {str(column.get("name")) for column in table.get("columns", [])}
        columns = list(table.get("columns", []))
        meta_table = await meta_repository.get_table_by_name(table_name)
        if meta_table is not None:
            required = required_by_table.get(table_name, set())
            for column in await meta_repository.list_columns_by_table_id(meta_table.id):
                if column.name in existing_names:
                    continue
                if column.name in required or column.role in {"primary_key", "foreign_key"}:
                    columns.append(
                        {
                            "name": column.name,
                            "type": column.type or "",
                            "role": column.role or "dimension",
                            "description": column.description or "",
                            "alias": list(column.alias or []),
                            "examples": list(column.examples or []),
                        }
                    )
        new_table = dict(table)
        new_table["columns"] = columns
        supplemented.append(new_table)
    return supplemented


def _convert_column_info_from_mysql_to_qdrant(column_info: ColumnInfoMySQL) -> ColumnInfoQdrant:
    return ColumnInfoQdrant(
        id=column_info.id,
        name=column_info.name,
        type=column_info.type,
        role=column_info.role,
        examples=column_info.examples,
        description=column_info.description,
        alias=column_info.alias,
        table_id=column_info.table_id
    )


def _convert_column_info_from_qdrant_to_state(column_info: dict) -> ColumnInfoState:
    # ======== 核心修复：全面使用 .get() 防御硬索引 KeyError ========
    return ColumnInfoState(
        name=column_info.get('name', ''),
        type=column_info.get('type', ''),
        role=column_info.get('role', 'dimension'),
        description=column_info.get('description', ''),
        alias=column_info.get('alias', []),
        examples=column_info.get('examples', [])
    )


def _convert_column_info_from_mysql_to_state(column_info: ColumnInfoMySQL) -> ColumnInfoState:
    return ColumnInfoState(
        name=column_info.name,
        type=column_info.type,
        role=column_info.role,
        description=column_info.description,
        alias=column_info.alias,
        examples=column_info.examples
    )
