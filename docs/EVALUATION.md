# 智能问数评测框架使用说明

## 1. 这个框架解决什么问题

Text-to-SQL 最危险的问题不是“SQL 报错”，而是“SQL 能跑，但答案是错的”。

例如：

```text
用户问：去年华东地区的销售额是多少？
系统返回：50518240.41
```

这个数字能出来，但地区和时间条件被模型删掉了，所以它是错的。没有评测集，你无法发现这类问题，也无法证明优化是否有效。

这个框架的目标是：

```text
用一份固定的题目集，量化 Agent 的执行准确率
每次修改 Prompt、召回、校验后，用同一个标准对比前后结果
```

## 2. 前置条件

评测跑的是完整 Agent 链路，所以需要这些服务都启动：

```text
MySQL：dw 与 meta
Qdrant：字段索引、指标索引
Elasticsearch：枚举值索引
DeepSeek 与智谱 Embedding 的 API Key
```

启动方式：

```bash
python start_all.py
```

## 3. 目录结构

```text
app/evaluation/
  golden_questions.jsonl   评测集，一行一题
  schema.py                评测用例数据结构
  comparator.py            结果规范化与比对
  metrics.py               指标计算
  runner.py                单题执行器
  run_evaluation.py        评测 CLI 入口
  add_case.py              追加新题的 CLI
  reports/                 评测报告输出目录
```

## 4. 五步工作流

### 第一步：看当前有哪些题

```bash
python -m app.evaluation.run_evaluation --list
```

输出包含 id、分类、难度、标准答案行数和问题。

### 第二步：新增一道题

```bash
python -m app.evaluation.add_case \
  --question "2025年4月的销售额是多少？" \
  --category 时间范围 \
  --gold-sql "SELECT SUM(pay_amount) FROM fact_order WHERE order_status='已完成' AND pay_time >= '2025-04-01' AND pay_time < '2025-05-01'"
```

你只需要提供三样东西：

```text
question   问什么
category   属于哪一类
gold_sql   正确答案是什么
```

其余元数据全部自动推导：

```text
id          按分类自动编号，例如 join_005
tables      从 SQL 中识别物理表
columns     从 EXPLAIN 执行计划中提取用到的字段
difficulty  根据 JOIN、窗口函数、CTE、子查询、GROUP BY 自动打分
expected_result  自动执行 gold SQL 填充标准答案
```

如果只想先记录题目、暂不连数据库：

```bash
python -m app.evaluation.add_case --question "..." --category "..." --gold-sql "..." --offline
```

也可以直接编辑 `golden_questions.jsonl`，一行一题。

### 第三步：生成标准答案（可选）

```bash
python -m app.evaluation.run_evaluation --refresh-gold
```

用简化版 `add_case` 加题时，标准答案已经自动填充，这一步通常不需要再跑。

只有在下面两种情况才需要：

```text
1. 用 --offline 离线加题
2. DW 数据更新后，需要刷新全部标准答案快照
```

这一步会执行每道题的 `gold_sql`，把真实结果写成 `expected_result`。

如果某道题的 gold SQL 写错了，这一步会直接报错，可以先修题目再继续。

### 第四步：单题调试

```bash
python -m app.evaluation.run_evaluation --id join_001 --verbose
```

输出会包含：

```text
实际 SQL
期望结果
实际结果
失败原因
```

适合定位某一类问题。

### 第五步：跑全量评测并看报告

```bash
python -m app.evaluation.run_evaluation
```

或者只跑某一类：

```bash
python -m app.evaluation.run_evaluation --category 多表Join
```

报告写入：

```text
app/evaluation/reports/eval_report_<时间戳>.md
```

报告包含：

```text
总体 Execution Accuracy
Schema Linking Recall
Join Accuracy
延迟 P50 / P95
分类准确率
失败明细（含实际 SQL、期望结果、实际结果）
```

## 5. 如何解读结果

### Execution Accuracy

主指标。它回答的问题是：

```text
Agent 返回的数据，和标准答案是否一致
```

比较时会做规范化：

```text
忽略列名差异（品牌 与 brand 等价）
忽略行顺序
数值按精度四舍五入
5621、'5621'、5621.00 视为相同
```

### Schema Linking Recall

标准答案需要的表，是否都出现在候选表里。

### Join Accuracy

多表题是否正确保留了 Join 字段。

### 延迟

P50 和 P95 的端到端耗时，用来观察优化是否牺牲了性能。

## 6. 评测集格式

一行一题，字段说明：

```text
id              唯一标识
question        用户自然语言问题
category        分类，用于分组统计
gold_sql        标准 SQL，用来生成标准答案
expected_result 标准答案快照，由 --refresh-gold 生成
tables          标准答案涉及的表
columns         标准答案涉及的字段
metric          涉及的指标
difficulty      难度
notes           备注
expect_no_sql   该题不应返回业务数据（超范围问题）
```

## 7. 已知限制

第一，结果比对忽略列名。表头是否正确，当前不单独计分。

第二，只跑一次。LLM 有随机性，建议后续支持重复运行，报告 pass@1 与 pass@3。

第三，数据规模小。当前 DW 是合成数据，题目难度低于真实企业环境。

第四，超范围问题评分偏宽松。当前只判断“没有返回业务数据”，没有检查是否浪费了自纠错重试。

## 8. 推荐使用节奏

```text
每次改动 Prompt 或召回逻辑
    ↓
python -m app.evaluation.run_evaluation
    ↓
对比新报告与上一份报告的 Execution Accuracy
    ↓
准确率上升则保留改动，下降则回滚
```

这样每一次优化都有数据依据，而不是凭感觉调 Prompt。

最后更新：2026-09-22
