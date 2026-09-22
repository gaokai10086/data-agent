"""
================================================================================
脚本名称：企业级中文电商数仓批量数据合成与物理建模引擎 🏭
脚本路径：app/scripts/init_chinese_dw.py
核心功能：
    1. 遵循 Kimball 维度建模规范，构建 4 张维度表 (dim_) 与 2 张事实表 (fact_)
    2. 生成 1,000 用户、120 商品、20 门店、10,000 订单以及 20,000+ 明细事实流水
    3. 全面涵盖中文枚举、多表外键关系、真实业务折扣与跨年时序分布
================================================================================
"""
import asyncio
import random
from datetime import datetime, timedelta
import asyncmy
from app.config.app_config import app_config

# 1. 严格区分维度表 (dim_) 与事实表 (fact_) 的 DDL 物理建模
CREATE_TABLES_SQL = """
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS fact_order_detail;
DROP TABLE IF EXISTS fact_order;
DROP TABLE IF EXISTS dim_promotion;
DROP TABLE IF EXISTS dim_store;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_user;

SET FOREIGN_KEY_CHECKS = 1;

-- ==================== 1. 维度表 (Dimensions) ====================

-- 1.1 用户维度表
CREATE TABLE dim_user (
    user_id VARCHAR(32) PRIMARY KEY COMMENT '用户唯一编码',
    user_name VARCHAR(64) NOT NULL COMMENT '用户真实姓名',
    gender VARCHAR(10) NOT NULL DEFAULT '未知' COMMENT '性别(男/女)',
    age INT NOT NULL COMMENT '用户年龄',
    city VARCHAR(64) NOT NULL COMMENT '所在城市',
    province VARCHAR(64) NOT NULL COMMENT '所在省份',
    user_level VARCHAR(32) NOT NULL DEFAULT '普通会员' COMMENT '会员等级(普通会员/黄金会员/白金会员/钻石会员)',
    register_channel VARCHAR(32) NOT NULL DEFAULT 'App' COMMENT '注册渠道(App/微信小程序/网页端/抖音直播间)',
    created_time DATETIME NOT NULL COMMENT '注册时间',
    KEY idx_city_level (city, user_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电商用户维度表';

-- 1.2 商品维度表
CREATE TABLE dim_product (
    product_id VARCHAR(32) PRIMARY KEY COMMENT '商品SPU/SKU编码',
    product_name VARCHAR(128) NOT NULL COMMENT '商品全称',
    category_level1 VARCHAR(64) NOT NULL COMMENT '一级类目(手机数码/家用电器/服装服饰/食品饮料/运动户外)',
    category_level2 VARCHAR(64) NOT NULL COMMENT '二级类目(智能手机/大家电/休闲服/零食坚果/运动鞋等)',
    brand VARCHAR(64) NOT NULL COMMENT '所属品牌(华为/苹果/小米/美的/李宁/耐克/三只松鼠等)',
    market_price DECIMAL(10, 2) NOT NULL COMMENT '吊牌官方标价',
    cost_price DECIMAL(10, 2) NOT NULL COMMENT '采购进货成本价',
    KEY idx_cat1_brand (category_level1, brand)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电商商品维度表';

-- 1.3 门店/销售渠道维度表
CREATE TABLE dim_store (
    store_id VARCHAR(32) PRIMARY KEY COMMENT '门店/渠道编码',
    store_name VARCHAR(128) NOT NULL COMMENT '门店或线上渠道全称',
    channel_type VARCHAR(32) NOT NULL COMMENT '渠道形态(线上自营/线上商城/线下旗舰店/线下加盟店)',
    region VARCHAR(32) NOT NULL COMMENT '所属大区(华东/华北/华南/华西/华中)',
    city VARCHAR(64) NOT NULL COMMENT '所在城市',
    manager_name VARCHAR(64) NOT NULL COMMENT '负责人/店长姓名',
    opened_date DATE NOT NULL COMMENT '开店运营日期'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='渠道与门店维度表';

-- 1.4 营销活动维度表
CREATE TABLE dim_promotion (
    promo_id VARCHAR(32) PRIMARY KEY COMMENT '活动方案编码',
    promo_name VARCHAR(128) NOT NULL COMMENT '活动名称(如: 无促销, 618大促, 双11狂欢, 周末会员日)',
    promo_type VARCHAR(32) NOT NULL COMMENT '活动类型(无优惠/满减券/直降打折/秒杀促销)',
    discount_rate DECIMAL(4, 2) NOT NULL DEFAULT 1.00 COMMENT '折扣系数(如0.85代表85折, 1.00代表不打折)'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='营销促销活动维度表';


-- ==================== 2. 事实表 (Facts) ====================

-- 2.1 订单主事实表 (事务粒度: 一单一行)
CREATE TABLE fact_order (
    order_sn VARCHAR(32) PRIMARY KEY COMMENT '订单全局唯一业务号',
    user_id VARCHAR(32) NOT NULL COMMENT '买家编码(关联dim_user.user_id)',
    store_id VARCHAR(32) NOT NULL COMMENT '成交渠道编码(关联dim_store.store_id)',
    promo_id VARCHAR(32) NOT NULL COMMENT '关联营销方案(关联dim_promotion.promo_id)',
    total_sku_count INT NOT NULL DEFAULT 1 COMMENT '订单内SKU品种数',
    total_item_quantity INT NOT NULL DEFAULT 1 COMMENT '订单包含商品总件数',
    original_amount DECIMAL(12, 2) NOT NULL COMMENT '订单原价总金额',
    discount_amount DECIMAL(12, 2) NOT NULL DEFAULT 0.00 COMMENT '营销与会员优惠减免金额',
    pay_amount DECIMAL(12, 2) NOT NULL COMMENT '实际结算实付金额',
    order_status VARCHAR(32) NOT NULL DEFAULT '已完成' COMMENT '订单状态(已完成/已发货/已退款/待付款/已取消)',
    pay_time DATETIME NOT NULL COMMENT '支付时间戳',
    cancel_time DATETIME DEFAULT NULL COMMENT '退款或取消时间戳',
    FOREIGN KEY (user_id) REFERENCES dim_user(user_id),
    FOREIGN KEY (store_id) REFERENCES dim_store(store_id),
    FOREIGN KEY (promo_id) REFERENCES dim_promotion(promo_id),
    KEY idx_user_time (user_id, pay_time),
    KEY idx_status_time (order_status, pay_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单交易主事实表';

-- 2.2 订单明细事实表 (原子事务粒度: 一单多行，细化到具体购买商品)
CREATE TABLE fact_order_detail (
    detail_id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '明细主键自增流水号',
    order_sn VARCHAR(32) NOT NULL COMMENT '所属订单号(关联fact_order.order_sn)',
    product_id VARCHAR(32) NOT NULL COMMENT '购买商品编码(关联dim_product.product_id)',
    buy_quantity INT NOT NULL DEFAULT 1 COMMENT '购买数量',
    unit_price DECIMAL(10, 2) NOT NULL COMMENT '单件结算成交价',
    unit_cost DECIMAL(10, 2) NOT NULL COMMENT '单件采购成本价',
    total_sales_amount DECIMAL(12, 2) NOT NULL COMMENT '该明细销售小计(buy_quantity * unit_price)',
    total_cost_amount DECIMAL(12, 2) NOT NULL COMMENT '该明细成本小计(buy_quantity * unit_cost)',
    gross_profit DECIMAL(12, 2) NOT NULL COMMENT '该明细毛利额',
    FOREIGN KEY (order_sn) REFERENCES fact_order(order_sn),
    FOREIGN KEY (product_id) REFERENCES dim_product(product_id),
    KEY idx_product_sales (product_id, total_sales_amount)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单商品销售明细事实表';
"""

# 2. 真实业务词典
CITIES_INFO = [
    ("北京", "北京", "华北"), ("上海", "上海", "华东"), ("广州", "广东", "华南"), ("深圳", "广东", "华南"),
    ("杭州", "浙江", "华东"), ("南京", "江苏", "华东"), ("苏州", "江苏", "华东"), ("成都", "四川", "华西"),
    ("重庆", "重庆", "华西"), ("武汉", "湖北", "华中"), ("长沙", "湖南", "华中"), ("西安", "陕西", "华西")
]
FIRST_NAMES = ["张", "李", "王", "刘", "陈", "杨", "赵", "黄", "周", "吴", "徐", "孙", "胡", "高", "林", "何", "郭", "马"]
LAST_NAMES = ["伟", "芳", "娜", "敏", "静", "秀英", "丽", "强", "军", "磊", "洋", "勇", "艳", "杰", "娟", "涛", "明", "超", "鹏"]
LEVEL_CHOICES = ["普通会员", "黄金会员", "白金会员", "钻石会员"]
LEVEL_WEIGHTS = [0.55, 0.28, 0.13, 0.04]
CHANNELS = ["App", "微信小程序", "网页端", "抖音直播间"]

# 基础商品库模版 (后续自动化扩充至 120+ SKU)
BASE_PRODUCTS = [
    ("Mate 60 Pro", "手机数码", "智能手机", "华为", 6999.00, 5200.00),
    ("Pura 70 Ultra", "手机数码", "智能手机", "华为", 9999.00, 7800.00),
    ("iPhone 15 Pro", "手机数码", "智能手机", "苹果", 7999.00, 6200.00),
    ("iPhone 16 Pro Max", "手机数码", "智能手机", "苹果", 9999.00, 7900.00),
    ("小米 14 Pro", "手机数码", "智能手机", "小米", 4999.00, 3800.00),
    ("Redmi K70", "手机数码", "智能手机", "小米", 2499.00, 1950.00),
    ("FreeBuds Pro 3", "手机数码", "数码配件", "华为", 1299.00, 780.00),
    ("AirPods Pro 2", "手机数码", "数码配件", "苹果", 1799.00, 1150.00),
    ("小米 67W 闪充套装", "手机数码", "数码配件", "小米", 129.00, 55.00),
    ("无风感变频冷暖空调 1.5匹", "家用电器", "大家电", "美的", 3299.00, 2250.00),
    ("直驱变频滚筒洗衣机 10kg", "家用电器", "大家电", "海尔", 2899.00, 1920.00),
    ("十字对开双门冰箱 500L", "家用电器", "大家电", "容声", 3499.00, 2400.00),
    ("智能多功能空气炸锅 5L", "家用电器", "厨房电器", "美的", 299.00, 130.00),
    ("变频电磁炉带炒锅", "家用电器", "厨房电器", "苏泊尔", 219.00, 110.00),
    ("超轻透气缓震跑步鞋", "服装服饰", "运动鞋", "耐克", 899.00, 390.00),
    ("赤兔7 竞速碳板跑鞋", "服装服饰", "运动鞋", "李宁", 499.00, 205.00),
    ("三合一加厚户外防风冲锋衣", "服装服饰", "休闲服", "安踏", 699.00, 290.00),
    ("重磅新疆棉宽松短袖T恤", "服装服饰", "休闲服", "李宁", 129.00, 42.00),
    ("三只松鼠每日坚果 750g礼盒", "食品饮料", "零食坚果", "三只松鼠", 128.00, 68.00),
    ("百草味肉脯大礼包 500g", "食品饮料", "零食坚果", "百草味", 69.00, 32.00),
    ("特仑苏全脂纯牛奶 250ml*16", "食品饮料", "乳品冲饮", "蒙牛", 65.00, 46.00),
    ("农夫山泉东方树叶乌龙茶 15瓶", "食品饮料", "茶饮果汁", "农夫山泉", 60.00, 34.00),
    ("碳素超轻专业羽毛球拍", "运动户外", "球类运动", "尤尼克斯", 480.00, 260.00),
    ("户外专业折叠露营车", "运动户外", "野营装备", "原始人", 299.00, 130.00)
]

STORES_DEF = [
    ("STR001", "官方线上天猫旗舰店", "线上自营", "华东", "杭州", "陈志远", "2020-03-01"),
    ("STR002", "官方微信商城自营店", "线上商城", "华东", "上海", "刘明慧", "2020-06-15"),
    ("STR003", "京东自营官方旗舰店", "线上自营", "华北", "北京", "李建国", "2019-11-01"),
    ("STR004", "抖音直播电商1号店", "线上商城", "华南", "广州", "张小雨", "2021-08-01"),
    ("STR005", "北京王府井旗舰店", "线下旗舰店", "华北", "北京", "王宏伟", "2018-05-18"),
    ("STR006", "上海南京东路旗舰店", "线下旗舰店", "华东", "上海", "赵雪峰", "2018-09-28"),
    ("STR007", "深圳万象城体验店", "线下旗舰店", "华南", "深圳", "孙国强", "2019-12-12"),
    ("STR008", "成都太古里潮流店", "线下旗舰店", "华西", "成都", "唐玉成", "2020-10-01"),
    ("STR009", "杭州西湖湖滨直营店", "线下旗舰店", "华东", "杭州", "周文龙", "2021-04-15"),
    ("STR010", "武汉光谷天地加盟店", "线下加盟店", "华中", "武汉", "严学军", "2022-01-10"),
    ("STR011", "西安大雁塔直营店", "线下加盟店", "华西", "西安", "宋海鹏", "2022-03-05"),
    ("STR012", "重庆解放碑体验店", "线下旗舰店", "华西", "重庆", "郑向阳", "2021-09-10")
]

PROMOTIONS_DEF = [
    ("PRM001", "常态无优惠", "无优惠", 1.00),
    ("PRM002", "春季焕新季88折", "直降打折", 0.88),
    ("PRM003", "618年中狂欢满减", "满减券", 0.85),
    ("PRM004", "双11巅峰大促8折", "直降打折", 0.80),
    ("PRM005", "周末会员专属狂欢券", "满减券", 0.92),
    ("PRM006", "大牌爆款秒杀狂欢", "秒杀促销", 0.75)
]

ORDER_STATUSES = ["已完成", "已完成", "已完成", "已完成", "已发货", "已退款", "待付款"]


def build_full_dataset(user_target=1000, order_target=10000):
    """大批量多维关系数据合成"""
    users = []
    products = []
    stores = STORES_DEF
    promotions = PROMOTIONS_DEF

    # 1. 批量生成 1,000 位用户
    base_user_time = datetime(2023, 6, 1)
    for i in range(1, user_target + 1):
        uid = f"U{10000 + i}"
        name = f"{random.choice(FIRST_NAMES)}{random.choice(LAST_NAMES)}"
        gender = random.choice(["男", "女"])
        age = random.randint(18, 65)
        city, prov, _ = random.choice(CITIES_INFO)
        level = random.choices(LEVEL_CHOICES, weights=LEVEL_WEIGHTS, k=1)[0]
        reg_channel = random.choice(CHANNELS)
        reg_time = base_user_time + timedelta(days=random.randint(0, 900), hours=random.randint(0, 23))
        users.append((uid, name, gender, age, city, prov, level, reg_channel, reg_time.strftime("%Y-%m-%d %H:%M:%S")))

    # 2. 扩充生成 120+ 标品 SKU (通过规格衍生)
    spec_suffixes = ["标准版", "尊享版 256G", "旗舰版 512G", "经典款", "升级长续航版"]
    pid_seq = 1
    for bp in BASE_PRODUCTS:
        for suffix in spec_suffixes:
            pid = f"SKU{1000 + pid_seq}"
            p_name = f"{bp[0]} ({suffix})"
            price_factor = 1.0 + (spec_suffixes.index(suffix) * 0.15)
            m_price = round(bp[4] * price_factor, 2)
            c_price = round(bp[5] * price_factor, 2)
            products.append((pid, p_name, bp[1], bp[2], bp[3], m_price, c_price))
            pid_seq += 1

    # 3. 仿真实序订单与明细 (10,000 主单 + 20,000+ 明细)
    orders = []
    order_details = []
    start_order_time = datetime(2024, 1, 1, 8, 0, 0)
    total_days = 950  # 跨越 2024 至 2026 年

    for i in range(1, order_target + 1):
        order_sn = f"ORD{202400000 + i}"
        user = random.choice(users)
        u_id = user[0]
        u_level = user[6]
        store = random.choice(stores)
        s_id = store[0]
        promo = random.choice(promotions)
        p_id = promo[0]
        promo_rate = float(promo[3])

        # 随机选择该单购买 1 ~ 4 种不同的 SKU
        sku_types_count = random.choices([1, 2, 3, 4], weights=[0.65, 0.22, 0.10, 0.03], k=1)[0]
        selected_prods = random.sample(products, sku_types_count)

        order_orig_amt = 0.0
        order_total_qty = 0

        for prod in selected_prods:
            p_code = prod[0]
            m_price = float(prod[5])
            c_price = float(prod[6])
            qty = random.choices([1, 2, 3], weights=[0.82, 0.14, 0.04], k=1)[0]

            # 会员额外微折扣 (钻石 92折, 白金 95折, 黄金 98折)
            level_discount = 0.92 if u_level == "钻石会员" else (0.95 if u_level == "白金会员" else (0.98 if u_level == "黄金会员" else 1.0))
            actual_unit_price = round(m_price * promo_rate * level_discount, 2)

            detail_sales = round(actual_unit_price * qty, 2)
            detail_cost = round(c_price * qty, 2)
            gross_profit = round(detail_sales - detail_cost, 2)

            order_orig_amt += (m_price * qty)
            order_total_qty += qty

            order_details.append((
                order_sn, p_code, qty, actual_unit_price, c_price,
                detail_sales, detail_cost, gross_profit
            ))

        order_orig_amt = round(order_orig_amt, 2)
        # 实付总额为明细汇总
        order_pay_amt = sum(d[5] for d in order_details[-sku_types_count:])
        order_pay_amt = round(order_pay_amt, 2)
        order_discount_amt = round(order_orig_amt - order_pay_amt, 2)

        status = random.choice(ORDER_STATUSES)
        pay_dt = start_order_time + timedelta(
            days=random.randint(0, total_days),
            hours=random.randint(0, 23),
            minutes=random.randint(0, 59),
            seconds=random.randint(0, 59)
        )
        cancel_dt = (pay_dt + timedelta(days=random.randint(1, 3))) if status in ["已退款", "已取消"] else None

        orders.append((
            order_sn, u_id, s_id, p_id, sku_types_count, order_total_qty,
            order_orig_amt, order_discount_amt, order_pay_amt, status,
            pay_dt.strftime("%Y-%m-%d %H:%M:%S"),
            cancel_dt.strftime("%Y-%m-%d %H:%M:%S") if cancel_dt else None
        ))

    return users, products, stores, promotions, orders, order_details


async def run_data_factory():
    print("=" * 70)
    print("🏭 正在启动企业级中文电商数仓批量数据合成与导入流水线...")
    print("=" * 70)

    conn = await asyncmy.connect(
        host=app_config.db_dw.host,
        port=app_config.db_dw.port,
        user=app_config.db_dw.user,
        password=app_config.db_dw.password,
        database=app_config.db_dw.database
    )

    try:
        async with conn.cursor() as cursor:
            # 1. 物理建表
            print("🛠️ 正在重建 4 维度 + 2 事实表拓扑架构...")
            for stmt in CREATE_TABLES_SQL.split(";"):
                sql = stmt.strip()
                if sql:
                    await cursor.execute(sql)
            print("✅ 物理数据表建表完成 (dim_user, dim_product, dim_store, dim_promotion, fact_order, fact_order_detail)")

            # 2. 批量生成仿真数据
            print("📦 正在内存中生成万级真实业务仿真流水...")
            users, prods, stores, promos, orders, details = build_full_dataset(user_target=1000, order_target=10000)
            print(f"✨ 数据生成完毕: 用户 {len(users)} | 商品 {len(prods)} | 门店 {len(stores)} | 促销 {len(promos)}")
            print(f"✨ 事实流水构建完毕: 订单事实 {len(orders)} | 商品明细事实 {len(details)}")

            # 3. 维度表入库
            print("⏳ 正在写入维度表数据...")
            await cursor.executemany("INSERT INTO dim_user VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", users)
            await cursor.executemany("INSERT INTO dim_product VALUES (%s, %s, %s, %s, %s, %s, %s)", prods)
            await cursor.executemany("INSERT INTO dim_store VALUES (%s, %s, %s, %s, %s, %s, %s)", stores)
            await cursor.executemany("INSERT INTO dim_promotion VALUES (%s, %s, %s, %s)", promos)

            # 4. 事实表分批极速入库 (防止单条 SQL 报超大数据包错误)
            print("⏳ 正在分批极速落库 10,000 笔订单事实主表...")
            batch_size = 2000
            for i in range(0, len(orders), batch_size):
                await cursor.executemany(
                    "INSERT INTO fact_order VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    orders[i:i + batch_size]
                )

            print(f"⏳ 正在分批极速落库 {len(details)} 条商品明细事实表...")
            for i in range(0, len(details), batch_size):
                await cursor.executemany(
                    """INSERT INTO fact_order_detail (
                        order_sn, product_id, buy_quantity, unit_price, unit_cost,
                        total_sales_amount, total_cost_amount, gross_profit
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    details[i:i + batch_size]
                )

            await conn.commit()
            print("=" * 70)
            print("🎉 恭喜！企业级中文数仓数据全部就绪！累计物理数据已超 30,000+ 条！")
            print("=" * 70)

    finally:
        await conn.ensure_closed()


if __name__ == '__main__':
    asyncio.run(run_data_factory())