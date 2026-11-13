"""独立的维度归因 Agent（单维度工具阶段）。"""

__all__ = ["dimension_attribution_graph", "AttributionResult", "DimensionAttributionInput"]


def __getattr__(name: str):
    if name == "dimension_attribution_graph":
        from app.agent.anomaly_agent.DimensionAttribution_Agent.graph import dimension_attribution_graph
        return dimension_attribution_graph
    if name in {"AttributionResult", "DimensionAttributionInput"}:
        from app.agent.anomaly_agent.DimensionAttribution_Agent import schemas
        return getattr(schemas, name)
    raise AttributeError(name)
