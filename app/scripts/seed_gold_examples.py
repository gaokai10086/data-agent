"""
================================================================================
脚本名称：全量 Few-Shot 金标 SQL 正式持久化灌库脚本 🚀💾
文件路径：app/scripts/seed_gold_examples.py
核心职责：
    1. 复用应用配置模块加载的环境变量
    2. 整合覆盖 6 大模式的高质量正向数仓金标 SQL (题库打底)
    3. 调用 ExampleService 执行物理表校验、LLM 智能打标与 BaseQdrantRepository 向量持久化
================================================================================
"""
import asyncio
import sys
from pathlib import Path

# 精准定位至 data-agent 项目根目录（向上三层）
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.clients.mysql_client import dw_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.clients.embedding_client import embedding_client_manager
from app.service.example_service import example_service, UserInputExampleItem


# 15 条经过工业级验证的真实数仓正向金标资产矩阵
GOLDEN_EXAMPLES = [
    # ---------------- 模式一：单表切片与基础聚合 ----------------
    UserInputExampleItem(
        question="统计女性且年龄在18至25岁之间的注册用户总数",
        sql="SELECT COUNT(user_id) AS young_female_count FROM dim_user WHERE gender = '女' AND age BETWEEN 18 AND 25;"
    ),
    UserInputExampleItem(
        question="查询状态处于启用状态的促销活动清单及折扣率",
        sql="SELECT promotion_id, promotion_name, discount_rate FROM dim_promotion WHERE status = 1 ORDER BY discount_rate DESC;"
    ),
    UserInputExampleItem(
        question="统计各个大区的实体店铺总数量",
        sql="SELECT region, COUNT(store_id) AS store_cnt FROM dim_store GROUP BY region ORDER BY store_cnt DESC;"
    ),
    UserInputExampleItem(
        question="查询2024年实付金额大于1000元的高价值订单总数与总销售额",
        sql="SELECT COUNT(order_id) AS high_value_order_cnt, SUM(pay_amount) AS total_amount FROM fact_order WHERE order_date >= '2024-01-01' AND order_date < '2025-01-01' AND pay_amount > 1000;"
    ),

    # ---------------- 模式二：跨表深度关联分析 ----------------
    UserInputExampleItem(
        question="查询华东大区各店铺在2024年的订单总数与实付金额",
        sql="""
        SELECT s.store_id, s.store_name, COUNT(DISTINCT o.order_id) AS total_orders, SUM(o.pay_amount) AS total_sales
        FROM fact_order o
        JOIN dim_store s ON o.store_id = s.store_id
        WHERE s.region = '华东' AND o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01'
        GROUP BY s.store_id, s.store_name
        ORDER BY total_sales DESC;
        """
    ),
    UserInputExampleItem(
        question="统计各商品一级类目下的总销量与销售总金额",
        sql="""
        SELECT p.category_level1, SUM(d.quantity) AS total_qty, SUM(d.total_sales_amount) AS total_amount
        FROM fact_order_detail d
        JOIN dim_product p ON d.product_id = p.product_id
        GROUP BY p.category_level1
        ORDER BY total_amount DESC;
        """
    ),
    UserInputExampleItem(
        question="查询从未在平台产生过任何下单行为的沉睡用户姓名与手机号",
        sql="""
        SELECT u.user_id, u.user_name
        FROM dim_user u
        LEFT JOIN fact_order o ON u.user_id = o.user_id
        WHERE o.order_id IS NULL;
        """
    ),

    # ---------------- 模式三：组内排序与排名截断 ----------------
    UserInputExampleItem(
        question="查询每个一级类目下销售额排名前3的商品品牌",
        sql="""
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
    ),
    UserInputExampleItem(
        question="查询每位用户在平台的首笔消费订单详情及实付金额",
        sql="""
        WITH user_first_order AS (
            SELECT o.user_id, o.order_id, o.order_date, o.pay_amount,
                   ROW_NUMBER() OVER(PARTITION BY o.user_id ORDER BY o.order_date ASC, o.order_id ASC) AS rn
            FROM fact_order o
        )
        SELECT user_id, order_id, order_date, pay_amount
        FROM user_first_order
        WHERE rn = 1;
        """
    ),

    # ---------------- 模式四：开窗统计与累计占比 ----------------
    UserInputExampleItem(
        question="统计各一级类目的销售额以及占全站大盘总销售额的百分比",
        sql="""
        SELECT p.category_level1, SUM(d.total_sales_amount) AS cat_sales,
               ROUND(SUM(d.total_sales_amount) * 100.0 / SUM(SUM(d.total_sales_amount)) OVER(), 2) AS sales_pct
        FROM fact_order_detail d
        JOIN dim_product p ON d.product_id = p.product_id
        GROUP BY p.category_level1;
        """
    ),
    UserInputExampleItem(
        question="计算每日销售总额以及按时间顺序向前滚动的7日移动平均销售额",
        sql="""
        WITH daily_sales AS (
            SELECT order_date, SUM(pay_amount) AS day_amount
            FROM fact_order
            GROUP BY order_date
        )
        SELECT order_date, day_amount,
               AVG(day_amount) OVER(ORDER BY order_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS rolling_avg_7d
        FROM daily_sales;
        """
    ),

    # ---------------- 模式五：时间序列与周期比对 ----------------
    UserInputExampleItem(
        question="计算每日销售额与前一日相比的销售增长金额（日环比差额）",
        sql="""
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
    ),
    UserInputExampleItem(
        question="统计平台下单活跃用户的次日留存率指标",
        sql="""
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
    ),

    # ---------------- 模式六：交叉透视与条件聚合 ----------------
    UserInputExampleItem(
        question="按品类统计2024年第一季度1月、2月和3月的销售额（月份作为独立列透视展示）",
        sql="""
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
    ),
    UserInputExampleItem(
        question="统计各大区不同订单支付状态（已支付、已退款、待支付）的订单笔数分布",
        sql="""
        SELECT s.region,
               COUNT(CASE WHEN o.order_status = 'PAID' THEN 1 END) AS paid_cnt,
               COUNT(CASE WHEN o.order_status = 'REFUNDED' THEN 1 END) AS refund_cnt,
               COUNT(CASE WHEN o.order_status = 'PENDING' THEN 1 END) AS pending_cnt
        FROM fact_order o
        JOIN dim_store s ON o.store_id = s.store_id
        GROUP BY s.region;
        """
    )
]


async def main():
    print("🚀 开始向 Qdrant 批量注入生产级金标 SQL 资产...")
    dw_client_manager.init()
    embedding_client_manager.init()
    qdrant_client_manager.init()
    try:
        # 直接调用业务服务层执行完整质检打标与异步仓储落库
        res = await example_service.batch_audit_and_upload(GOLDEN_EXAMPLES)
        print("\n🎉 资产灌库圆满成功！")
        print(f"👉 入库反馈: {res}")
    finally:
        if dw_client_manager.engine:
            await dw_client_manager.close()
        if qdrant_client_manager.client:
            await qdrant_client_manager.close()
        print("🔌 [Teardown] 数据库与向量库连接已释放。")


if __name__ == "__main__":
    asyncio.run(main())
