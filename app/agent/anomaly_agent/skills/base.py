"""所有业务分析 Skill 的统一接口与输出校验。"""
from abc import ABC, abstractmethod

from app.agent.anomaly_agent.schemas import AnalysisContext, AnalysisStrategy, ExcludedDimension


def _direct_fixed_columns(context: AnalysisContext) -> set[str]:
    """用 Metadata 的名称、别名或完整字段解析等值筛选；不猜测物理字段。

    同名维度有歧义时停止制定策略，避免错误地排除或选择维度。
    """
    fixed = set()
    for condition in context.request.filters:
        expression = condition.dimension.strip().casefold()
        matches = {
            dimension.column for dimension in context.metric.dimensions
            if expression in {
                name.strip().casefold()
                for name in (dimension.name, dimension.column, *dimension.aliases)
            }
        }
        if len(matches) != 1:
            raise ValueError(f"筛选维度无法唯一对应 Metadata：{condition.dimension}")
        fixed.update(matches)
    return fixed


def excluded_dimensions(context: AnalysisContext) -> list[ExcludedDimension]:
    """由单值等值筛选沿已发布依赖传播；不反向推断，不猜地理关系。"""
    direct = _direct_fixed_columns(context)
    dimensions = {item.column: item for item in context.metric.dimensions}
    excluded = {
        column: ExcludedDimension(dimension_column=column, reason="direct_filter",
                                  detail="该维度已被本次单值等值筛选固定")
        for column in dimensions if column in direct
    }
    pending = list(excluded)
    while pending:
        source = pending.pop(0)
        for target in dimensions[source].determines:
            if target not in excluded:
                excluded[target] = ExcludedDimension(
                    dimension_column=target, reason="dependency_fixed", determined_by=source,
                    detail=f"已固定维度 {source} 按 Metadata 可唯一确定该维度",
                )
                pending.append(target)
    # 本 Skill 分析两个完整月份的总体变化，不按原始时间戳拆成大量细分组。
    time_column = context.metric.time_column
    if time_column:
        qualified = time_column if "." in time_column else f"{context.metric.fact_table}.{time_column}"
        if qualified in dimensions and qualified not in excluded:
            excluded[qualified] = ExcludedDimension(
                dimension_column=qualified, reason="period_scope",
                detail="默认时间字段已用于两个月份的范围限定，当前维度贡献方法不再按时间戳分组",
            )
    return list(excluded.values())


def fixed_dimension_columns(context: AnalysisContext) -> set[str]:
    return {item.dimension_column for item in excluded_dimensions(context)}


def validate_strategy(context: AnalysisContext, strategy: AnalysisStrategy) -> None:
    """在 Skill 边界校验输出，不允许下一节点执行越界或重复的计划。"""
    if context.verification.decision != "continue":
        raise ValueError("变化核实未通过，不能制定后续策略")
    if strategy.request_id != context.request.request_id:
        raise ValueError("策略与当前请求不一致")
    if strategy.status != "ready":
        return
    allowed = {dimension.column for dimension in context.metric.dimensions}
    fixed = fixed_dimension_columns(context)
    for step in strategy.steps:
        if step.dimension_column not in allowed:
            raise ValueError(f"分析维度不属于当前指标：{step.dimension_column}")
        if step.dimension_column in fixed:
            raise ValueError(f"分析维度已被单值筛选固定：{step.dimension_column}")


class BaseAnalysisSkill(ABC):
    """子类只负责适用条件与分析方法，共享输入输出契约。"""

    skill_id: str
    version: str

    @abstractmethod
    def supports(self, context: AnalysisContext) -> bool:
        """判断是否适用于当前请求及已核实的实际变化。"""

    @abstractmethod
    async def build_strategy(self, context: AnalysisContext) -> AnalysisStrategy:
        """制定计划；不生成 SQL，不查库，不重新计算总体差额。"""
