"""独立的指标拆解 Agent，可由 RootCause 通过受控 Action 调用。"""

__all__ = ["metric_decomposition_graph", "MetricDecompositionInput", "DecompositionResult"]


def __getattr__(name: str):
    if name == "metric_decomposition_graph":
        from app.agent.anomaly_agent.MetricDecomposition_Agent.graph import metric_decomposition_graph
        return metric_decomposition_graph
    if name in {"MetricDecompositionInput", "DecompositionResult"}:
        from app.agent.anomaly_agent.MetricDecomposition_Agent import schemas
        return getattr(schemas, name)
    raise AttributeError(name)
