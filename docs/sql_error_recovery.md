# SQL 异常分类与恢复

基线：GitHub origin/main `771794f`。变更只覆盖 SQL 校验/执行及错误输出，四路召回、会话持久化与 Checkpointer 架构保持原样。

## 恢复决策

| 错误 | 动作 |
| --- | --- |
| MySQL 1052/1054/1064/1109/1146/1149，以及既有 Schema/值/Join 引用校验失败 | 复用 correct_sql 的原问题、SQL、Schema、指标和错误上下文，再回到 validate_sql |
| 权限/认证错误 | translate_error → END，不调用 LLM |
| 明确的临时连接错误或 SQLAlchemy 已标记失效的连接 | 当前节点内最多尝试 3 次；每次失败先回滚 Session；等待 0.25、0.5 秒 |
| SQLSafetyError | translate_error → END；生成或纠错产生的危险 SQL 均终止 |
| 单次操作超时 | 取消当前等待并终止，不重复执行慢查询 |
| 其他错误 | 记录分类、阶段、异常类型和脱敏原因，统一终止 |

SQL 纠错沿用原来的累计 3 次失败上限（最多两次 LLM 纠错），校验与执行共享计数。技术重试独立计数。连接池继续使用现有 pool_pre_ping/pool_recycle，事务回滚和重新获取连接交给 SQLAlchemy。回滚失败后停止技术重试。

单次节点数据库操作超时为 15 秒，包含该节点内的 EXPLAIN/引用校验或执行/取数；回滚另有 15 秒上限。这不是整个请求的超时预算。配置常量集中于 `app/service/sql_error_handling.py`。

## 文件职责

- `app/service/sql_error_handling.py`：驱动错误码分类、有限重试、事务清理、脱敏错误状态。
- `app/core/sql_safety.py`：用兼容 ValueError 的 SQLSafetyError 标记安全拒绝，原拦截规则不变。
- `app/agent/data_agent/state.py`：增加错误类别和失败阶段；继续使用现有 retry_count。
- `app/agent/data_agent/sql_routing.py`：两个查询图共用的恢复路由。
- `app/agent/data_agent/graph.py`、`query_graph.py`：执行后改为条件路由；成功仍进入既有展示链或直接结束。
- `app/agent/data_agent/nodes/validate_sql.py`、`execute_sql.py`：错误写回 State，避免执行异常直接越过图路由。
- `app/agent/data_agent/nodes/translate_error.py`：固定业务文案，通过原 error_response 事件输出，避免模型转述内部异常。
- `app/api/routers/chat_router.py`：未预期异常也使用安全提示，保留 error 字段兼容性，流自然结束。
- `app/tests/test_sql_read_only_safety.py`：更新旧的“安全拒绝可纠错”预期，验证真实纠错节点。
- `app/tests/test_sql_error_recovery.py`：驱动异常、事务恢复、路由、超时、取消及 SSE 故障注入。

## 验证

```bash
.venv/bin/python -m unittest \
  app.tests.test_sql_read_only_safety \
  app.tests.test_sql_error_recovery \
  app.tests.test_conversation_context \
  app.tests.test_multiturn_context \
  app.tests.test_conversation_persistence -q
```

45 项离线测试通过，包括真实 SQL 校验/纠错节点及 LangGraph 条件路由；SSE 验证失败事件、异常兜底、成功报告（含图表/分析载荷）透传和流结束。`git diff --check` 通过。

## 边界与下一步

- 本地虚拟环境缺少现有 MySQL 会话库需要的 pymysql；SSE 测试使用会话仓储替身，未验证真实 MySQL 持久化。
- 未连接真实数据库或调用真实 LLM。上线前应在测试库验证断网恢复、权限拒绝、驱动取消与完整 BI 展示链。
- 客户端协程超时不保证服务器立即停止 SQL；严格资源控制还需数据库侧执行时间与只读权限限制。
- 分类采用明确错误码白名单；未知错误保守终止，不自动处理死锁、锁等待、连接数耗尽或业务语义错误。
- 保留原有文本只读策略的覆盖范围，不能据此声称具备完整 SQL 安全隔离。

工程判断练习：为什么 pool_pre_ping 不能代替查询中途断线后的事务恢复？为什么重试连接故障和让 LLM 修改 SQL 要有独立预算？
