"""Read-only DW consistency and scenario validation for the rebuilt fixture."""
from __future__ import annotations
import asyncio, json
from decimal import Decimal
import asyncmy
from app.config.app_config import app_config
from app.agent.anomaly_agent.MetricDecomposition_Agent.tools import calculate_symmetric_product_decomposition

async def main():
    c=app_config.db_dw; conn=await asyncmy.connect(host=c.host,port=c.port,user=c.user,password=c.password,db=c.database)
    out={}
    async with conn.cursor() as cur:
        checks={
            "tables":"SHOW TABLES",
            "orphan_details":"SELECT COUNT(*) FROM fact_order_detail d LEFT JOIN fact_order o ON o.order_sn=d.order_sn WHERE o.order_sn IS NULL",
            "amount_mismatch":"SELECT COUNT(*) FROM (SELECT o.order_sn,o.pay_amount,SUM(d.total_sales_amount) s FROM fact_order o JOIN fact_order_detail d ON d.order_sn=o.order_sn GROUP BY o.order_sn,o.pay_amount HAVING ABS(o.pay_amount-s)>0.01) x",
            "month_values":"SELECT DATE_FORMAT(pay_time,'%Y-%m'),COUNT(DISTINCT order_sn),SUM(pay_amount) FROM fact_order WHERE order_status='已完成' GROUP BY DATE_FORMAT(pay_time,'%Y-%m') ORDER BY 1",
            "channel_reconciliation":"SELECT COUNT(*) FROM (SELECT DATE_FORMAT(o.pay_time,'%Y-%m') p,SUM(o.pay_amount) total,SUM(CASE WHEN s.channel_type IS NOT NULL THEN o.pay_amount ELSE 0 END) grouped_total FROM fact_order o JOIN dim_store s ON s.store_id=o.store_id WHERE o.order_status='已完成' GROUP BY DATE_FORMAT(o.pay_time,'%Y-%m') HAVING ABS(total-grouped_total)>0.01) x",
        }
        for key,sql in checks.items(): await cur.execute(sql); out[key]=await cur.fetchall()
        pairs=[("2026-07","2026-08"),("2026-05","2026-06"),("2026-09","2026-10")]
        out["decomposition"]=[]
        for before,after in pairs:
            vals=[]
            for p in (before,after):
                await cur.execute("SELECT COUNT(DISTINCT order_sn),SUM(pay_amount) FROM fact_order WHERE order_status='已完成' AND pay_time >= %s AND pay_time < DATE_ADD(%s,INTERVAL 1 MONTH)",(p+'-01',p+'-01')); vals.append(tuple(Decimal(str(v)) for v in await cur.fetchone()))
            (n0,s0),(n1,s1)=vals; components,reconciliation=calculate_symmetric_product_decomposition(current_metric=s1,comparison_metric=s0,current_count=n1,comparison_count=n0,overall_change=s1-s0)
            out["decomposition"].append({"comparison_period":before,"target_period":after,"sales_before":str(s0),"sales_after":str(s1),"order_count_before":str(n0),"order_count_after":str(n1),"matches":reconciliation.matches,"components":[{"factor":x.factor_key,"contribution":str(x.contribution_amount)} for x in components]})
    conn.close(); print(json.dumps(out,ensure_ascii=False,default=str,indent=2))

if __name__ == '__main__': asyncio.run(main())
