"""
================================================================================
测试脚本：Few-Shot 金标 SQL 20 条工业级深度评测套件 (覆盖 6 大核心范式与防御门禁) 🧪
文件路径：app/tests/test_audit_dry_run.py
核心目标：
    1. 覆盖 20 个不同业务维度的测试用例，涵盖 6 大核心计算范式与边界拦截
    2. 针对每个用例完整输出最终组装好的 Qdrant Payload 载荷字典
================================================================================
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi import HTTPException
from app.clients.mysql_client import dw_client_manager
from app.api.example import upload_examples, BatchUploadRequest, UserInputExampleItem


def print_banner(index: int, title: str, category: str):
    print("\n" + "=" * 80)
    print(f"📌 [Case {index:02d}] 【{category}】: {title}")
    print("=" * 80)


def print_payload(payload: dict):
    """格式化打印待落库的 Payload 实体"""
    print("📦 【生成的 Qdrant Payload 载荷】:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def run_single_test(index: int, category: str, question: str, sql: str, expect_error: bool = False):
    """通用测试执行器"""
    print_banner(index, question, category)
    req = BatchUploadRequest(
        dry_run=True,
        items=[UserInputExampleItem(question=question, sql=sql)]
    )
    try:
        res = await upload_examples(req)
        if expect_error:
            print(f"⚠️ [未达预期] 该用例预期拦截，但模型放行: {res}")
        else:
            print_payload(res["audited_items"][0])
    except HTTPException as he:
        if expect_error:
            print(f"✅ [拦截命中预期] (HTTP {he.status_code}): {he.detail}")
        else:
            print(f"❌ [意外拦截] (HTTP {he.status_code}): {he.detail}")
    except Exception as e:
        print(f"❌ [运行时崩溃]: {e}")


# ==============================================================================
# 20 条数仓工业级测试用例矩阵
# ==============================================================================
TEST_CASES = [
    # ---------------- 模块一：单表切片与基础聚合 (4条) ----------------
    {
        "category": "单表切片与基础聚合",
        "question": "统计女性且年龄在18至25岁之间的注册用户总数",
        "sql": "SELECT COUNT(user_id) AS young_female_count FROM dim_user WHERE gender = '女' AND age BETWEEN 18 AND 25;"
    },
    {
        "category": "单表切片与基础聚合",
        "question": "查询状态处于启用状态的促销活动清单及折扣率",
        "sql": "SELECT promotion_id, promotion_name, discount_rate FROM dim_promotion WHERE status = 1 ORDER BY discount_rate DESC;"
    },
    {
        "category": "单表切片与基础聚合",
        "question": "统计各个大区的实体店铺总数量",
        "sql": "SELECT region, COUNT(store_id) AS store_cnt FROM dim_store GROUP BY region ORDER BY store_cnt DESC;"
    },
    {
        "category": "单表切片与基础聚合",
        "question": "查询2024年实付金额大于1000元的高价值订单总数与总销售额",
        "sql": "SELECT COUNT(order_id) AS high_value_order_cnt, SUM(pay_amount) AS total_amount FROM fact_order WHERE order_date >= '2024-01-01' AND order_date < '2025-01-01' AND pay_amount > 1000;"
    },

    # ---------------- 模块二：跨表深度关联分析 (4条) ----------------
    {
        "category": "跨表深度关联分析",
        "question": "查询华东大区各店铺在2024年的订单总数与实付金额",
        "sql": """
        SELECT s.store_id, s.store_name, COUNT(DISTINCT o.order_id) AS total_orders, SUM(o.pay_amount) AS total_sales
        FROM fact_order o
        JOIN dim_store s ON o.store_id = s.store_id
        WHERE s.region = '华东' AND o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01'
        GROUP BY s.store_id, s.store_name
        ORDER BY total_sales DESC;
        """
    },
    {
        "category": "跨表深度关联分析",
        "question": "统计各商品一级类目下的总销量与销售总金额",
        "sql": """
        SELECT p.category_level1, SUM(d.quantity) AS total_qty, SUM(d.total_sales_amount) AS total_amount
        FROM fact_order_detail d
        JOIN dim_product p ON d.product_id = p.product_id
        GROUP BY p.category_level1
        ORDER BY total_amount DESC;
        """
    },
    {
        "category": "跨表深度关联分析",
        "question": "查询从未在平台产生过任何下单行为的沉睡用户姓名与手机号",
        "sql": """
        SELECT u.user_id, u.user_name
        FROM dim_user u
        LEFT JOIN fact_order o ON u.user_id = o.user_id
        WHERE o.order_id IS NULL;
        """
    },
    {
        "category": "跨表深度关联分析",
        "question": "基于双层CTE查询日均销售额超过5000元的大区与店铺",
        "sql": """
        WITH cte_daily AS (
            SELECT s.region, s.store_id, s.store_name, o.order_date, SUM(o.pay_amount) AS daily_sum
            FROM fact_order o
            JOIN dim_store s ON o.store_id = s.store_id
            GROUP BY s.region, s.store_id, s.store_name, o.order_date
        ),
        cte_avg AS (
            SELECT region, store_id, store_name, AVG(daily_sum) AS avg_sales
            FROM cte_daily
            GROUP BY region, store_id, store_name
        )
        SELECT region, store_id, store_name, ROUND(avg_sales, 2) AS avg_sales
        FROM cte_avg
        WHERE avg_sales > 5000;
        """
    },

    # ---------------- 模块三：组内排序与排名截断 (3条) ----------------
    {
        "category": "组内排序与排名截断",
        "question": "查询每个一级类目下销售额排名前3的商品品牌",
        "sql": """
        WITH ranked_brand AS (
            SELECT p.category_level1, p.brand, SUM(d.total_sales_amount) AS brand_sales,
                   DENSE_RANK() OVER(PARTITION BY p.category_level1 ORDER BY SUM(d.total_sales_amount) DESC) AS rnk
            FROM fact_order_detail d
            JOIN dim_product p ON d.product_id = p.product_id
            GROUP BY p.category_level1, p.brand
        )
        SELECT category_level1, brand, brand_sales
        FROM ranked_brand
        WHERE rnk <= 3;
        """
    },
    {
        "category": "组内排序与排名截断",
        "question": "查询每位用户在平台的首笔消费订单详情及实付金额",
        "sql": """
        WITH user_first_order AS (
            SELECT o.user_id, o.order_id, o.order_date, o.pay_amount,
                   ROW_NUMBER() OVER(PARTITION BY o.user_id ORDER BY o.order_date ASC, o.order_id ASC) AS rn
            FROM fact_order o
        )
        SELECT user_id, order_id, order_date, pay_amount
        FROM user_first_order
        WHERE rn = 1;
        """
    },
    {
        "category": "组内排序与排名截断",
        "question": "查询每个大区内实付销售总额最高的一家旗舰店铺",
        "sql": """
        WITH ranked_store AS (
            SELECT s.region, s.store_id, s.store_name, SUM(o.pay_amount) AS store_sales,
                   RANK() OVER(PARTITION BY s.region ORDER BY SUM(o.pay_amount) DESC) AS rank_num
            FROM fact_order o
            JOIN dim_store s ON o.store_id = s.store_id
            GROUP BY s.region, s.store_id, s.store_name
        )
        SELECT region, store_id, store_name, store_sales
        FROM ranked_store
        WHERE rank_num = 1;
        """
    },

    # ---------------- 模块四：开窗统计与累计占比 (3条) ----------------
    {
        "category": "开窗统计与累计占比",
        "question": "统计各一级类目的销售额以及占全站大盘总销售额的百分比",
        "sql": """
        SELECT p.category_level1, SUM(d.total_sales_amount) AS cat_sales,
               ROUND(SUM(d.total_sales_amount) * 100.0 / SUM(SUM(d.total_sales_amount)) OVER(), 2) AS sales_pct
        FROM fact_order_detail d
        JOIN dim_product p ON d.product_id = p.product_id
        GROUP BY p.category_level1;
        """
    },
    {
        "category": "开窗统计与累计占比",
        "question": "计算每日销售总额以及按时间顺序向前滚动的7日移动平均销售额",
        "sql": """
        WITH daily_sales AS (
            SELECT order_date, SUM(pay_amount) AS day_amount
            FROM fact_order
            GROUP BY order_date
        )
        SELECT order_date, day_amount,
               AVG(day_amount) OVER(ORDER BY order_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS rolling_avg_7d
        FROM daily_sales;
        """
    },
    {
        "category": "开窗统计与累计占比",
        "question": "统计每月销售总额及当年的累计销售总额趋势",
        "sql": """
        WITH monthly_sales AS (
            SELECT DATE_FORMAT(order_date, '%Y-%m') AS sales_month, SUM(pay_amount) AS month_amount
            FROM fact_order
            GROUP BY DATE_FORMAT(order_date, '%Y-%m')
        )
        SELECT sales_month, month_amount,
               SUM(month_amount) OVER(ORDER BY sales_month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_total_amount
        FROM monthly_sales;
        """
    },

    # ---------------- 模块五：时间序列与周期比对 (2条) ----------------
    {
        "category": "时间序列与周期比对",
        "question": "计算每日销售额与前一日相比的销售增长金额（日环比差额）",
        "sql": """
        WITH daily_agg AS (
            SELECT order_date, SUM(pay_amount) AS current_amount
            FROM fact_order
            GROUP BY order_date
        )
        SELECT order_date, current_amount,
               LAG(current_amount, 1) OVER (ORDER BY order_date) AS prev_amount,
               (current_amount - LAG(current_amount, 1) OVER (ORDER BY order_date)) AS day_diff
        FROM daily_agg;
        """
    },
    {
        "category": "时间序列与周期比对",
        "question": "统计平台下单活跃用户的次日留存率指标",
        "sql": """
        SELECT today.order_date,
               COUNT(DISTINCT today.user_id) AS active_users,
               COUNT(DISTINCT tomorrow.user_id) AS retained_users,
               ROUND(COUNT(DISTINCT tomorrow.user_id) * 100.0 / COUNT(DISTINCT today.user_id), 2) AS retention_rate
        FROM fact_order today
        LEFT JOIN fact_order tomorrow 
            ON today.user_id = tomorrow.user_id 
            AND tomorrow.order_date = DATE_ADD(today.order_date, INTERVAL 1 DAY)
        GROUP BY today.order_date
        ORDER BY today.order_date DESC;
        """
    },

    # ---------------- 模块六：交叉透视与条件聚合 (2条) ----------------
    {
        "category": "交叉透视与条件聚合",
        "question": "按品类统计2024年第一季度1月、2月和3月的销售额（月份作为独立列透视展示）",
        "sql": """
        SELECT p.category_level1,
               SUM(CASE WHEN MONTH(o.order_date) = 1 THEN d.total_sales_amount ELSE 0 END) AS m1_sales,
               SUM(CASE WHEN MONTH(o.order_date) = 2 THEN d.total_sales_amount ELSE 0 END) AS m2_sales,
               SUM(CASE WHEN MONTH(o.order_date) = 3 THEN d.total_sales_amount ELSE 0 END) AS m3_sales
        FROM fact_order_detail d
        JOIN fact_order o ON d.order_id = o.order_id
        JOIN dim_product p ON d.product_id = p.product_id
        WHERE o.order_date >= '2024-01-01' AND o.order_date < '2024-04-01'
        GROUP BY p.category_level1;
        """
    },
    {
        "category": "交叉透视与条件聚合",
        "question": "统计各大区不同订单支付状态（已支付、已退款、待支付）的订单笔数分布",
        "sql": """
        SELECT s.region,
               COUNT(CASE WHEN o.order_status = 'PAID' THEN 1 END) AS paid_cnt,
               COUNT(CASE WHEN o.order_status = 'REFUNDED' THEN 1 END) AS refund_cnt,
               COUNT(CASE WHEN o.order_status = 'PENDING' THEN 1 END) AS pending_cnt
        FROM fact_order o
        JOIN dim_store s ON o.store_id = s.store_id
        GROUP BY s.region;
        """
    },

    # ---------------- 模块七：安全边界与质检门禁拦截 (2条) ----------------
    {
        "category": "门禁防御 (虚假表)",
        "question": "统计供应商发货数量明细",
        "sql": "SELECT supplier_id, SUM(ship_qty) FROM fact_supplier_delivery GROUP BY supplier_id;",
        "expect_error": True
    },
    {
        "category": "门禁防御 (非只读写操作)",
        "question": "删除测试用户的数据",
        "sql": "DELETE FROM dim_user WHERE user_id = 99999;",
        "expect_error": True
    }
]


# ==============================================================================
# 主循环执行
# ==============================================================================
async def main():
    print("🚀 开始执行 20 条全量 Few-Shot 金标 SQL 工业级评测矩阵...\n")
    dw_client_manager.init()
    try:
        for idx, tc in enumerate(TEST_CASES, start=1):
            await run_single_test(
                index=idx,
                category=tc["category"],
                question=tc["question"],
                sql=tc["sql"],
                expect_error=tc.get("expect_error", False)
            )
            # 适度停顿避免 API 请求频率超出 DeepSeek 速率阈值
            await asyncio.sleep(0.5)
    finally:
        if dw_client_manager.engine:
            await dw_client_manager.close()
            print("\n🔌 [Teardown] 数仓数据库连接池已安全关闭。")

    print("\n🏁 20 条基准评测矩阵全量执行完毕！")


if __name__ == "__main__":
    asyncio.run(main())