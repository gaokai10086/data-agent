"""向评测集追加一道新题，其余元数据自动推导。

你只需要提供三样东西：
    question   问什么
    category   属于哪一类（用于分组统计）
    gold_sql   标准 SQL（正确答案的定义）

框架会自动完成：
    1. EXPLAIN 校验 SQL 能否执行
    2. 从 SQL 中识别物理表
    3. 从执行计划中提取用到的字段
    4. 根据 SQL 结构推断难度
    5. 自动生成用例 id
    6. 执行 gold SQL 填充标准答案快照

用法：
    python -m app.evaluation.add_case \
        --question "每个大区销售额排名前3的门店是哪些？" \
        --category 组内TopN \
        --gold-sql "SELECT region, store_name, sales_amount FROM (...)"

只记录题目、暂不连接数据库：
    python -m app.evaluation.add_case --question "..." --category "..." --gold-sql "..." --offline
"""
import argparse
import asyncio

from app.clients.mysql_client import dw_client_manager
from app.evaluation.comparator import jsonify_rows
from app.evaluation.schema import EvalCase, load_cases, save_cases
from app.repositories.mysql.dw_mysql_repository import DWMySQLRepository

CATEGORY_SLUGS = {
    "单表筛选": "single",
    "基础聚合": "agg",
    "多表Join": "join",
    "时间范围": "time",
    "枚举值过滤": "value",
    "指标口径": "metric",
    "TopN": "topn",
    "组内TopN": "grouptopn",
    "超范围": "outscope",
}


def infer_difficulty(sql: str) -> str:
    """根据 SQL 结构推断难度，避免手工填写主观值。"""
    normalized = " ".join(sql.split()).upper()
    join_count = normalized.count(" JOIN ")
    has_window = any(
        keyword in normalized
        for keyword in ("OVER(", "OVER (", "ROW_NUMBER", "DENSE_RANK", "RANK(", "LAG(", "LEAD(")
    )
    has_cte = normalized.startswith("WITH")
    has_subquery = normalized.count("SELECT") > 1
    has_group = "GROUP BY" in normalized
    has_having = "HAVING" in normalized

    score = join_count * 2
    score += 2 if has_window else 0
    score += 1 if has_cte else 0
    score += 1 if has_subquery else 0
    score += 1 if has_group else 0
    score += 1 if has_having else 0

    if score <= 1:
        return "easy"
    if score <= 3:
        return "medium"
    return "hard"


def next_case_id(cases: list[EvalCase], category: str) -> str:
    """按分类生成带序号的用例 id，例如 join_005。"""
    slug = CATEGORY_SLUGS.get(category, "case")
    existing = {case.id for case in cases}
    index = 1
    while f"{slug}_{index:03d}" in existing:
        index += 1
    return f"{slug}_{index:03d}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="向评测集追加一道题（元数据自动推导）")
    parser.add_argument("--question", required=True, help="用户自然语言问题")
    parser.add_argument("--category", required=True, help="分类，例如 多表Join")
    parser.add_argument("--gold-sql", default="", help="标准 SQL")
    parser.add_argument("--id", default="", help="用例 id，不填则自动生成")
    parser.add_argument("--metric", default="", help="涉及的指标名称（可选）")
    parser.add_argument("--notes", default="", help="备注（可选）")
    parser.add_argument("--expect-no-sql", action="store_true", help="该题不应返回业务数据")
    parser.add_argument("--offline", action="store_true", help="不连接数据库，只记录题目")
    return parser.parse_args()


async def build_case(args: argparse.Namespace, cases: list[EvalCase]) -> EvalCase:
    case_id = args.id.strip() or next_case_id(cases, args.category)
    case = EvalCase(
        id=case_id,
        question=args.question.strip(),
        category=args.category.strip(),
        gold_sql=args.gold_sql.strip(),
        metric=args.metric.strip(),
        notes=args.notes.strip(),
        expect_no_sql=args.expect_no_sql,
    )
    if args.offline or not case.gold_sql:
        return case

    dw_client_manager.init()
    try:
        async with dw_client_manager.session_factory() as session:
            repository = DWMySQLRepository(session)
            await repository.validate_sql(case.gold_sql)
            case.tables = await repository.extract_tables_from_sql(case.gold_sql)
            case.columns = await repository.get_used_columns_from_sql(case.gold_sql)
            case.difficulty = infer_difficulty(case.gold_sql)
            rows = await repository.execute_sql(case.gold_sql)
            case.expected_result = jsonify_rows(rows)
    finally:
        await dw_client_manager.close()
    return case


async def main() -> None:
    args = parse_args()
    if not args.gold_sql and not args.expect_no_sql:
        raise SystemExit("必须提供 --gold-sql，或使用 --expect-no-sql 表示该题不应返回数据。")

    cases = load_cases()
    if args.id and any(case.id == args.id for case in cases):
        raise SystemExit(f"用例 id 已存在：{args.id}")

    try:
        case = await build_case(args, cases)
    except Exception as exc:
        raise SystemExit(f"gold SQL 校验失败，请先修 SQL 再加题：{exc}") from exc

    cases.append(case)
    save_cases(cases)

    print(f"已添加用例 {case.id}，当前共 {len(cases)} 题。")
    print(f"  分类：{case.category}    难度：{case.difficulty}（自动推断）")
    print(f"  涉及表：{case.tables}")
    print(f"  涉及字段：{case.columns}")
    print(f"  标准答案：{len(case.expected_result)} 行")
    if args.offline or not case.gold_sql:
        print("  提示：离线模式未生成标准答案，稍后执行 --refresh-gold 补齐。")
    else:
        print("  标准答案已自动填充，可直接执行单题调试：")
        print(f"    python -m app.evaluation.run_evaluation --id {case.id} --verbose")


if __name__ == "__main__":
    asyncio.run(main())
