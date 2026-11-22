"""业务诊断的可复用 Schema、证据纯函数与受控范围工具。"""

__all__ = ["BusinessDiagnosisInput", "DiagnosisResult"]


def __getattr__(name: str):
    if name in {"BusinessDiagnosisInput", "DiagnosisResult"}:
        from app.agent.anomaly_agent.Main_RootCause_Agent.diagnosis_support.schemas import (
            BusinessDiagnosisInput, DiagnosisResult,
        )
        return {"BusinessDiagnosisInput": BusinessDiagnosisInput, "DiagnosisResult": DiagnosisResult}[name]
    raise AttributeError(name)
