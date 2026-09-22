"""
================================================================================
模块名称：dw 业务数仓物理表结构与字段注释探针脚本 🔍
文件路径：inspect_dw_schema.py
核心功能：
    直连 dw 数据库，拉取所有物理表及其字段名、数据类型与业务注释，
    用于核对真实数仓资产并针对性构造金标测试问题。
================================================================================
"""
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 1. 导入你现成的 dw 业务库连接管理器与配置
# 注意：请将下面的导入路径替换为你实际存放 MySQLClientManager 的模块路径
# 例如：from app.clients.mysql_client import dw_client_manager, app_config
from app.clients.mysql_client import dw_client_manager
from app.config.app_config import app_config


async def inspect_dw():
    print("=" * 75)
    print(f"📋 正在导出 [{app_config.db_dw.database}] 业务数仓的物理表结构与字段注释...")
    print("=" * 75)

    # 2. 初始化 dw 异步引擎连接池
    dw_client_manager.init()

    # 3. 查询当前库下的表与字段元数据
    # 使用参数化查询过滤当前 dw 数据库名称，确保不查混其他系统库
    query_sql = text("""
        SELECT 
            TABLE_NAME, 
            COLUMN_NAME, 
            DATA_TYPE, 
            COLUMN_COMMENT 
        FROM information_schema.COLUMNS 
        WHERE TABLE_SCHEMA = :db_name
        ORDER BY TABLE_NAME, ORDINAL_POSITION;
    """)

    try:
        async with AsyncSession(dw_client_manager.engine) as session:
            result = await session.execute(query_sql, {"db_name": app_config.db_dw.database})
            rows = result.fetchall()

        if not rows:
            print(f"⚠️ 未在数据库 [{app_config.db_dw.database}] 中检索到任何物理字段，请检查配置或库名！")
            return

        current_table = ""
        table_count = 0
        column_count = 0

        for table_name, column_name, data_type, comment in rows:
            column_count += 1
            if table_name != current_table:
                table_count += 1
                current_table = table_name
                print(f"\n📁 【表名】: {table_name}")
                print(f"   {'列名 (Column)':<28} {'类型 (Type)':<16} {'注释说明 (Comment)'}")
                print("   " + "-" * 65)

            comment_str = comment.strip() if comment else "无注释"
            print(f"   {column_name:<28} {data_type:<16} {comment_str}")

        print("\n" + "=" * 75)
        print(f"✅ 导出完毕！共探测到 {table_count} 张物理表，{column_count} 个物理字段。")
        print("💡 建议：根据上方打印出的真实表名与字段名构造业务问答。")
        print("=" * 75)

    finally:
        # 4. 规范释放连接池资源
        await dw_client_manager.close()


if __name__ == "__main__":
    asyncio.run(inspect_dw())