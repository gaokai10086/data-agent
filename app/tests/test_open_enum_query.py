"""开放枚举路径测试。

验证目标：
    字段被标记为开放枚举（不维护枚举值、不进 ES 索引）时，
    大模型能否仅凭字段语义，自己推导出正确的过滤条件。

对照设计：
    前两题走开放枚举（city / province），值索引里没有，必须靠模型推导。
    第三题走封闭枚举（brand），值索引里有，作为对照组。

运行：
    python -m app.tests.test_open_enum_query
"""
import asyncio

from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.evaluation.runner import build_chat_service
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository

CASES = [
    {
        "question": "杭州市的销售额是多少？",
        "value_type": "open",
        "table": "dim_store",
        "column": "city",
        "dw_value": "杭州",
        "note": "DW 里存的是「杭州」而不是「杭州市」，模型写错就会返回空",
    },
    {
        "question": "江苏省的订单数量是多少？",
        "value_type": "open",
        "table": "dim_user",
        "column": "province",
        "dw_value": "江苏",
        "note": "省份字段，值索引里没有",
    },
    {
        "question": "华为的销售额是多少？",
        "value_type": "closed",
        "table": "dim_product",
        "column": "brand",
        "dw_value": "华为",
        "note": "对照组：品牌是封闭枚举，值索引里已有「华为」",
    },
]


async def main() -> int:
    meta_client_manager.init()
    dw_client_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    embedding_client_manager.init()

    failures: list[str] = []
    try:
        print("=" * 74)
        print("开放枚举路径测试")
        print("=" * 74)

        async with meta_client_manager.session_factory() as meta_session:
            repo = MetaMySQLRepository(meta_session)
            print("\n[字段取值类型确认]")
            for case in CASES:
                table = await repo.get_table_by_name(case["table"])
                column = await repo.get_column(table.id, case["column"])
                actual_type = column.value_type or "open"
                ok = actual_type == case["value_type"]
                print(
                    f"  {case['table']}.{case['column']:<12} "
                    f"value_type={actual_type:<7} 期望={case['value_type']:<7} "
                    f"{'✅' if ok else '❌'}"
                )
                if not ok:
                    failures.append(f"{case['table']}.{case['column']} 取值类型不符")

        for case in CASES:
            print("\n" + "-" * 74)
            print(f"问题：{case['question']}")
            print(f"路径：{case['value_type']}（{case['note']}）")
            async with (
                meta_client_manager.session_factory() as meta_session,
                dw_client_manager.session_factory() as dw_session,
            ):
                chat_service = build_chat_service(meta_session, dw_session)
                final = await chat_service.run_once(case["question"])

            sql = (final.get("sql") or "").strip()
            rows = final.get("sql_result") or []
            error = final.get("error") or ""

            print("\n生成的 SQL：")
            print("  " + (sql.replace("\n", "\n  ") if sql else "(无)"))
            print(f"\n返回行数：{len(rows)}")
            print(f"结果：{rows[:3]}")
            if error:
                print(f"校验错误：{error}")

            value_in_sql = case["dw_value"] in sql
            print(
                f"\n是否使用了 DW 真实值「{case['dw_value']}」："
                f"{'✅ 是' if value_in_sql else '❌ 否（模型可能写了别的写法）'}"
            )
            if not value_in_sql:
                failures.append(f"{case['question']} 未使用 DW 真实值")

            if not rows and not error:
                print(
                    "⚠️ 返回空结果：SQL 能执行但没数据。"
                    "如果模型写成了「杭州市」而 DW 存的是「杭州」，就会出现这种情况。"
                )
    finally:
        await meta_client_manager.close()
        await dw_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()

    print("\n" + "=" * 74)
    if failures:
        print("❌ 存在问题：")
        for item in failures:
            print("   -", item)
        return 1
    print("✅ 开放枚举与封闭枚举路径均按预期工作")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
