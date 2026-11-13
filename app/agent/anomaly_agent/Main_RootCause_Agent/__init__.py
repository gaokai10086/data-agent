"""Main_RootCause_Agent 主节点的专属能力。"""

__all__ = ["main_root_cause_agent"]


def __getattr__(name: str):
    if name == "main_root_cause_agent":
        from app.agent.anomaly_agent.Main_RootCause_Agent.graph import main_root_cause_agent
        return main_root_cause_agent
    raise AttributeError(name)
