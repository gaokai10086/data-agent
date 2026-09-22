"""字段语义草稿的人工审核入口：查看、通过、驳回。

用法示例：
    python -m app.scripts.review_column_semantic_drafts --list
    python -m app.scripts.review_column_semantic_drafts --show <草稿ID>
    python -m app.scripts.review_column_semantic_drafts --approve <草稿ID> --reviewer 张三
    python -m app.scripts.review_column_semantic_drafts --reject <草稿ID> --reviewer 张三 --comment 口径描述不准确
"""
import argparse
import asyncio

from app.clients.mysql_client import meta_client_manager
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.service.column_semantic_draft_service import (
    ColumnSemanticDraftService,
    DraftView,
    ReviewReport,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="字段语义草稿审核")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="列出草稿，默认动作")
    action.add_argument("--show", metavar="DRAFT_ID", help="查看单条草稿的完整证据")
    action.add_argument("--approve", metavar="DRAFT_ID", help="审核通过并发布到 column_info")
    action.add_argument("--reject", metavar="DRAFT_ID", help="审核驳回，不修改 column_info")
    parser.add_argument("--status", default="pending", help="--list 时按状态过滤，默认 pending")
    parser.add_argument("--reviewer", help="审核人姓名，审核动作必填")
    parser.add_argument("--comment", help="审核意见")
    parser.add_argument("--description", help="审核通过时替换候选说明后再发布")
    parser.add_argument("--alias", help="审核通过时替换别名，英文逗号分隔")
    parser.add_argument("--examples", help="审核通过时替换枚举样例，英文逗号分隔")
    parser.add_argument(
        "--role",
        choices=("primary_key", "foreign_key", "measure", "dimension"),
        help="审核通过时修正字段角色，默认不修改",
    )
    return parser.parse_args()


def split_terms(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]


def print_draft(view: DraftView, with_evidence: bool = False) -> None:
    print(f"草稿编号：{view.id}")
    print(f"字段：{view.qualified_name}   状态：{view.status}   版本：{view.revision}")
    print(f"候选说明：{view.candidate_description}")
    print(f"候选别名：{view.candidate_alias}")
    print(f"真实样例：{view.candidate_value_examples}")
    print(f"字段角色：{view.candidate_role}")
    print(f"生成模型：{view.model_name}   提示词版本：{view.prompt_version}")
    if view.reviewer:
        print(f"审核人：{view.reviewer}   审核意见：{view.review_comment}")
    if with_evidence:
        print("推导依据：")
        for key, value in view.evidence.items():
            print(f"  - {key}: {value}")
    print("-" * 72)


def print_review_report(report: ReviewReport) -> None:
    print(f"草稿 {report.draft_id}（{report.qualified_name}）已置为 {report.status}")
    print(f"审核人：{report.reviewer}")
    if report.status == "approved":
        print(f"发布前：{report.previous}")
        print(f"已发布：{report.published}")
        print("ℹ️ 该字段的向量与倒排索引需要重建后，Agent 才能召回新语义。")
    else:
        print("ℹ️ column_info 未被修改。")


async def review_column_semantic_drafts() -> None:
    args = parse_args()
    meta_client_manager.init()
    try:
        if meta_client_manager.session_factory is None:
            raise RuntimeError("Meta 数据库连接池初始化失败")
        async with meta_client_manager.session_factory() as session:
            service = ColumnSemanticDraftService(
                meta_repository=MetaMySQLRepository(session),
                connector=None,  # 审核流程不访问 DW
                llm=None,
            )

            if args.approve or args.reject:
                if not args.reviewer:
                    raise RuntimeError("审核动作必须通过 --reviewer 指定审核人")
                if args.approve:
                    report = await service.approve(
                        draft_id=args.approve,
                        reviewer=args.reviewer,
                        review_comment=args.comment,
                        description=args.description,
                        alias=split_terms(args.alias),
                        value_examples=split_terms(args.examples),
                        role=args.role,
                    )
                else:
                    report = await service.reject(
                        draft_id=args.reject,
                        reviewer=args.reviewer,
                        review_comment=args.comment,
                    )
                await session.commit()
                print_review_report(report)
                return

            status = None if args.status == "all" else args.status
            views = await service.list_drafts(status)
            print("=" * 72)
            print(f"共 {len(views)} 条草稿（状态：{args.status}）")
            print("=" * 72)
            if args.show:
                for view in views:
                    if view.id == args.show:
                        print_draft(view, with_evidence=True)
                        break
                else:
                    print(f"未在状态 {args.status} 下找到草稿 {args.show}")
                return
            for view in views:
                print_draft(view)
    finally:
        await meta_client_manager.close()


if __name__ == "__main__":
    asyncio.run(review_column_semantic_drafts())
