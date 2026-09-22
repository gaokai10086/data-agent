"""Text-to-SQL 最小评测入口。

用法：
    python -m app.evaluation.run_evaluation --list
    python -m app.evaluation.run_evaluation --id join_001 --verbose
    python -m app.evaluation.run_evaluation --refresh-gold
    python -m app.evaluation.run_evaluation
    python -m app.evaluation.run_evaluation --category 多表Join --limit 5
"""
import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from app.clients.embedding_client import embedding_client_manager
from app.clients.es_client import es_client_manager
from app.clients.mysql_client import dw_client_manager, meta_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.evaluation.metrics import summarize
from app.evaluation.runner import build_chat_service, refresh_gold, run_case
from app.evaluation.schema import load_cases, save_cases
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Text-to-SQL 最小评测框架")
    parser.add_argument("--list", action="store_true", help="列出全部评测题目")
    parser.add_argument("--id", default="", help="只跑指定 id 的题目")
    parser.add_argument("--refresh-gold", action="store_true", help="重新执行 gold_sql 刷新标准答案")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--category", default="", help="只跑指定分类")
    parser.add_argument("--numeric-scale", type=int, default=2, help="数值比较精度")
    parser.add_argument("--output", default="", help="报告输出路径")
    parser.add_argument("--verbose", action="store_true", help="打印每题的 SQL 与结果明细")
    return parser.parse_args()


def _print_case_list(cases: list) -> None:
    print(f"共 {len(cases)} 道评测题")
    print("")
    print(f"{'id':<14}{'分类':<10}{'难度':<8}{'标准答案行数':<12}问题")
    print("-" * 90)
    for case in cases:
        print(
            f"{case.id:<14}{case.category:<10}{case.difficulty:<8}"
            f"{len(case.expected_result):<12}{case.question}"
        )


def _write_report(summary: dict, results: list, output_path: str = "") -> Path:
    if output_path:
        path = Path(output_path)
    else:
        reports_dir = Path(__file__).parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        path = reports_dir / f"eval_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    lines = [
        "# Text-to-SQL 评测报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"总体 Execution Accuracy：{summary['execution_accuracy']:.1%} "
        f"({summary['passed']}/{summary['total']})",
        "",
        f"Schema Linking Recall：{summary['avg_schema_linking_recall']:.1%}",
        f"Join Accuracy：{summary['avg_join_accuracy']:.1%}",
        f"延迟 P50 / P95：{summary['latency_p50']:.0f} ms / {summary['latency_p95']:.0f} ms",
        "",
        "## 分类准确率",
        "",
        "| 分类 | 通过/总数 | 准确率 |",
        "| --- | --- | --- |",
    ]
    for category, stats in summary["by_category"].items():
        lines.append(f"| {category} | {stats['passed']}/{stats['total']} | {stats['accuracy']:.1%} |")

    lines.extend(["", "## 失败明细", ""])
    failures = [result for result in results if not result.passed]
    if not failures:
        lines.append("全部通过。")
    for result in failures:
        lines.extend([
            f"### {result.case_id} {result.question}",
            "",
            f"- 分类：{result.category}",
            f"- 失败原因：{result.failure_reason}",
        ])
        if result.error:
            lines.append(f"- Agent 错误：{result.error}")
        if result.actual_sql:
            lines.extend(["- 实际 SQL：", "", "```sql", result.actual_sql, "```"])
        lines.extend([
            "",
            f"- 期望结果（前 3 行）：{result.expected_result[:3]}",
            f"- 实际结果（前 3 行）：{result.actual_result[:3]}",
            "",
        ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def main() -> None:
    args = parse_args()
    cases = load_cases()

    if args.list:
        _print_case_list(cases)
        return

    if args.id:
        cases = [case for case in cases if case.id == args.id]
        if not cases:
            raise SystemExit(f"未找到评测用例：{args.id}")
    if args.category:
        cases = [case for case in cases if case.category == args.category]
    if args.limit:
        cases = cases[: args.limit]

    meta_client_manager.init()
    dw_client_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    embedding_client_manager.init()

    try:
        if args.refresh_gold:
            async with dw_client_manager.session_factory() as dw_session:
                cases = await refresh_gold(cases, DWMySQLRepository(dw_session))
            save_cases(cases)
            print(f"已刷新 {len(cases)} 道题的标准答案快照。")
            return

        results = []
        for case in cases:
            async with (
                meta_client_manager.session_factory() as meta_session,
                dw_client_manager.session_factory() as dw_session,
            ):
                chat_service = build_chat_service(meta_session, dw_session)
                result = await run_case(chat_service, case, numeric_scale=args.numeric_scale)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {case.id} {case.question} ({result.latency_ms:.0f} ms)")
            if args.verbose:
                print(f"  实际 SQL: {result.actual_sql or '(无)'}")
                print(f"  期望结果: {result.expected_result[:3]}")
                print(f"  实际结果: {result.actual_result[:3]}")
                if result.failure_reason:
                    print(f"  失败原因: {result.failure_reason}")
                print("")

        summary = summarize(results)
        report_path = _write_report(summary, results, args.output)
        print("")
        print(
            f"Execution Accuracy: {summary['execution_accuracy']:.1%} "
            f"({summary['passed']}/{summary['total']})"
        )
        print(f"报告已写入：{report_path}")
    finally:
        await meta_client_manager.close()
        await dw_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
