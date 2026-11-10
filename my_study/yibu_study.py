from pydantic import BaseModel

"""
{
    "metric_name": "sales_amount",
    "current_month": "2025-07",
    "previous_month": "2025-06"
}
1.导入 BaseModel。
2.定义一个继承 BaseModel 的类 AnalysisRequest。
3.定义三个必填字符串字段：metric_name、current_month、previous_month。
4.创建一个 AnalysisRequest 实例，并打印它的 metric_name。
"""

class AnalysisRequest(BaseModel):
    metric_name: str
    current_month: str
    previous_month: str

request = AnalysisRequest(
    metric_name="sales_amount",
    current_month="2025-07"
)

print(request.metric_name)