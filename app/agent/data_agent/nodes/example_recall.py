"""
================================================================================
模块名称：Few-Shot 金标 SQL 向量多路召回节点 🔍💾
文件路径：app/data_agent/nodes/example_recall.py
核心职责：
    1. 前端流式通信：向 runtime.stream_writer 推送阶段事件，点亮 UI 步骤
    2. 采用 get_default_components 懒加载单例仓储与向量计算客户端
    3. 基于短关键词与整句提问进行多轮稠密向量近邻检索
    4. 基于签名（Question + SQL）执行内存级防重复过滤
    5. 返回增量契约字典 {"retrieved_examples": [...] }，无缝注入全局 State
================================================================================
"""
from typing import Any, List, Dict
from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.models.qdrant.sql_example_info_qdrant import SQLExampleQdrant
from app.repositories.qdrant.sql_example_repository import SQLExampleQdrantRepository
from app.core.logging import logger
from app.agent.data_agent.progress import emit_node_result

_DEFAULT_EXAMPLE_REPO = None
_EMBEDDING_CLIENT = None


def get_default_components():
    """安全懒加载初始化底层仓储与向量模型客户端，彻底规避 NoneType 与跨事件循环问题"""
    global _DEFAULT_EXAMPLE_REPO, _EMBEDDING_CLIENT
    if _DEFAULT_EXAMPLE_REPO is None:
        if not getattr(embedding_client_manager, "client", None):
            embedding_client_manager.init()
        _EMBEDDING_CLIENT = embedding_client_manager.client

        if not getattr(qdrant_client_manager, "client", None):
            qdrant_client_manager.init()
        _DEFAULT_EXAMPLE_REPO = SQLExampleQdrantRepository(qdrant_client_manager.client)
    return _DEFAULT_EXAMPLE_REPO, _EMBEDDING_CLIENT


async def example_recall(state: Dict[str, Any], runtime: Any = None) -> Dict[str, Any]:
    """
    Few-Shot 金标 SQL 独立并发召回图节点
    严格对齐 column_recall.py 的实现风格与异常防御契约
    """
    # 🌟 核心：向前端客户端推送当前执行步骤，点亮前端 UI 步骤条
    if runtime and hasattr(runtime, "stream_writer") and runtime.stream_writer:
        try:
            runtime.stream_writer({"stage": "召回金标范例"})
        except Exception as e:
            logger.warning(f"⚠️ [ExampleRecall] 前端推流异常: {e}")

    logger.info("🔍 [ExampleRecall] 开始执行 Few-Shot 金标 SQL 语义向量召回...")
    repo, embedding_client = get_default_components()

    keywords: List[str] = state.get("keywords", [])
    query = state.get("query", "")

    # 1. 构建多路检索词：优先取紧凑短词，并与原句去重合并
    valid_keywords = [kw for kw in keywords if len(kw) < 15]
    search_terms = list(dict.fromkeys(valid_keywords + [query])) if valid_keywords else [query]

    recalled_examples: List[SQLExampleQdrant] = []
    seen_signatures = set()

    # 2. 遍历检索词项，多角度向量近邻搜索
    for term in search_terms:
        if not term or not term.strip():
            continue
        try:
            query_vector = embedding_client.embed_query(term)
            # 门禁阈值设为 0.40，放行语义高度对齐但用词稍有差异的数仓范式
            matched_items = await repo.search(query_vector, score_threshold=0.40, limit=3)

            for item in matched_items:
                # 兼容字典格式与原始 Point 对象
                payload = item if isinstance(item, dict) else getattr(item, "payload", {})
                score = payload.get("score", 0.0) if isinstance(item, dict) else getattr(item, "score", 0.0)

                ex_id = payload.get("id", "")
                question = payload.get("question", "")
                sql = payload.get("sql", "")
                pattern = payload.get("pattern", "通用分析模式")
                tables = payload.get("tables", [])
                dialect = payload.get("dialect", "mysql")
                difficulty = payload.get("difficulty", "中等")

                if question and sql:
                    # 使用 提问与SQL前缀 构建唯一特征签名，防止跨检索词导致相同示例重复入选
                    sig = f"{question}_{sql[:30]}"
                    if sig not in seen_signatures:
                        seen_signatures.add(sig)
                        # 组装符合 SQLExampleQdrant 契约的字典
                        recalled_examples.append({
                            "id": ex_id,
                            "question": question,
                            "sql": sql,
                            "pattern": pattern,
                            "tables": tables,
                            "dialect": dialect,
                            "difficulty": difficulty,
                            "score": round(score, 4)
                        })
        except Exception as e:
            logger.warning(f"⚠️ [ExampleRecall] 检索词项 [{term}] 召回波动: {e}")

    # 3. 按相似度分数从高到低排序，截取 Top-2 最优质范例，杜绝大模型 Prompt 上下文过载
    recalled_examples.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    final_examples = recalled_examples[:2]

    logger.info(f"✨ [ExampleRecall] 召回成功，共锁定 {len(final_examples)} 个高分金标示例")
    emit_node_result(runtime, f"金标 SQL 示例：召回 {len(final_examples)} 个")

    # 4. 精确返回与 DataAgentState 契约完全绑定的字段键名
    return {"retrieved_examples": final_examples}
