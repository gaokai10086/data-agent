"""
================================================================================
模块名称：Data Agent 核心节点逐步孤立排查测试脚本 (完美适配 Runtime 版)
文件路径：app/tests/test_nodes_step_by_step.py
================================================================================
"""
import asyncio
from app.agent.nodes.intent_detection import intent_detection
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.column_recall import column_recall
from app.agent.nodes.metric_recall import metric_recall
from app.agent.nodes.value_recall import value_recall
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info


class MockStreamWriter:
    def __call__(self, event):
        print(f"   [StreamWriter 进度]: {event}")


class MockRuntime:
    stream_writer = MockStreamWriter()
    context = {}


async def run_debug():
    print("=" * 60)
    print("🔎 开始逐个节点孤立排查...")
    print("=" * 60)

    # 构造 Mock 运行时环境（如果 merge 需要 meta_mysql_repository，可以在这里尝试安全加载）
    runtime = MockRuntime()
    try:
        from app.core.container import container  # 根据你项目的容器路径调整
        # runtime.context['meta_mysql_repository'] = container.meta_mysql_repository
    except Exception:
        print("ℹ️ [提示]: 未自动加载到容器仓储，如需测试 MySQL 聚合，请检查容器导入路径。")

    # 初始状态
    state = {
        "query": "各个商品品牌的销售总额是多少？按金额从高到低排序",
        "session_id": "debug_session_001"
    }

    # 1. 测试意图识别
    print("\n---------------- [1/5] 测试 intent_detection ----------------")
    intent_res = await intent_detection(state, runtime)
    state.update(intent_res)
    print(f"👉 意图识别输出: {intent_res}")

    # 2. 测试关键词提取
    print("\n---------------- [2/5] 测试 extract_keywords ----------------")
    kw_res = await extract_keywords(state, runtime)
    state.update(kw_res)
    print(f"👉 关键词提取输出: {kw_res}")

    # 3. 测试字段召回 (重点检查这里！)
    print("\n---------------- [3/5] 测试 column_recall ----------------")
    col_res = await column_recall(state, runtime)
    state.update(col_res)
    print(f"👉 字段召回输出 (retrieved_columns):")
    retrieved_cols = state.get("retrieved_columns", [])
    print(f"   总共召回列数: {len(retrieved_cols)}")
    for col in retrieved_cols:
        print(f"   - 表名: {col.get('table_name')} | 字段: {col.get('column_name')} | 描述: {col.get('description')} | 得分: {col.get('score')}")

    # 4. 测试指标与枚举值召回
    print("\n---------------- [4/5] 测试 metric & value recall ----------------")
    metric_res = await metric_recall(state, runtime)
    state.update(metric_res)
    value_res = await value_recall(state, runtime)
    state.update(value_res)
    print(f"👉 指标召回数: {len(state.get('retrieved_metrics', []))}, 枚举值召回数: {len(state.get('retrieved_values', []))}")

    # 5. 测试聚合节点 (merge_retrieved_info)
    print("\n---------------- [5/5] 测试 merge_retrieved_info ----------------")
    try:
        merge_res = await merge_retrieved_info(state, runtime)
        state.update(merge_res)
        print(f"👉 聚合成功！生成的表结构数量: {len(state.get('table_infos', []))}")
        for t in state.get('table_infos', []):
            print(f"   - 表名: {t.name} (角色: {t.role}) | 包含字段数: {len(t.columns)}")
    except Exception as e:
        print(f"❌ 聚合节点执行异常 (可能是缺少 MySQL 仓库依赖): {e}")

    print("\n🎉 逐步排查执行完毕！")


if __name__ == "__main__":
    asyncio.run(run_debug())