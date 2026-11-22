# BusinessDiagnosisAgent 职责与增量价值审计（2026-11-19，迁移前历史记录）

当前代码路径及职责请以 `docs/business_diagnosis_migration.md` 为准。

## 结论与证据级别

当前代码中，BusinessDiagnosisAgent 的唯一新增查询能力是对真实维度成员执行 `scoped_metric_decomposition`。既有总体、维度与总体指标拆解结论大多可由主 Agent 所持有的 Observation 直接读取。用户明确授权在线验证后，当前版本的 A 组真实运行成功：4 次业务取数、新增一条线下旗舰店范围拆解证据并对账通过；B 组仍只做影子计划、不执行 SQL。**功能增量得到验证，但保留或迁移由用户决定。**

追加的**同源离线 A/B 回放**使用当前 A 组 Graph、真实保存的前置证据和历史保存的四条查询响应；仅用确定性模型替身驱动 A 组决策，避免外部调用。A 组重新算出了相同的新增证据 ID `ev_b271c0e16ff4d2bc`，范围对账通过；B 组不执行查询，规划两个成员。此实验验证当前代码的功能增量与覆盖缺口，**不能替代真实 LLM 和数据库的在线质量、延迟或成本对照**。

首次尝试在可联网环境重试 A 组时，自动审批因内部销售证据发送目的地未明确授权而拒绝。用户随后明确授权访问配置的 DeepSeek、MySQL、Qdrant 和 Elasticsearch，才进行了本次成功的在线运行。此前无网络尝试因 `APIConnectionError` 返回 partial；这些失败记录与成功结果分开保留。

本文将“历史真实运行”与“本轮实时运行”严格分开。历史输出与本轮代码的维度证据 ID 已不同，不能当作同版本的配对性能实验。诊断原始记录：`diagnostics/rootcause_decomposition_c.json`、`diagnostics/business_diagnosis_scoped_real.json`。本轮实时记录与埋点：`/tmp/business_diagnosis_live_result.json`、`/tmp/business_diagnosis_live_audit.jsonl`。

## 职责重复矩阵

| 职责 | 主 Agent | 维度归因 | 指标拆解 | 业务诊断 | 判断 |
| --- | --- | --- | --- | --- | --- |
| 变化检验后决定下一动作 | 是 | 否 | 否 | 内部再次决策 | 决策层重复 |
| 维度成员两期变化与贡献 | 读取结果 | 计算并对账 | 否 | 重新陈述 | 结论重复 |
| 总体订单量/客单价拆解 | 读取结果 | 否 | 计算并对账 | 重新陈述 | 结论重复 |
| 证据 ID、缺口与假设状态 | Observation 和调度条件 | 局部证据 | 局部证据 | 构造证据索引与假设 | 部分独有；可向主图迁移 |
| 真实成员范围内的订单量/客单价补查 | 可调度受控动作，但当前未单列此动作 | 提供真实成员 | 提供计算公式 | 执行 4 次查询并对账 | 当前独有增量 |
| 最终展示报告 | 主图返回 State | 否 | 否 | 提供诊断结果 | Orchestrator 适配层生成 |

调用链：`Orchestrator → MainRootCause.supervisor_reason → action_executor → BusinessDiagnosisAgent(prepare_evidence → main_reason ↔ execute_diagnosis_tools → finalize_diagnosis) → action_executor.observations → supervisor_reason`。报告由主图结束后的 `run_anomaly_agent` 适配层生成。业务诊断输入只能使用同一请求且 `ChangeInspection.ready` 的结果；上游归因与拆解结果可选。

## 同源前置证据的 A/B 对照

前置记录为 2026 年 8 月北京销售额对比 7 月：233378.95 → 120953.99，变化 -112424.96；渠道成员下降额：线下旗舰店 -87003.55、线上自营 -15021.41、线上商城 -10400.00。总体拆解已有订单量和平均支付订单金额两项贡献。两组均读取 `rootcause_decomposition_c.json`；B 只生成影子计划，不连接生产路由或执行 SQL。

| 项目 | A：当前版本在线成功运行 | B：主 Agent 影子决策器 | A：同源离线回放 | A：历史真实成功记录 |
| --- | --- | --- | --- |
| 已有事实 | 同一前置记录 | 同一前置记录 | 同一前置记录 | 同一前置记录 |
| 新业务取数 | 4 次实际取数 | 0 | 回放 4 条历史响应，真实执行 0 | 4 |
| 新证据 | 线下旗舰店范围拆解 `ev_9e633eb2221687ea`，已对账 | 无 | 相同数值的范围拆解 `ev_b271c0e16ff4d2bc`，已对账 | `ev_b271c0e16ff4d2bc`，已对账 |
| 补查范围 | 线下旗舰店，覆盖约 77.39% | 线下旗舰店、线上自营各计划 4 次查询，累计覆盖约 90.75% | 线下旗舰店，覆盖约 77.39% | 线下旗舰店，覆盖约 77.39% |
| 假设 | 1 条 supported、6 条 evidence_gap；仍不能解释业务因果 | 2 条待验证 | 模型替身未提出假设 | 有 supported 与 evidence_gap |

可从已有证据推导：总体下降、三个渠道的统计贡献、总体订单量及客单价的拆解、缺少具体业务事件证据。依赖新增查询的结论：线下旗舰店范围内订单量因素约 -65461.79、平均支付订单金额因素约 -21541.76；两项与成员下降 -87003.55 在 0.01 容差内对账。它仍是统计分摊，不是业务因果。

在线结果中，最终 6 条 findings 中 4 条重述总体、维度与总体拆解已有结论；新增的 2 条是线下旗舰店范围内的订单量与客单价分摊。新证据显示该成员的 -87003.55 中，订单量因素约 -65461.79、平均支付订单金额因素约 -21541.76。当前模型把“主要渠道贡献”和“总体订单量贡献”等已有统计事实写入 `evidence_gap` 假设；这是状态分类不准确，而非缺少对应统计证据。`supported` 仅有“线下旗舰店范围内订单量贡献较大”一条，并不证明业务因果。

## 耗时、Token、SQL 与费用

| 测量范围 | prepare_evidence | main_reason | execute_diagnosis_tools | finalize_diagnosis | SQL | Token / 费用 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 本轮 A 组失败运行，节点 wall time | 0.42 ms | 961.65 ms | 未进入 | 0.09 ms | 0 | 模型请求失败，服务端用量不可得；费用不可估 |
| 本轮 A 组在线成功运行，节点 wall time | 0.51 ms | 19568.25 + 16585.37 ms | 9135.11 ms | 0.16 ms | 4 次实际取数 | 诊断 LLM：输入 7801、输出 6569 Token；见下方费用区间 |
| 本轮同源离线回放，节点 wall time | 0.31 ms | 38.44 + 12.55 ms | 6.17 ms | 0.06 ms | 4 条保存响应回放，真实执行 0 | 模型替身，无真实 Token/费用；不能用于生产性能比较 |
| 历史 A 组成功运行 | 未记录 | 未记录 | 未记录 | 未记录 | 4 | 未记录，不可估 |
| 本轮 B 组影子计划 | 未运行这些节点 | 未运行这些节点 | 不执行 | 未运行这些节点 | 0 | 决策函数 0.282 ms；无 LLM 调用，0 Token、0 模型费用 |

成功 A 组总 wall time 为 **46821.0 ms**，其中包含初始化、图调度等开销；**不能用它与 B 组的 0.282 ms 相减来归因节点成本**，因为 B 不执行 SQL 或模型。A 组两次诊断 LLM 回调耗时分别为 18126.8 和 16579.02 ms；它们包含在 `main_reason` 节点 wall time 内，不得再次累加。先前失败 A 组总时长 2345.3 ms、模型回调 69.08 ms，亦不可与成功运行混算。

本次模型响应名为 `deepseek-flash`，实际 Token 由模型回调取得。按 [DeepSeek 官方定价](https://api-docs.deepseek.com/quick_start/pricing/) 和本次运行约 06:10 UTC、工作日峰时，**仅两次业务诊断 LLM** 的费用区间约 **$0.00793–$0.01022**，下界按输入全命中缓存、上界按输入全未命中缓存计算；回调未记录缓存命中 Token，故不能给出精确账单。范围补查内部复用 DataQueryTool，可能触发额外 Text-to-SQL 模型调用；当前诊断埋点只记录业务诊断 `main_reason` 的 LLM 用量，**该费用区间不是整个 A 组的总费用**。默认埋点费用字段保持 `null`，仅在显式配置价格后逐次计算估值。

表中的 SQL 数量是诊断结果报告的业务取数次数，不包含潜在的 `EXPLAIN`、重试等底层请求；不能视为数据库物理执行总次数。启用埋点：`BUSINESS_DIAGNOSIS_AUDIT=1`，指定 `BUSINESS_DIAGNOSIS_AUDIT_PATH=/tmp/business_diagnosis_audit.jsonl`；可选配置 `BUSINESS_DIAGNOSIS_INPUT_USD_PER_1M` 和 `BUSINESS_DIAGNOSIS_OUTPUT_USD_PER_1M`。关闭开关时不写记录。

## 约束、失败与证据不足

- `scoped_metric_decomposition` 要求父级成员来自真实 AttributionPath，维度来自当前 MetricContext，保留固定筛选、两期时间边界和订单状态，并核对范围销售额与父成员及双因素分摊结果。测试覆盖假成员、预算不足、SQL 缺少周期/固定筛选/成员/订单状态。当前 SQL 核验以字符串匹配为主，不能证明复杂 SQL 的完整语义正确性。
- 对历史成功记录中的 4 条实际 SQL 重新执行当前 `_validate_sql` 合约检查，均通过；历史记录中的范围分摊对账标记为 true。这是离线复核，不是本轮新的数据库查询。
- 对本次在线运行返回的 4 条 SQL 再执行当前 `_validate_sql` 合约检查，均通过；两期均覆盖，固定北京筛选和线下旗舰店成员范围保持，`request_id` 一致，范围拆解与父成员变化对账通过。新 `evidence_id` 与历史不同，是因其由包含本次查询内容的 payload 哈希生成；数值结论一致。
- 本轮实时 A 组因 `APIConnectionError` 返回 partial，保留已有证据；不把模型失败解释为业务诊断没有价值。
- 当前 `finalize_diagnosis` 在模型失败且结果为 partial 时，`summary` 仍写“已基于真实证据完成受控业务假设验证”。这与本轮 `llm_failed` 的实际执行状态不符，是报告语义缺陷；本轮按约束只记录问题，不修改生产分析逻辑。
- 无维度归因或无总体拆解时，诊断输出相应 evidence gap。没有促销、库存、流量等数据时，不能宣称业务因果根因。
- 80% 多成员场景：现有 `_fallback_scoped_decision` 默认选最大下降成员一次；本例其毛下降覆盖约 77.39%，未达到阈值。影子计划选第二成员后覆盖约 90.75%。这说明单成员默认策略可能遗漏重要下降来源；并不证明所有问题都应固定使用 80% 门槛。

## 后续代码取舍建议（本轮不执行迁移）

| 代码 | 建议 | 条件 |
| --- | --- | --- |
| `BusinessDiagnosis_Agent/tools.py` 的范围拆解、真实成员门禁、SQL 验证和对账 | **保留** | 后续可封装为主 Agent 的受控动作 |
| `BusinessDiagnosis_Agent/schemas.py` 的证据 ID、假设状态和结果契约 | **保留并评估迁移** | 先定义主图统一证据契约和向后兼容方案 |
| `BusinessDiagnosis_Agent/nodes.py` 的证据准备与最终事实模板 | **评估迁移** | 主 Agent 统一证据审核能覆盖相同输出后 |
| `BusinessDiagnosis_Agent/nodes.py` 的第二层 `main_reason` 循环及单成员兜底 | **由用户决定是否迁移** | 同源功能差异已验证；是否接受影子决策器的覆盖策略与缺少第二层 LLM 的质量取舍，仍需产品判断。本轮不删除 |
| `app/service/anomaly_report.py` | **保留展示职责** | 不把展示适配与业务验证混为一层 |

合并草案：主 Supervisor 读取统一 Evidence Ledger，程序校验请求/范围/对账后输出 `evidence_gap`；仅选择白名单补查动作（包括成员范围拆解），执行结果以原 `evidence_id`、`task_id` 和 Observation 写回；达到证据充分、预算或失败条件后结束，由展示适配层生成报告。回归测试至少覆盖旧路径不变、方向不符停止、固定筛选与时间窗口、假成员拒绝、SQL 语义、对账失败、同源多成员覆盖、预算与重试、证据 ID 稳定、报告结论不超证据，并建立端到端成功率、有效证据增量、P95、SQL/Token/费用对照。

## 本轮实际执行

- `test_skeleton` + `test_react_loop`：9 项通过（埋点关闭）。
- `test_react_loop`：1 项通过（埋点开启）。
- `test_scoped_decomposition`：4 项通过，含新增 SQL 周期、固定筛选、成员范围与订单状态拒绝用例。
- `test_shadow_audit`：2 项通过。
- `test_audit_telemetry`：1 项通过，验证模型/Token 和显式配置单价下的费用计算。
- `test_real_evidence_replay`：1 项通过，当前 A 组 Graph 基于真实保存的前置与查询响应复算相同新增证据 ID；B 组同源且不执行 SQL。
- 最终联合回归（上述用例及主 Agent 业务诊断动作测试）：21 项通过。
- 当前代码真实 A 组首次运行 1 次，模型连接失败；此失败样本未参与成功 A/B 性能比较。
- 用户明确授权后，当前代码真实 A 组再次运行 1 次并通过验收：2 次诊断 LLM、4 次业务取数、1 条新增范围证据；对应原始记录位于 `/tmp/business_diagnosis_authorized_result.json` 与 `/tmp/business_diagnosis_authorized_audit.jsonl`。
