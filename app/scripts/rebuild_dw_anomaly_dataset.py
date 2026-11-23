"""Rebuild the local DW with deterministic, auditable anomaly-analysis data.

The script backs up the current DW first, then recreates only the configured DW
database. Meta is never connected to or modified. Ground truth is written to a
local JSON file and is deliberately not inserted into DW or Meta.
"""
from __future__ import annotations

import asyncio, json, random
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import asyncmy

from app.config.app_config import app_config

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "diagnostics" / "dw_backups"
GROUND_TRUTH = ROOT / "diagnostics" / "dw_anomaly_ground_truth.json"
SEED = 20261123
Q = Decimal("0.01")

DDL = """
SET FOREIGN_KEY_CHECKS=0;
DROP TABLE IF EXISTS fact_order_detail;
DROP TABLE IF EXISTS fact_order;
DROP TABLE IF EXISTS dim_promotion;
DROP TABLE IF EXISTS dim_store;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_user;
SET FOREIGN_KEY_CHECKS=1;
CREATE TABLE dim_user (
 user_id VARCHAR(32) PRIMARY KEY, user_name VARCHAR(64) NOT NULL, gender VARCHAR(10) NOT NULL,
 age INT NOT NULL, city VARCHAR(64) NOT NULL, province VARCHAR(64) NOT NULL,
 user_level VARCHAR(32) NOT NULL, register_channel VARCHAR(32) NOT NULL, created_time DATETIME NOT NULL,
 KEY idx_city_level(city,user_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户维度';
CREATE TABLE dim_product (
 product_id VARCHAR(32) PRIMARY KEY, product_name VARCHAR(128) NOT NULL, category_level1 VARCHAR(64) NOT NULL,
 category_level2 VARCHAR(64) NOT NULL, brand VARCHAR(64) NOT NULL, market_price DECIMAL(12,2) NOT NULL,
 cost_price DECIMAL(12,2) NOT NULL, KEY idx_cat1_brand(category_level1,brand)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品维度';
CREATE TABLE dim_store (
 store_id VARCHAR(32) PRIMARY KEY, store_name VARCHAR(128) NOT NULL, channel_type VARCHAR(32) NOT NULL,
 region VARCHAR(32) NOT NULL, city VARCHAR(64) NOT NULL, manager_name VARCHAR(64) NOT NULL,
 opened_date DATE NOT NULL, KEY idx_channel_region(channel_type,region), KEY idx_region_city(region,city)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='渠道门店维度';
CREATE TABLE dim_promotion (
 promo_id VARCHAR(32) PRIMARY KEY, promo_name VARCHAR(128) NOT NULL, promo_type VARCHAR(32) NOT NULL,
 discount_rate DECIMAL(5,2) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='促销维度';
CREATE TABLE fact_order (
 order_sn VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32) NOT NULL, store_id VARCHAR(32) NOT NULL,
 promo_id VARCHAR(32) NOT NULL, total_sku_count INT NOT NULL, total_item_quantity INT NOT NULL,
 original_amount DECIMAL(14,2) NOT NULL, discount_amount DECIMAL(14,2) NOT NULL,
 pay_amount DECIMAL(14,2) NOT NULL, order_status VARCHAR(32) NOT NULL DEFAULT '已完成',
 pay_time DATETIME NOT NULL, cancel_time DATETIME NULL, scenario_id VARCHAR(64) NULL,
 FOREIGN KEY(user_id) REFERENCES dim_user(user_id), FOREIGN KEY(store_id) REFERENCES dim_store(store_id),
 FOREIGN KEY(promo_id) REFERENCES dim_promotion(promo_id), KEY idx_status_time(order_status,pay_time),
 KEY idx_store_time(store_id,pay_time), KEY idx_scenario(scenario_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单事实';
CREATE TABLE fact_order_detail (
 detail_id BIGINT AUTO_INCREMENT PRIMARY KEY, order_sn VARCHAR(32) NOT NULL, product_id VARCHAR(32) NOT NULL,
 buy_quantity INT NOT NULL, unit_price DECIMAL(12,2) NOT NULL, unit_cost DECIMAL(12,2) NOT NULL,
 total_sales_amount DECIMAL(14,2) NOT NULL, total_cost_amount DECIMAL(14,2) NOT NULL,
 gross_profit DECIMAL(14,2) NOT NULL, FOREIGN KEY(order_sn) REFERENCES fact_order(order_sn),
 FOREIGN KEY(product_id) REFERENCES dim_product(product_id), KEY idx_product_sales(product_id,total_sales_amount)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细事实';
"""

CHANNELS = ["线上自营", "线上商城", "线下旗舰店", "线下加盟店"]
REGIONS = ["华东", "华北", "华南", "华西", "华中"]
CITY_BY_REGION = {"华东": ["上海", "杭州", "南京"], "华北": ["北京", "天津"], "华南": ["广州", "深圳"], "华西": ["成都", "西安"], "华中": ["武汉", "长沙"]}
PROMOS = [("PRM001", "常态无优惠", "无优惠", Decimal("1.00")), ("PRM002", "季节焕新折扣", "直降打折", Decimal("0.88")), ("PRM003", "会员满减券", "满减券", Decimal("0.92")), ("PRM004", "大促活动", "直降打折", Decimal("0.80"))]
PRODUCTS = [(f"SKU{i:04d}", f"商品{i:03d}", "手机数码" if i <= 20 else "家用电器" if i <= 40 else "服装服饰", "通用类目", f"品牌{i%8:02d}", Decimal(100+i*10), Decimal(60+i*6)) for i in range(1, 61)]

# Counts and average prices are intentionally controlled.  The product of the
# two values is the expected sales total; the generator rounds only final rows.
MONTH_PLAN = {
    "2026-05": (1200, Decimal("820")), "2026-06": (1180, Decimal("610")),  # price-led
    "2026-07": (2400, Decimal("820")), "2026-08": (1150, Decimal("820")),  # count-led
    "2026-09": (1800, Decimal("760")), "2026-10": (1300, Decimal("590")),  # two-factor
}

def month_list():
    return [f"{year:04d}-{month:02d}" for year in (2025, 2026) for month in range(1, 13) if not (year == 2026 and month > 10)]

def stores():
    rows = []; n = 1
    for channel in CHANNELS:
        for region in REGIONS:
            for city in CITY_BY_REGION[region][:2]:
                rows.append((f"STR{n:03d}", f"{city}{channel}{n}号店", channel, region, city, f"负责人{n}", "2020-01-01")); n += 1
    return rows

def distribute(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    raw = [total*w/sum(weights) for w in weights]; out = [x.quantize(Q, rounding=ROUND_HALF_UP) for x in raw]
    out[-1] += total - sum(out); return out

def build_rows():
    rng = random.Random(SEED); store_rows = stores(); users = [(f"U{i:05d}", f"用户{i}", "未知", 25+i%35, "上海", "上海", "普通会员", "App", "2024-01-01 00:00:00") for i in range(1, 1001)]
    orders=[]; details=[]; ground=[]; oid=1
    for period in month_list():
        count, avg = MONTH_PLAN.get(period, (1600, Decimal("720")))
        channel_weights = [Decimal("0.28"), Decimal("0.24"), Decimal("0.30"), Decimal("0.18")]
        channel_counts = [int(count*w/sum(channel_weights)) for w in channel_weights]; channel_counts[-1] = count-sum(channel_counts[:-1])
        channel_sales = distribute(Decimal(count)*avg, channel_weights)
        period_total = Decimal("0")
        for ci, channel in enumerate(CHANNELS):
            candidates = [s for s in store_rows if s[2] == channel]; amounts = distribute(channel_sales[ci], [Decimal(i+1) for i in range(len(candidates))])
            for si, store in enumerate(candidates):
                n = channel_counts[ci] * (si+1) // sum(range(1,len(candidates)+1)); n = max(1, n)
                if si == len(candidates)-1: n = channel_counts[ci] - sum(max(1, channel_counts[ci]*(x+1)//sum(range(1,len(candidates)+1))) for x in range(len(candidates)-1))
                if n <= 0: continue
                unit = (amounts[si] / n).quantize(Q, rounding=ROUND_HALF_UP); amounts[si] = unit*n
                for j in range(n):
                    order = f"ORD{oid:09d}"; oid += 1; user = users[(oid+j)%len(users)]; promo = PROMOS[(oid+j)%len(PROMOS)]; product = PRODUCTS[(oid+j)%len(PRODUCTS)]
                    pay = unit if j < n-1 else amounts[si] - unit*(n-1); pay = pay.quantize(Q); original=(pay/Decimal("0.88")).quantize(Q); discount=original-pay; day=1+(j%25)
                    ts=f"{period}-{day:02d} 12:00:00"; scenario = "sales_decline_2026_08" if period in ("2026-07","2026-08") else "sales_decline_2026_06" if period in ("2026-05","2026-06") else "sales_decline_2026_10" if period in ("2026-09","2026-10") else "baseline"
                    orders.append((order,user[0],store[0],promo[0],1,1,original,discount,pay,"已完成",ts,None,scenario)); cost=product[6]; details.append((order,product[0],1,pay,cost,pay,cost,pay-cost)); period_total += pay
        scenario_id = "sales_decline_2026_08" if period in ("2026-07", "2026-08") else "sales_decline_2026_06" if period in ("2026-05", "2026-06") else "sales_decline_2026_10" if period in ("2026-09", "2026-10") else "monthly_baseline"
        ground.append({"scenario_id": scenario_id, "period": period, "sales": str(period_total), "orders": count, "avg_order_amount": str((period_total/Decimal(count)).quantize(Q))})
    return users, PRODUCTS, store_rows, PROMOS, orders, details, ground

async def main():
    cfg=app_config.db_dw; BACKUP_DIR.mkdir(parents=True, exist_ok=True); stamp=datetime.now().strftime("%Y%m%d_%H%M%S"); backup=BACKUP_DIR/f"dw_backup_{stamp}.json"
    conn=await asyncmy.connect(host=cfg.host,port=cfg.port,user=cfg.user,password=cfg.password,db=cfg.database,autocommit=False)
    try:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DATABASE()"); target=(await cur.fetchone())[0]
            if target != cfg.database or target.lower() != "dw": raise RuntimeError(f"拒绝操作目标数据库: {target}")
            await cur.execute("SHOW TABLES"); tables=[row[0] for row in await cur.fetchall()]; snapshot={"database":target,"tables":tables,"ddl":{}}
            for table in tables:
                await cur.execute(f"SHOW CREATE TABLE `{table}`"); snapshot["ddl"][table]=(await cur.fetchone())[1]
                await cur.execute(f"SELECT * FROM `{table}`")
                columns=[item[0] for item in cur.description]
                snapshot.setdefault("rows", {})[table] = [dict(zip(columns, row)) for row in await cur.fetchall()]
            backup.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            await cur.execute(DDL)
            users, products, store_rows, promos, orders, details, ground=build_rows()
            await cur.executemany("INSERT INTO dim_user VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", users)
            await cur.executemany("INSERT INTO dim_product VALUES (%s,%s,%s,%s,%s,%s,%s)", products)
            await cur.executemany("INSERT INTO dim_store VALUES (%s,%s,%s,%s,%s,%s,%s)", store_rows)
            await cur.executemany("INSERT INTO dim_promotion VALUES (%s,%s,%s,%s)", promos)
            await cur.executemany("INSERT INTO fact_order VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", orders)
            await cur.executemany("INSERT INTO fact_order_detail(order_sn,product_id,buy_quantity,unit_price,unit_cost,total_sales_amount,total_cost_amount,gross_profit) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", details)
            await conn.commit()
            ground_payload={"seed":SEED,"database":target,"backup":str(backup),"scenarios":ground,"note":"ground truth is local only and not registered in Meta"}; GROUND_TRUTH.write_text(json.dumps(ground_payload,ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({"database":target,"backup":str(backup),"ground_truth":str(GROUND_TRUTH),"tables":tables,"orders":len(orders),"details":len(details)},ensure_ascii=False))
    except Exception:
        await conn.rollback(); raise
    finally: conn.close()

if __name__ == "__main__": asyncio.run(main())
