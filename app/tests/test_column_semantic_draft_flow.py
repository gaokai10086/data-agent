"""字段语义治理闭环自检（离线）。

不调用真实大模型，用替身模型验证数据库链路：
    生成候选 -> 审核通过发布 / 审核驳回 -> 重复运行不重复生成 -> 还原现场

运行方式：
    python -m app.tests.test_column_semantic_draft_flow
"""
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import text

from app.clients.mysql_client import meta_client_manager
from app.config.app_config import app_config
from app.core.datasource.mysql_connector import MySQLConnector
from app.core.meta.value_sampling_policy import decide
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.column_semantic_draft_service import ColumnSemanticDraftService, PROMPT_VERSION

ENUM_TARGET = "dim_store.region"
IDENTIFIER_TARGET = "fact_order.order_sn"
REVIEWER = "self-check"
NEWLINE = chr(10)


class StubLLM:
    """替身模型：按提示词中的字段名返回代码块包裹的候选 JSON。"""

    model_name = "stub-llm"

    async def ainvoke(self, prompt: str) -> Any:
        column_name = "unknown"
        for line in prompt.splitlines():
            if line.startswith("物理字段名："):
                column_name = line.split("：", 1)[1].strip()
                break
        payload = {
            "description": f"替身模型为 {column_name} 生成的业务说明",
            "alias": [f"{column_name}别名一", f"{column_name}别名二"],
            "reasoning": "仅用于离线链路自检",
        }
        content = "```json" + NEWLINE + json.dumps(payload, ensure_ascii=False) + NEWLINE + "```"
        return type("StubResponse", (), {"content": content})()


class Checker:
    """极简断言收集器，跑完所有用例后统一输出结果。"""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, condition: bool, description: str) -> None:
        print(f"  {'✅' if condition else '❌'} {description}")
        if not condition:
            self.failures.append(description)

    def check_equal(self, actual: Any, expected: Any, description: str) -> None:
        self.check(actual == expected, f"{description}（实际：{actual}）")


async def snapshot_column(session: Any, table_name: str, column_name: str) -> dict[str, Any]:
    result = await session.execute(
        text(
            """
            SELECT c.description, c.alias, c.examples, c.role
            FROM column_info AS c
            JOIN table_info AS t ON t.id = c.table_id
            WHERE t.name = :table_name AND c.name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    row = result.mappings().first()
    if not row:
        return {}
    snapshot = dict(row)
    # 原生 SQL 读取 JSON 列得到的是字符串，这里统一还原为 Python 对象便于比对。
    for field in ("alias", "examples"):
        if isinstance(snapshot.get(field), str):
            snapshot[field] = json.loads(snapshot[field])
    return snapshot


async def restore_column(
    session: Any, table_name: str, column_name: str, snapshot: dict[str, Any]
) -> None:
    """把字段语义恢复到自检前的状态，避免自检污染 Meta。"""
    await session.execute(
        text(
            """
            UPDATE column_info AS c
            JOIN table_info AS t ON t.id = c.table_id
            SET c.description = :description,
                c.alias = :alias,
                c.examples = :examples,
                c.role = :role
            WHERE t.name = :table_name AND c.name = :column_name
            """
        ),
        {
            "description": snapshot.get("description"),
            "alias": json.dumps(snapshot.get("alias") or [], ensure_ascii=False),
            "examples": json.dumps(snapshot.get("examples") or [], ensure_ascii=False),
            "role": snapshot.get("role"),
            "table_name": table_name,
            "column_name": column_name,
        },
    )


async def main() -> int:
    checker = Checker()
    connector = MySQLConnector(
        host=app_config.db_dw.host,
        port=app_config.db_dw.port,
        user=app_config.db_dw.user,
        password=app_config.db_dw.password,
        database=app_config.db_dw.database,
    )
    meta_client_manager.init()
    pre_existing_draft_ids: set[str] = set()
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}

    print("=" * 72)
    print("🚀 字段语义治理闭环自检（离线替身模型）")
    print("=" * 72)

    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")

        async with meta_client_manager.session_factory() as session:
            repository = MetaMySQLRepository(session)
            service = ColumnSemanticDraftService(
                meta_repository=repository, connector=connector, llm=StubLLM()
            )

            for table_name, column_name in (("dim_store", "region"), ("fact_order", "order_sn")):
                snapshots[(table_name, column_name)] = await snapshot_column(
                    session, table_name, column_name
                )
            pre_existing_draft_ids = {draft.id for draft in await service.list_drafts(status=None)}

            print("\n[1] 采样安全策略判定")
            checker.check(decide("dim_store", "region", "VARCHAR").allowed, "region 允许采样")
            checker.check(
                not decide("fact_order", "order_sn", "VARCHAR").allowed, "order_sn 禁止采样"
            )
            checker.check(
                not decide("dim_user", "user_name", "VARCHAR").allowed, "user_name 禁止采样"
            )
            checker.check(
                not decide("fact_order", "pay_amount", "DECIMAL").allowed, "数值型字段禁止采样"
            )
            checker.check(
                not decide("fact_order", "pay_time", "DATETIME").allowed, "时间字段禁止采样"
            )

            print("\n[2] 生成候选草稿")
            report = await service.generate(targets=[ENUM_TARGET, IDENTIFIER_TARGET])
            await session.commit()
            checker.check_equal(
                sorted(report.created),
                sorted([ENUM_TARGET, IDENTIFIER_TARGET]),
                "两个字段各生成一条草稿",
            )
            checker.check_equal(report.failures, [], "生成过程无失败")

            drafts = await service.list_drafts(status="pending")
            draft_by_column = {draft.qualified_name: draft for draft in drafts}
            enum_draft = draft_by_column.get(ENUM_TARGET)
            identifier_draft = draft_by_column.get(IDENTIFIER_TARGET)
            checker.check(enum_draft is not None, "region 草稿已落库为 pending")
            checker.check(identifier_draft is not None, "order_sn 草稿已落库为 pending")
            if enum_draft is None or identifier_draft is None:
                return 1
            checker.check_equal(enum_draft.prompt_version, PROMPT_VERSION, "草稿记录 Prompt 版本")
            checker.check_equal(enum_draft.model_name, "stub-llm", "草稿记录生成模型")
            checker.check(not enum_draft.candidate_alias == [], "候选别名已被解析")

            print("\n[3] 真实样例只来自 DW 采样")
            checker.check(
                len(enum_draft.candidate_value_examples) > 0,
                f"region 草稿带真实样例（{enum_draft.candidate_value_examples}）",
            )
            checker.check_equal(
                identifier_draft.candidate_value_examples, [], "order_sn 草稿无样例"
            )
            checker.check_equal(
                identifier_draft.evidence.get("sampling", {}).get("allowed"),
                False,
                "order_sn 的采样判定与原因写入 evidence",
            )

            print("\n[4] 审核通过：写入 column_info")
            approval = await service.approve(
                draft_id=enum_draft.id,
                reviewer=REVIEWER,
                review_comment="自检通过",
                alias=["大区", "销售区域"],
            )
            await session.commit()
            checker.check_equal(approval.status, "approved", "草稿状态变为 approved")
            checker.check_equal(approval.published.get("alias"), ["大区", "销售区域"], "审计记录发布内容")
            published_column = await snapshot_column(session, "dim_store", "region")
            checker.check_equal(
                published_column.get("alias"), ["大区", "销售区域"], "审核后的别名已发布"
            )
            checker.check_equal(
                published_column.get("examples"),
                enum_draft.candidate_value_examples,
                "审核后的真实样例已发布",
            )

            print("\n[5] 已审核草稿不能重复审核")
            try:
                await service.approve(enum_draft.id, reviewer=REVIEWER)
                checker.check(False, "已审核草稿再次通过应报错")
            except RuntimeError as exc:
                checker.check("只能审核" in str(exc), f"重复审核被拦截：{exc}")

            print("\n[6] 审核驳回：不修改 column_info")
            rejection = await service.reject(
                draft_id=identifier_draft.id,
                reviewer=REVIEWER,
                review_comment="流水号字段不应作为枚举样例",
            )
            await session.commit()
            checker.check_equal(rejection.status, "rejected", "草稿状态变为 rejected")
            rejected_column = await snapshot_column(session, "fact_order", "order_sn")
            checker.check_equal(
                rejected_column.get("alias"),
                snapshots[("fact_order", "order_sn")].get("alias"),
                "驳回后 order_sn 别名保持原样",
            )
            checker.check_equal(
                rejected_column.get("examples"),
                snapshots[("fact_order", "order_sn")].get("examples"),
                "驳回后 order_sn 样例保持原样",
            )

            print("\n[7] 重复运行不重复生成")
            second_report = await service.generate(targets=[ENUM_TARGET, IDENTIFIER_TARGET])
            await session.commit()
            checker.check_equal(second_report.created, [], "已治理字段不再重复生成草稿")
            checker.check_equal(
                second_report.skipped_enriched, [ENUM_TARGET], "已发布字段被识别为语义已发布"
            )
            checker.check_equal(
                second_report.skipped_rejected,
                [IDENTIFIER_TARGET],
                "被驳回字段不再重复生成同一版候选",
            )

            print("\n[8] 空审核人必须被拒绝")
            try:
                await service.reject(identifier_draft.id, reviewer="   ")
                checker.check(False, "空审核人应报错")
            except RuntimeError as exc:
                checker.check("审核人" in str(exc), f"空审核人被拦截：{exc}")
    finally:
        try:
            if meta_client_manager.session_factory is not None:
                async with meta_client_manager.session_factory() as session:
                    for table_name, column_name in (
                        ("dim_store", "region"),
                        ("fact_order", "order_sn"),
                    ):
                        snapshot = snapshots.get((table_name, column_name))
                        if snapshot:
                            await restore_column(session, table_name, column_name, snapshot)
                    # 只清理本次自检新建的草稿：按目标字段圈定，并排除自检前已存在的草稿。
                    # 注意：外层 service 绑定的是已关闭的会话，这里必须绑定当前清理会话，
                    # 避免在退出时留下未归还到连接池的数据库连接。
                    cleanup_service = ColumnSemanticDraftService(
                        meta_repository=MetaMySQLRepository(session),
                        connector=connector,
                        llm=StubLLM(),
                    )
                    residual_drafts = await cleanup_service.list_drafts(status=None)
                    for draft in residual_drafts:
                        if (
                            draft.id not in pre_existing_draft_ids
                            and draft.qualified_name in {ENUM_TARGET, IDENTIFIER_TARGET}
                        ):
                            await session.execute(
                                text("DELETE FROM column_semantic_draft WHERE id = :draft_id"),
                                {"draft_id": draft.id},
                            )
                    await session.commit()
                    print("\n🧹 已删除自检草稿并还原 column_info 原始语义。")
        finally:
            await connector.close()
            await meta_client_manager.close()

    print("=" * 72)
    if checker.failures:
        print(f"❌ 自检失败 {len(checker.failures)} 项：")
        for failure in checker.failures:
            print(f"   - {failure}")
        return 1
    print("✅ 字段语义治理闭环自检全部通过")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
