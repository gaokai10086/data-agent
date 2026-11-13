"""独立业务诊断 Agent；第一阶段只消费已有分析证据。"""

__all__ = ["business_diagnosis_graph", "BusinessDiagnosisInput", "DiagnosisResult"]


def __getattr__(name: str):
    if name == "business_diagnosis_graph":
        from app.agent.anomaly_agent.BusinessDiagnosis_Agent.graph import business_diagnosis_graph
        return business_diagnosis_graph
    if name in {"BusinessDiagnosisInput", "DiagnosisResult"}:
        from app.agent.anomaly_agent.BusinessDiagnosis_Agent.schemas import (
            BusinessDiagnosisInput, DiagnosisResult,
        )
        return {"BusinessDiagnosisInput": BusinessDiagnosisInput, "DiagnosisResult": DiagnosisResult}[name]
    raise AttributeError(name)
