"""初始化封闭枚举字段：从 DW 拉取真实取值写入 Meta，并清理其余字段的样例。

背景：
    字段分为两类，由 value_type 标记
        closed = 封闭枚举，人工维护枚举值，用于值召回精确匹配
        open   = 开放枚举，不维护值，由大模型根据语义推导

    本脚本是一次性初始化：
        1. 对 CLOSED_ENUMS 中的字段，从 DW 读取去重值写入 column_info.examples
        2. 把这些字段的 value_type 设为 closed
        3. 其余字段 value_type 设为 open，并清空 examples

用法：
    python -m app.scripts.init_enum_fields
"""
import asyncio

from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository

# 封闭枚举字段：企业定义、取值有限、用于值召回
CLOSED_ENUMS: tuple[str, ...] = (
    "fact_order.order_status",
    "dim_store.region",
    "dim_store.channel_type",
    "dim_user.gender",
    "dim_user.user_level",
    "dim_user.register_channel",
    "dim_promotion.promo_type",
    "dim_promotion.promo_name",
    "dim_product.category_level1",
    "dim_product.category_level2",
    "dim_product.brand",
)


async def main() -> None:
    meta_client_manager.init()
    dw_client_manager.init()
    try:
        async with (
            meta_client_manager.session_factory() as meta_session,
            dw_client_manager.session_factory() as dw_session,
        ):
            meta_repo = MetaMySQLRepository(meta_session)
            dw_repo = DWMySQLRepository(dw_session)

            closed_count = 0
            cleared_count = 0

            for table, column in await meta_repo.list_columns_with_tables():
                qualified_name = f"{table.name}.{column.name}"
                if qualified_name in CLOSED_ENUMS:
                    values = await dw_repo.get_column_values(table.name, column.name or "", 200)
                    column.value_type = "closed"
                    column.examples = [str(value) for value in values]
                    closed_count += 1
                    print(f"  [closed] {qualified_name}: {len(column.examples)} 个枚举值")
                else:
                    column.value_type = "open"
                    if column.examples:
                        cleared_count += 1
                    column.examples = []
                await meta_repo.save_column(column)

            await meta_session.commit()
            print()
            print(f"✅ 已标记 {closed_count} 个封闭枚举字段，清理 {cleared_count} 个字段的错误样例。")
    finally:
        await meta_client_manager.close()
        await dw_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
