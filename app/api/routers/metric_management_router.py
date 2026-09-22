"""指标管理接口：业务人员提交指标，系统技术校验后直接发布。"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_meta_session
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository
from app.schemas.metric_management import MetricSubmitRequest
from app.service.metric_submission_service import MetricSubmissionService

router = APIRouter(prefix="/api/metric-management", tags=["指标管理"])


@router.post("/metrics")
async def submit_metric(request: MetricSubmitRequest, session=Depends(get_meta_session)) -> dict:
    service = MetricSubmissionService(MetaMySQLRepository(session))
    try:
        metric = await service.submit(request)
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "metric_key": metric.metric_key,
        "name": metric.name,
        "status": metric.status,
        "fact_table": metric.fact_table,
        "measure_column": metric.measure_column,
    }


@router.get("/metrics")
async def list_metrics(
    status: str = Query("published"),
    session=Depends(get_meta_session),
) -> dict:
    if status not in {"published", "draft", "deprecated", "all"}:
        raise HTTPException(status_code=422, detail="status 只支持 published、draft、deprecated 或 all")
    repository = MetaMySQLRepository(session)
    metrics = (
        await repository.list_metrics_by_status(status)
        if status != "all"
        else await repository.list_all_metrics()
    )
    return {
        "items": [
            {
                "metric_key": metric.metric_key,
                "name": metric.name,
                "status": metric.status,
                "description": metric.description,
                "formula": metric.formula,
                "fact_table": metric.fact_table,
                "measure_column": metric.measure_column,
                "aggregation": metric.aggregation,
                "time_column": metric.time_column,
                "filters": metric.filters,
                "alias": metric.alias,
                "dimensions": metric.dimensions,
            }
            for metric in metrics
        ]
    }
