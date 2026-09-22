# Data Agent 智能问数

面向企业数据仓库的 Text-to-SQL / Data Agent 项目，使用 LangGraph 编排意图识别、Schema 检索、指标检索、值检索、Few-shot SQL、SQL 校验、自愈和结构化结果展示。

## 项目结构

完整的目录和文件职责说明见：

👉 [docs/PROJECT_STRUCTURE.md](docs/PROJECT_STRUCTURE.md)

## 数仓 ER 图

DW 表的粒度、主外键和推荐 Join 路径见：

👉 [docs/DW_ERD.md](docs/DW_ERD.md)

## 业务指标口径

已确认指标的业务定义、计算规则和 SQL 模板见：

👉 [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md)

## Meta 数据库

智能问数控制平面的表设计、职责和初始化方式见：

👉 [docs/META_DATABASE_DESIGN.md](docs/META_DATABASE_DESIGN.md)

## 快速开始

1. 安装依赖。
2. 复制 `.env.example` 为 `.env`，填写本地配置。
3. 确保 MySQL、Qdrant 和 Elasticsearch 服务可用。
4. 运行：

```bash
python start_all.py
```

5. 打开 <http://localhost:8501>。

## 核心链路

```text
用户问题 → 多路召回 → 上下文组装 → SQL 生成 → EXPLAIN 校验
→ SQL 自愈 → 只读执行 → 结构化 BI 报告
```

## 当前阶段

项目目前属于架构较完整的 MVP / Demo。后续升级重点应按以下顺序推进：

1. SQL 安全与数据权限。
2. Text-to-SQL 自动化评测。
3. 可靠性、延迟和成本观测。
4. 测试、部署和文档完善。
