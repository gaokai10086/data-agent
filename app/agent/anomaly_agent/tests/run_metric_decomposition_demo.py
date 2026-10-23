"""离线演示：python -m app.agent.anomaly_agent.tests.run_metric_decomposition_demo"""
import json
from decimal import Decimal

from app.agent.anomaly_agent.handlers.metric_decomposition import calculate_metric_decomposition
from app.agent.anomaly_agent.tests.test_metric_decomposition_tool import context_with_amounts


def main():
    # 示例数字，仅演示公式；真实数据由 execute_metric_decomposition 查两期订单数。
    result = calculate_metric_decomposition(context_with_amounts(), Decimal(800), Decimal(1000))
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
