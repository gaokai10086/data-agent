# 业务诊断并入 Main RootCause Agent

## 设计与边界

原流程：主 Supervisor → `business_diagnosis` Action → 独立 BusinessDiagnosis StateGraph（证据准备 → 独立 LLM 决策 → scoped 工具 → 汇总）→ 主 Observation。

新流程：主 Supervisor → 变化检验 → 维度归因 / 指标拆解 → 同一个 Supervisor 读取已验证证据并决定诊断假设及 scoped 补查 → Action Executor 执行受控工具 → Observation → 同一个 Supervisor 继续推理 → `AnalysisResult`。

主 Agent 负责诊断决策；原业务诊断的 Schema、范围拆解工具及纯证据函数已迁入主 Agent 的 `diagnosis_support` 包。独立 Graph、State 与旧文件夹已移除。没有实现 Report Agent；下游可用 `materialize_report_input(state)` 取得结构化数据。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `Main_RootCause_Agent/nodes/supervisor_reason.py` | 统一调度与业务诊断决策入口 |
| `Main_RootCause_Agent/diagnosis.py` | 证据约束、假设更新、范围补查计划和结果终结 |
| `Main_RootCause_Agent/nodes/action_executor.py` | Action 权限、参数校验、去重、预算、工具调用及 Observation |
| `Main_RootCause_Agent/actions/registry.py` | 注册 `scoped_metric_decomposition` 为受控 Action |
| `Main_RootCause_Agent/budget.py` | 跨阶段聚合已记录的 SQL 查询次数 |
| `Main_RootCause_Agent/analysis_result.py` | 小型结构化索引与下游数据解引用接口 |
| `Main_RootCause_Agent/diagnosis_support/tools.py` | 保留成员、父证据、筛选、周期、SQL 与对账校验 |
| `Main_RootCause_Agent/diagnosis_support/schemas.py`, `nodes.py` | 保留证据模型及纯函数 |

## State 与数据流

输入为问题、解析请求、Skill 和已有 Observation。主 Supervisor 输出 Action 名称及经 Pydantic 校验的 `tool_arguments`。Action Executor 把工具结果、查询次数及证据 ID 放入 Observation；主 State 仅保留诊断假设、少量证据引用和最终 `analysis_result`。已验证的大型归因及拆解结果留在 Observation，`AnalysisResult.observation_refs` 用索引引用，避免重复存储。

`AnalysisResult` 包含 request_id、指标和周期、总体检验、维度多路径索引、证据 ID、Findings、Hypotheses、各阶段状态、SQL 使用量、停止原因及整体完成状态。`materialize_report_input` 可在下游展开完整结果；所有数值仍来自原 Observation 或已验证证据。

诊断工具必须取得 ready 变化检验、同 request_id 的已对账父证据、有效 task_id 和实际成员。范围工具继续验证固定筛选、周期与对账。失败时已验证证据继续保留，完成状态降为 partial。补查候选沿用累计下降覆盖率 80% 的多成员计划；不会只取最大下降成员。

## 验证状态与风险

本地执行 `python -m compileall -q ...` 与 `git diff --check` 均通过。当前 Python 环境没有 pytest；使用 `uv run --with pytest` 时因网络 DNS 无法下载锁定依赖，另一现有环境缺少 `langchain_core`，因此本次环境内未能执行 pytest。应在依赖完整环境运行 `Main_RootCause_Agent/tests`、`Main_RootCause_Agent/diagnosis_support/tests` 和报告适配测试，再进行真实数据库/LLM 验收。

SQL 预算聚合依赖各 Action 记录的 `total_query_count` 或 SQL 明细。异常中断且未返回查询次数的底层工具可能少计真实已发起的 SQL；生产验收前应在共享 SQL 执行层增加统一计数器。模型决策正确性、真实多路径成员补查及报告内容仍需以真实回放验证。
