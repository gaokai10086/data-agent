"""为高价值字段生成待审核的字段语义草稿。

该脚本只写 column_semantic_draft，不修改 column_info。
审核发布请使用：python -m app.scripts.review_column_semantic_drafts --approve <草稿ID>
"""
import argparse
import asyncio

from app.agent.llm import get_llm
from app.clients.mysql_client import meta_client_manager
from app.config.app_config import app_config
from app.core.datasource.mysql_connector import MySQLConnector
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.column_semantic_draft_service import (
    DEFAULT_DRAFT_TARGETS,
    ColumnSemanticDraftService,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成字段语义草稿（等待人工审核）")
    parser.add_argument(
        "--column",
        action="append",
        dest="columns",
        help="指定字段，格式 表名.字段名，可重复传入；默认使用内置高价值字段清单",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="从 Meta 枚举全部已同步字段生成草稿，代替默认的高价值字段清单",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="对已发布语义或已有待审核草稿的字段强制重新生成，旧草稿标记为 superseded",
    )
    parser.add_argument(
        "--model-tier",
        choices=("flash", "pro"),
        default="flash",
        help="使用 flash 或 pro 规格模型，默认 flash",
    )
    return parser.parse_args()


async def generate_column_semantic_drafts() -> None:
    args = parse_args()
    if args.all and args.columns:
        raise SystemExit("--all 与 --column 不能同时使用。")
    targets = tuple(args.columns) if args.columns else None

    connector = MySQLConnector(
        host=app_config.db_dw.host,
        port=app_config.db_dw.port,
        user=app_config.db_dw.user,
        password=app_config.db_dw.password,
        database=app_config.db_dw.database,
    )
    meta_client_manager.init()
    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        llm = get_llm(provider="deepseek", model_tier=args.model_tier, temperature=0.2)
        async with meta_client_manager.session_factory() as session:
            service = ColumnSemanticDraftService(
                meta_repository=MetaMySQLRepository(session),
                connector=connector,
                llm=llm,
            )
            if args.all:
                targets = await service.collect_all_targets()
            elif targets is None:
                targets = DEFAULT_DRAFT_TARGETS

            print("=" * 72)
            print(f"🚀 开始生成字段语义草稿，共 {len(targets)} 个目标字段")
            print("=" * 72)
            report = await service.generate(targets=targets, force=args.force)
            await session.commit()

        print("✅ 草稿生成完成：")
        print(f"   新增草稿：{len(report.created)} -> {report.created}")
        print(f"   取代旧草稿：{len(report.superseded)} -> {report.superseded}")
        print(f"   跳过（已有待审核草稿）：{len(report.skipped_pending)} -> {report.skipped_pending}")
        print(f"   跳过（语义已发布）：{len(report.skipped_enriched)} -> {report.skipped_enriched}")
        print(f"   跳过（候选已被驳回）：{len(report.skipped_rejected)} -> {report.skipped_rejected}")
        if report.failures:
            print(f"   失败：{len(report.failures)}")
            for qualified_name, message in report.failures:
                print(f"     - {qualified_name}: {message}")
        print("ℹ️ 草稿仍处于 pending 状态，需审核通过后才会写入 column_info。")
        print("ℹ️ 查看与审核：python -m app.scripts.review_column_semantic_drafts --list")
    finally:
        await connector.close()
        await meta_client_manager.close()


if __name__ == "__main__":
    asyncio.run(generate_column_semantic_drafts())
