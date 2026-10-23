from pathlib import Path
from app.service.conversation_context import QueryContext, rewrite_query

CASES = [
    ("地区替换", "查询最近30天华东地区的销售额", "那华南地区呢？", "查询最近30天华南地区的销售额"),
    ("时间替换", "查询华东地区最近30天的订单数量", "改成上个月呢？", "查询华东地区上个月的订单数量"),
    ("增加分组维度", "统计最近30天各地区的销售额", "再按渠道拆分一下", "统计最近30天各地区的销售额，按渠道拆分"),
    ("新问题识别", "查询华东地区最近30天的销售额", "统计最近7天的订单数量", "统计最近7天的订单数量"),
    ("连续三轮追问", "查询最近30天华东地区的销售额", "那华南呢？ -> 再改成上个月", "查询上个月华南地区的销售额"),
]

def main():
    rows=[]
    for name, first, followup, expected in CASES:
        previous=QueryContext(first, first, "query_database")
        if " -> " in followup:
            first_follow, second_follow = followup.split(" -> ")
            actual, _ = rewrite_query(first_follow, previous)
            actual, _ = rewrite_query(second_follow, QueryContext(actual, actual, "query_database"))
        else:
            actual, _ = rewrite_query(followup, previous)
        passed = (actual == expected) if name != "新问题识别" else actual == expected
        rows.append((name, first, followup, expected, actual, "通过" if passed else "失败", "" if passed else "规则输出与预期不同"))
    report = ["# multiturn_test_report", "", "| 测试组 | 第一轮 | 后续输入 | 预期改写 | 实际改写 | 结果 | 失败原因 |", "|---|---|---|---|---|---|---|"]
    report += ["|"+"|".join(row)+"|" for row in rows]
    report += ["", "## 真实 LLM 评测", "", "未执行：当前环境未配置可验证的模型凭证；以上结果全部为 Mock/规则单元测试，不代表真实 LLM 改写效果。", "", "## 运行说明", "", "`PYTHONPATH=. .venv/bin/python -m unittest app.tests.test_multiturn_context -v`（环境未安装 pytest；该测试文件同时兼容 pytest）。"]
    Path("multiturn_test_report.md").write_text("\n".join(report)+"\n", encoding="utf-8")

if __name__ == "__main__": main()
