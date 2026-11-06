"""跨 Agent 共享能力。"""


def __getattr__(name: str):
    if name == "verify_metric_change":
        from app.agent.anomaly_agent.common.change_inspection import verify_metric_change

        return verify_metric_change
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["verify_metric_change"]
