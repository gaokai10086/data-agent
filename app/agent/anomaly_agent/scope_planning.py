"""从成功观察解析下钻范围；以范围＋分组字段识别重复动作。"""
from app.agent.anomaly_agent.handlers.overall_comparison import calculate_change
from app.agent.anomaly_agent.schemas import (
    AnalysisAction, AnalysisContext, AnalysisObservation, FilterCondition,
)
from app.agent.anomaly_agent.skills.base import fixed_dimension_columns


MAX_SCOPE_DEPTH = 2
VISIBLE_GROUPS = 10


def scope_depth(action: AnalysisAction, observations: list[AnalysisObservation]) -> int:
    if action.scope_ref is None:
        return 0
    for index, observation in enumerate(observations):
        if observation.observation_id == action.scope_ref.observation_id:
            return 1 + scope_depth(observation.action, observations[:index])
    raise ValueError("下钻引用不存在或不是先前结果")


def resolve_scope(root: AnalysisContext, action: AnalysisAction,
                  observations: list[AnalysisObservation]) -> AnalysisContext:
    """沿父观察恢复完整条件链；不信任模型提供任何数值或新增条件。"""
    if action.scope_ref is None:
        return root
    if scope_depth(action, observations) > MAX_SCOPE_DEPTH:
        raise ValueError("超过最大下钻深度")
    ref = action.scope_ref
    for index, parent in enumerate(observations):
        if parent.observation_id != ref.observation_id:
            continue
        if parent.status != "ok" or parent.action.action != "dimension_contribution":
            raise ValueError("只能下钻成功的维度查询结果")
        matches = [g for g in parent.groups[:VISIBLE_GROUPS] if g.group_value == ref.group_value]
        if len(matches) != 1:
            raise ValueError("下钻分组不在已展示的成功结果中")
        group = matches[0]
        parent_context = resolve_scope(root, parent.action, observations[:index])
        source_column = parent.action.dimension_column
        if source_column not in {d.column for d in root.metric.dimensions}:
            raise ValueError("父观察的维度不属于当前指标")
        if source_column in fixed_dimension_columns(parent_context):
            raise ValueError("父观察不能按已固定的维度分组")
        if not group.current_value.is_finite() or not group.comparison_value.is_finite():
            raise ValueError("下钻基准数值不可用")
        request = parent_context.request.model_copy(update={
            "filters": [*parent_context.request.filters,
                        FilterCondition(dimension=source_column, value=group.group_value)],
        })
        amount, rate = calculate_change(group.current_value, group.comparison_value)
        comparison = parent_context.comparison.model_copy(update={
            "current_value": group.current_value, "comparison_value": group.comparison_value,
            "change_amount": amount, "change_rate": rate,
        })
        # 总体是否继续已核实；子范围允许上升、下降或不变，不再次套用下降 Skill。
        return AnalysisContext(request=request, metric=root.metric, comparison=comparison,
                               verification=root.verification)
    raise ValueError("下钻引用不存在")


def action_key(context: AnalysisContext, column: str):
    return (column, tuple(sorted((f.dimension, f.operator, f.value) for f in context.request.filters)))


def available_actions(root: AnalysisContext, dimensions: list[str],
                      observations: list[AnalysisObservation]) -> list[AnalysisAction]:
    """合法动作菜单：根范围和已成功分组的下钻；错误动作也计入尝试历史。"""
    tried = {action_key(resolve_scope(root, o.action, observations[:index]), o.action.dimension_column)
             for index, o in enumerate(observations)}
    scopes = [None]
    for observation in observations:
        if observation.status == "ok":
            scopes.extend({"observation_id": observation.observation_id, "group_value": g.group_value}
                          for g in observation.groups[:VISIBLE_GROUPS] if g.group_value is not None)
    actions, seen = [], set()
    for ref in scopes:
        sample = AnalysisAction(action="dimension_contribution", dimension_column=dimensions[0],
                                scope_ref=ref, reason="候选") if dimensions else None
        if sample is None:
            break
        try:
            scoped = resolve_scope(root, sample, observations)
            fixed = fixed_dimension_columns(scoped)
        except ValueError:
            continue
        for column in dimensions:
            key = action_key(scoped, column)
            if column not in fixed and key not in tried and key not in seen:
                seen.add(key)
                actions.append(sample.model_copy(update={"dimension_column": column}))
    return actions


def validate_action(root: AnalysisContext, action: AnalysisAction, dimensions: list[str],
                    observations: list[AnalysisObservation]) -> AnalysisContext:
    scoped = resolve_scope(root, action, observations)
    allowed = available_actions(root, dimensions, observations)
    if not any(item.dimension_column == action.dimension_column and item.scope_ref == action.scope_ref
               for item in allowed):
        raise ValueError("维度、下钻范围或重复动作不合法")
    return scoped
