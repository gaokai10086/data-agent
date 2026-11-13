# 销售额异动分析 Skill

## 适用范围

本 Skill 只处理已经由 Orchestrator 判定为 `anomaly_analysis`，并且指标已经确认是已发布“销售额”的问题。

当前优先支持两个明确月份之间的变化分析，尤其是月环比下降问题，例如：

- 为什么 2025 年 7 月销售额比 6 月下降？
- 北京地区 2025 年 7 月销售额比 6 月下降的主要原因是什么？

## 分析流程

1. 确认用户问题中的指标、目标月份、对比月份和筛选范围。
2. 使用已发布的销售额指标定义，不自行改写指标公式。
3. **必须先执行 ChangeInspection**；只有 `InspectionOutcome.status=ready` 才能进入维度归因。
4. `rejected` 直接结束当前分析；`failed` 按技术故障协议处理，不把失败当作业务证据。
5. 变化核验通过后，再从 `MetricContext.dimensions`（其来源是 Meta）选择合法维度。
6. 如果指标存在已发布的拆解公式，可以进行数量和平均金额等因素拆解。
7. 每个结论必须关联可追溯的数据证据。

## 约束

- 不分析没有明确时间范围的问题。
- 不使用未经 Metadata 验证的指标、维度或字段。
- 不让模型直接生成或执行未经校验的 SQL。
- 不把“贡献”直接表述为确定的业务因果关系。
- 没有足够证据时，必须明确说明分析不完整或无法判断。
- 用户明确指定的筛选范围不能被后续下钻动作删除或替换。
- 用户未指定维度时，可以依据 Skill 策略、Meta 维度和已有 Observation 动态选择维度。
- 字段歧义、未发布维度、重复分析范围或预算耗尽时必须停止或请求澄清。
- 分析维度、深度、轮次和查询次数由程序预算约束，Markdown 规则不能替代执行层限制。

## 可使用的能力

- 总体销售额变化核验
- 维度贡献分析（仅在 ChangeInspection.ready 后执行）
- 已发布指标公式拆解（仅在 ChangeInspection.ready 后执行，使用 `metric_decomposition` Action）
- 受控业务诊断（仅在已有归因/拆解证据和 ChangeInspection.ready 后执行，使用 `business_diagnosis` Action）

## 专业 Agent 调度

- `metric_decomposition` 与 `dimension_attribution` 都必须复用同一请求已经验证的 `InspectionOutcome.ready`。
- 用户同时要求渠道贡献和订单量/客单价因素时，Supervisor 可以在两个专业 Agent 之间顺序调度；每个 Agent 只执行一次相同参数的任务。
- 专业 Agent 的 `completed`、`partial`、`failed` 必须通过 Observation 原样保留；partial 只代表已有证据的部分完成，不能表述为完整根因结论。
- `business_diagnosis` 只消费同一请求的真实归因、拆解和变化证据；未经验证的业务假设必须保持 `unverified`，不得把统计贡献直接改写成因果结论。

## DimensionAttributionAgent 策略契约

- 维度来源：`MetricContext.dimensions`；不得在 Skill 中硬编码数据库字段清单。
- 用户明确指定的维度优先，但必须同时存在于 Meta 维度和当前指标允许范围内。
- 未指定维度时，子 Agent 可动态选择尚未分析且证据充分的维度。
- 每次下钻继承原始筛选条件，最多 8 个分析轮次、6 个维度、16 次实际 SQL 查询、3 层深度；双周期查询至少计 2 次，重试按实际执行次数计。
- 已实现双周期维度对比并在内部执行确定性贡献计算与对账；其他未注册工具不得调用。
- 已对账的真实维度成员可以使用 `scoped_metric_decomposition` 验证该成员范围内的订单量与客单价；成员、维度和父级证据必须来自 AttributionPath，不能由模型编造。
- 必须保存两个周期值、净变化贡献、下降贡献、贡献率及对账结果。
- 零基数、缺失值、部分维度上涨和对账失败都必须结构化记录。
- 贡献表示统计关系，不能直接表述为业务因果。

