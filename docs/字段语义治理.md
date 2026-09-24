# 字段语义治理：模型候选、人工审核、发布

## 1. 要解决的问题

DW Schema 同步器只写入可信的物理事实：表名、字段名、类型、主键、外键和
Join 关系。`column_info` 中的业务说明、别名和枚举样例属于业务语义，不能由
同步器直接猜，也不应让大模型直接写入正式元数据，否则一次幻觉就会污染后续
检索和 SQL 生成上下文。

因此采用三层职责：

```text
大模型   → 根据表名、字段注释、字段类型和已发布指标推导候选语义
采样器   → 按安全策略从 DW 采集真实枚举值，模型不能编造取值
人工审核 → 通过后才把语义发布回 column_info
```

## 2. 核心数据流

```text
DW Schema 同步
    ↓
column_info（只有物理事实）
    ↓ 生成候选
column_semantic_draft(status = pending)
    ↓ 人工审核
    ├─ approved → 写入 column_info
    └─ rejected → column_info 保持不变
```

`column_info` 始终只保存已发布、可被 Agent 使用的字段知识；草稿表保存模型
候选与审核过程，两者职责分离。

## 3. 命令

生成候选草稿：

```bash
python -m app.scripts.generate_column_semantic_drafts
```

该命令默认只处理第一版高价值字段，例如 `fact_order.pay_amount`、
`dim_store.region`、`dim_user.user_level` 等，避免一次给 48 个字段带来审核负担。
可指定字段或强制重新生成：

```bash
python -m app.scripts.generate_column_semantic_drafts --column dim_store.region --force
```

如果要为 Meta 中全部 48 个已同步字段生成待审核草稿：

```bash
python -m app.scripts.generate_column_semantic_drafts --all
```

`--all` 会遍历 `table_info` 与 `column_info`，把所有字段交给模型生成候选。已经有
`pending` 草稿的字段会跳过，所以重复运行不会重复生成。注意这会发起多次模型调用，
建议在本地网络和 API Key 可用时分批执行，而不是在业务页面自动触发。

查看待审核草稿：

```bash
python -m app.scripts.review_column_semantic_drafts --list
python -m app.scripts.review_column_semantic_drafts --show <草稿ID>
```

审核通过并发布：

```bash
python -m app.scripts.review_column_semantic_drafts \
  --approve <草稿ID> \
  --reviewer 张三 \
  --comment 已确认该字段口径
```

审核人可以直接改写候选后再发布：

```bash
python -m app.scripts.review_column_semantic_drafts \
  --approve <草稿ID> \
  --reviewer 张三 \
  --description 门店所属销售大区 \
  --alias 大区,销售区域
```

审核驳回：

```bash
python -m app.scripts.review_column_semantic_drafts \
  --reject <草稿ID> \
  --reviewer 张三 \
  --comment 该字段不应作为枚举样例
```

## 4. 采样安全策略

真实枚举样例来自 DW，而不是模型输出。策略位于
[value_sampling_policy.py](/Users/gaokai/Desktop/CodeProjects/large_models/data-agent/app/core/meta/value_sampling_policy.py)，
按以下顺序判定：

1. 数值型和时间型字段不采样，因为它们不适合作为枚举值。
2. 主外键、流水号、个人姓名、联系方式等模式不采样。
3. 只有显式进入业务枚举白名单的字段才允许采样。
4. 去重取值超过 30 个时认为基数过高，同样不写入样例。

例如 `dim_store.region` 可以采样 `华东、华北、华南`；`fact_order.order_sn`
和 `dim_user.user_name` 会被策略拒绝。所有判定结果写入草稿的 `evidence`，
审核人可以追溯某个字段为什么没有真实样例。

## 5. 幂等与防重复打扰

生成脚本可以重复运行，并遵循以下规则：

- 已有 `pending` 草稿的字段默认跳过，避免覆盖尚未审核的内容。
- 已发布过别名的字段视为语义已完成，默认跳过。
- 同一 Prompt 版本下被驳回过的字段默认不再重复提交，避免反复打扰审核人。
- 需要强制重来时可加 `--force`，旧的 `pending` 草稿会标记为 `superseded`。

审核动作必须指定审核人，并记录审核意见和审核时间。审核通过时还会把实际写入
`column_info` 的内容保存在 `published_content`，便于追溯和审计。

## 6. 当前边界

本阶段只提供命令行审核，不包含登录鉴权、多级审批或管理后台。这些能力应在
核心数据流和 Agent 效果稳定后，再以同一套 Service 方法包装为 API 和界面。

已提供第一版 Web 审批入口，两种方式都调用同一套 Service 与 API：

在问数控制台内审批：

```bash
python -m streamlit run web_ui.py --server.port 8501
```

打开 `http://localhost:8501` 后，在左侧「功能导航」进入「元数据管理」，再选择
「字段语义审批」。

也可以作为独立页面运行：

```bash
streamlit run meta_approval_ui.py
```

审批页面只通过 HTTP 调用治理 API，业务人员可以在浏览器中查看待审核草稿，填写
审核人后选择“通过并发布”或“驳回”。审批通过会调用同一个 Service 方法写回
`column_info`，因此命令行和 Web 页面共享同一套业务规则与审计逻辑。

最后更新：2026-09-16
