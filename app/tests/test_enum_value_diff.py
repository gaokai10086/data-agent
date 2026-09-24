"""枚举值对照功能自测脚本。

验证三个场景：
    场景一：Meta 多登记了一个 DW 中不存在的值  → missing_in_dw 应包含它
    场景二：Meta 少登记了一个 DW 中存在的值    → unregistered 应包含它
    场景三：Meta 与 DW 完全一致                → 两个差异列表都应为空

全程在同一个事务内进行，结束时 rollback，不会修改真实数据。

运行：
    python -m app.tests.test_enum_value_diff
"""
import asyncio

from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.column_semantic_draft_service import ColumnSemanticDraftService

TABLE = "dim_store"
COLUMN = "channel_type"
REVIEWER = "self-check"
FAKE_VALUE = "测试渠道"


async def main() -> int:
    failures: list[str] = []
    meta_client_manager.init()
    dw_client_manager.init()
    try:
        async with (
            meta_client_manager.session_factory() as meta_session,
            dw_client_manager.session_factory() as dw_session,
        ):
            meta_repo = MetaMySQLRepository(meta_session)
            dw_repo = DWMySQLRepository(dw_session)
            service = ColumnSemanticDraftService(
                meta_repository=meta_repo, connector=None, llm=None
            )

            table = await meta_repo.get_table_by_name(TABLE)
            column = await meta_repo.get_column(table.id, COLUMN)
            original = [str(item) for item in (column.examples or [])]
            dw_values = await dw_repo.get_column_values(TABLE, COLUMN, 200)

            print("=" * 70)
            print(f"目标字段：{TABLE}.{COLUMN}")
            print(f"  Meta 原始登记：{original}")
            print(f"  DW 实际取值  ：{dw_values}")
            print("=" * 70)

            print("\n[场景一] Meta 多登记一个 DW 中不存在的值")
            await service.update_field(
                column_id=column.id,
                reviewer=REVIEWER,
                examples=original + [FAKE_VALUE],
                value_type="closed",
            )
            await meta_session.flush()
            diff = service.build_value_diff(column, dw_values)
            print(f"  missing_in_dw = {diff['missing_in_dw']}")
            ok = FAKE_VALUE in diff["missing_in_dw"]
            print(f"  {'✅ 通过' if ok else '❌ 失败'}")
            if not ok:
                failures.append("场景一")

            removed = original[0] if original else None
            print(f"\n[场景二] Meta 少登记一个 DW 中存在的值（{removed}）")
            await service.update_field(
                column_id=column.id,
                reviewer=REVIEWER,
                examples=[item for item in original if item != removed],
                value_type="closed",
            )
            await meta_session.flush()
            diff = service.build_value_diff(column, dw_values)
            print(f"  unregistered = {diff['unregistered']}")
            ok = removed in diff["unregistered"]
            print(f"  {'✅ 通过' if ok else '❌ 失败'}")
            if not ok:
                failures.append("场景二")

            print("\n[场景三] Meta 与 DW 完全一致")
            await service.update_field(
                column_id=column.id,
                reviewer=REVIEWER,
                examples=original,
                value_type="closed",
            )
            await meta_session.flush()
            diff = service.build_value_diff(column, dw_values)
            print(
                f"  unregistered={diff['unregistered']}  "
                f"missing_in_dw={diff['missing_in_dw']}"
            )
            ok = not diff["unregistered"] and not diff["missing_in_dw"]
            print(f"  {'✅ 通过' if ok else '❌ 失败'}")
            if not ok:
                failures.append("场景三")

            await meta_session.rollback()
            print("\n🧹 已回滚，Meta 数据未发生任何变化。")
    finally:
        await meta_client_manager.close()
        await dw_client_manager.close()

    print("=" * 70)
    if failures:
        print(f"❌ 失败场景：{failures}")
        return 1
    print("✅ 三个场景全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
