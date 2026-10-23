# 变化核实节点测试

打开 `test_verify_change.py`，在顶部修改 `QUESTION`，然后在 PyCharm 运行。
也可以在项目根目录执行：

```bash
python -m app.agent.anomaly_agent.tests.test_verify_change
```

脚本首先打印用户问题，之后展示五个阶段的输入/输出。它通过应用 Orchestrator
获取已发布指标定义与真实查询结果，不在测试入口中写死指标字段、指标 ID 或数据值。
这条路径需要数据库、检索服务和模型；换指标时该指标必须已发布在 Metadata 中。

两个周期并行查询的运行日志会带任务标识，例如：

```text
[data-agent-1] 🔍 [ColumnRecall] ...
[data-agent-2] 🔍 [ColumnRecall] ...
```

同一任务内部的召回、SQL 生成/校验/执行及 asyncmy 驱动说明共享这个前缀。
编号在查询任务创建时自动分配，与指标、周期和问题类型无关；进程重启后重新计数。
文件日志记录完整请求/任务 ID；并行日志仍会交错，可按标识区分。

下降幅度很小也允许继续分析，不引入百分比门槛或 ±3σ 判断。

当前核实范围是请求/指标/周期标记、日历完整月份、返回值和已有差额的一致性。
该节点没有查询 SQL 的口径证据、ETL 水位或历史分布，不能证明 SQL 业务正确、
数据已完整入仓或变化属于统计异常。核实为 stop 时结束，为 continue 时进入策略节点。

## 分析 Skill 与策略节点

打开 `test_plan_analysis.py`，修改顶部 `QUESTION` 后直接运行，默认通过真实链路
展示变化核实及 Node 6 的输入输出。资源注入复用同目录已有测试入口。

```bash
python -m app.agent.anomaly_agent.tests.test_plan_analysis
```

本文件只保留 `main()` 运行入口，不包含 unittest 测试类。
在 PyCharm 中使用 Python 运行配置；若之前保存了该文件的 Unittests 配置，请删除旧配置，
再通过 `main` 入口运行。真实链路需要模型、数据库及检索服务。

第一版只注册销售额（sales_amount，SUM）月环比下降 Skill，采用业务规则制定策略，
不额外调用 LLM。按实际 Metadata 的维度顺序生成全部适用步骤，不维护维度名称白名单。
名称、别名和完整字段都可用于识别单值筛选。排除直接固定、依赖传播固定的维度，
以及当前完整月份对比所使用的默认时间字段。excluded_dimensions 记录排除原因。
查询并发控制由后续执行节点负责。
其他指标/比较类型/方向返回 unavailable；筛选无法唯一映射、无可用维度或多个 Skill
同时命中也会明确返回原因，不回退到不相关策略。

AnalysisContext 由已有 State 临时组装；State 只新增 analysis_strategy。
analysis_plan 仍表示已有总体查询路由，analysis_strategy 表示后续拆解步骤，二者职责不同。
执行节点必须沿用 analysis_request 中的指标、周期和全部筛选，以及 metric_context 的指标口径。
plan_analysis 只生成并校验候选策略；之后 analyze_change 在一个节点内执行有限决策循环。
支持引用成功分组进行条件下钻；当前不进行公式拆解或因果推断。
维度可加总性和跨表关联粒度仍需后续执行/贡献计算阶段校验，不能仅凭 SUM 推断分组可直接相加。

### 维度依赖（第一版）

在指标 dimensions JSON 内配置，例如城市维度增加：

```json
{"name": "城市", "column": "dim_store.city", "aliases": ["门店城市"], "determines": ["dim_store.region"]}
```

表示一个城市可唯一确定大区，而不是一个大区只能包含一个城市。关系可传递，不能循环；
引用必须在同一指标的维度列表内。无配置时不推断。当前发布校验只支持同表依赖，
扫描 DW 确认每个非空源值只对应一个非空目标值，拒绝空数据、缺失归属和多重归属。
这只验证发布时的数据；数据更新破坏关系时需要重新校验或撤销配置。

现有销售额配置的迁移脚本（不运行全量种子覆盖人工配置）：

```bash
python -m app.scripts.sync_dimension_dependencies
python -m app.scripts.sync_dimension_dependencies --apply
```

迁移先验证 DW 数据，写入前保存快照，再更新 Meta 与 Qdrant payload。只有 Metadata
增加该关系后，北京筛选才会同时排除城市和大区。节点测试入口仍为 main；
公共规则的离线回归补充在已有 app/tests/test_value_grounding_unit.py 中。

## 单节点自适应分析（新增）

路径：verify_change → plan_analysis → analyze_change → END。
Skill 步骤是候选集合，LLM 每轮只能选择一个候选维度或 finish，不必全部执行。
默认最多 3 轮决策、每个维度 2 次取数、节点总时限 240 秒；单次决策 45 秒，
单个维度工具 90 秒。配置常量在 nodes/analyze_change.py，均由代码强制执行。
节点每次调用从空历史开始，不支持在节点中断位置自动恢复；不要对整个节点盲目重试。

维度工具顺序执行两期查询，每次创建独立 DW Session，避免并发共享 Meta Session。
原始周期、筛选与指标口径来自 State；模型动作不可携带 SQL、自由编写的筛选或新指标。
固定维度沿用 Skill 排除规则；第一版额外排除字段名以 _id 结尾的标识符维度。
单期最多 100 组（查询请求 101 组用于超限检测），拒绝重复键、缺失／非有限值。
NULL 维度保留为独立分组，仅一期不存在的组以零补齐。
根范围两期分组分别与总体对账，下钻时与引用的父分组两期数值对账；差额也需对账，
容差为 0.01 元。贡献率分母为当前查询范围的净差额，可能为负或超过 100%；
父子级或不同维度的贡献不能相加。失败结果不参与贡献结论。
保留成功查询 SQL 供复核。对账只是必要检查，不保证 SQL 口径或 Join 粒度完全正确；
查询限制目前通过 Text-to-SQL 指令表达，尚未实现 AST 级结构化口径锁定及数据库硬资源配额。
其他高基数字段也可能超限，本版拒绝形成其贡献结论。

离线回归（无需真实模型和数据库）：

```bash
python -m unittest app.agent.anomaly_agent.tests.test_analyze_change_unit
python -m unittest app.agent.anomaly_agent.tests.test_drilldown_unit
```

真实链路：修改 run_analyze_change.py 顶部 QUESTION，通过 Python 配置直接运行，或执行：

```bash
python -m app.agent.anomaly_agent.tests.run_analyze_change
```

输入来自已有总体查询和核实结果；输出 change_analysis_report 包含 decisions、observations、
stop_reason 和确定性生成的 summary。事件层转发 analysis_action、analysis_observation，
最终通过 token 展示报告，兼容现有 UI，不使用旧 anomaly_result 卡片协议。

注意：原 test_verify_change.py、test_plan_analysis.py 也调用完整 Orchestrator，
接线更新后即使只打印前面阶段，也会实际执行新增分析节点，增加模型调用和查询耗时。

### 条件下钻

AnalysisAction 新增可选 scope_ref，例如：

```json
{
  "action": "dimension_contribution",
  "dimension_column": "dim_promotion.promo_name",
  "scope_ref": {"observation_id": "dimension_1", "group_value": "上海南京东路旗舰店"},
  "reason": "检查该门店下降是否集中在某些活动"
}
```

scope_ref 为空表示原请求范围；非空时必须来自已成功观察、且展示给模型的前 10 个分组。
scope_planning.py 沿引用链恢复父范围，继承原请求的全部条件，再追加该分组所属字段的等值筛选。
不允许引用失败、虚构、未展示、NULL 分组；NULL 仍可作为查询结果中的独立分组展示。
下钻后重新按 Metadata 排除固定维度及其依赖维度；模型从 available_actions 菜单中选择。
重复控制从“维度”改为“完整范围＋维度”，同维度可以在不同门店分析，不能在同范围重复。
最大下钻深度为 2，默认三轮预算不变；模型也可以横向比较或结束，不强制每轮下钻。
如果新增/消失分组下钻后某期无数据，仅在父分组该期基准为零时允许空结果。

观察新增 scope_filters、scope_depth、baseline_current_value、baseline_comparison_value、
baseline_change_amount、contribution_basis。报告展示当前筛选范围及贡献率口径，
run_analyze_change.py 会先打印简洁的执行路径，便于区分横向分析与下钻。
本版仍复用 Text-to-SQL，以指令传递继承后的查询条件；对账通过不能代替 SQL 语义审核。
