"""字段语义草稿服务：模型推导候选 → 人工审核 → 发布到 column_info。

职责边界：
    1. 模型只产出 description 与 alias 两类候选，不产出字段取值；
    2. 字段真实取值由采样策略从 DW 受控采集，模型无法编造；
    3. 只有审核通过的动作才会写入 column_info，并记录审核人与发布内容。
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, Field

from app.core.datasource.base import BaseDatasourceConnector
from app.core.logging import logger
from app.core.meta.value_sampling_policy import (
    MAX_ALLOWED_CARDINALITY,
    MAX_SAMPLE_VALUES,
    decide,
)
from app.models.mysql.column_semantic_draft_mysql import ColumnSemanticDraftMySQL
from app.prompt.prompt_loader import load_prompt
from app.repositories.mysql.meta_mysql_repository import MetaMySQLRepository

PROMPT_VERSION = "enrich_column_semantic_draft_v1"

# 审核通过允许发布的字段角色。字段类型与主外键由 DW 同步器决定，不在此范围内。
ALLOWED_PUBLISH_ROLES = frozenset({"primary_key", "foreign_key", "measure", "dimension"})

# 第一版只对高价值字段生成草稿，避免一次性铺开 48 个字段导致审核成本过高。
DEFAULT_DRAFT_TARGETS: tuple[str, ...] = (
    "fact_order.pay_amount",
    "fact_order.order_status",
    "fact_order.pay_time",
    "fact_order.original_amount",
    "fact_order.discount_amount",
    "dim_store.region",
    "dim_store.store_name",
    "dim_store.channel_type",
    "dim_user.user_level",
    "dim_user.gender",
    "dim_promotion.promo_name",
    "dim_product.category_level1",
)


class ColumnSemanticCandidate(BaseModel):
    """模型输出的结构化契约，字段别名做宽松兼容以抵抗模型措辞漂移。"""

    description: str = Field(
        default="",
        validation_alias=AliasChoices("description", "desc", "comment", "meaning", "business_description"),
        description="字段业务说明候选",
    )
    alias: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("alias", "aliases", "synonyms", "business_terms"),
        description="字段别名与同义词候选",
    )
    reasoning: str = Field(
        default="",
        validation_alias=AliasChoices("reasoning", "reason", "basis", "evidence"),
        description="模型给出的推导依据与不确定项",
    )


@dataclass
class GenerationReport:
    """候选生成结果统计。"""

    created: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    skipped_pending: list[str] = field(default_factory=list)
    skipped_enriched: list[str] = field(default_factory=list)
    skipped_rejected: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class DraftView:
    """带物理字段信息的草稿视图，供命令行与管理后台展示。"""

    id: str
    qualified_name: str
    status: str
    revision: int
    candidate_description: str
    candidate_alias: list[str]
    candidate_value_examples: list[str]
    candidate_role: str | None
    model_name: str | None
    prompt_version: str | None
    reviewer: str | None
    review_comment: str | None
    created_at: datetime | None
    evidence: dict[str, Any]


@dataclass
class ReviewReport:
    """审核结果，包含发布前后的差异，便于人工确认影响范围。"""

    draft_id: str
    qualified_name: str
    status: str
    reviewer: str
    published: dict[str, Any] = field(default_factory=dict)
    previous: dict[str, Any] = field(default_factory=dict)


class ColumnSemanticDraftService:
    def __init__(
        self,
        meta_repository: MetaMySQLRepository,
        connector: BaseDatasourceConnector,
        llm: Any,
        model_name: str | None = None,
        prompt_version: str = PROMPT_VERSION,
    ):
        self.meta_repository = meta_repository
        self.connector = connector
        self.llm = llm
        self.model_name = model_name or self._resolve_model_name(llm)
        self.prompt_version = prompt_version

    # ==================== 候选生成 ====================

    async def collect_all_targets(self) -> tuple[str, ...]:
        """从 Meta 枚举全部已同步字段，返回「表名.字段名」清单。"""
        pairs = await self.meta_repository.list_qualified_columns()
        return tuple(f"{table_name}.{column_name}" for table_name, column_name in pairs)

    async def generate(
        self,
        targets: tuple[str, ...] | list[str] | None = None,
        force: bool = False,
    ) -> GenerationReport:
        """为指定字段生成待审核草稿；已有的 pending 草稿默认保留。"""
        report = GenerationReport()
        for qualified_name in targets or DEFAULT_DRAFT_TARGETS:
            try:
                outcome = await self._generate_one(qualified_name, force)
            except Exception as exc:  # 单个字段失败不应中断整批生成
                logger.warning(f"⚠️ 字段 [{qualified_name}] 草稿生成失败: {exc}")
                report.failures.append((qualified_name, str(exc)))
                continue

            if outcome == "created":
                report.created.append(qualified_name)
            elif outcome == "superseded":
                report.superseded.append(qualified_name)
            elif outcome == "skipped_enriched":
                report.skipped_enriched.append(qualified_name)
            elif outcome == "skipped_rejected":
                report.skipped_rejected.append(qualified_name)
            else:
                report.skipped_pending.append(qualified_name)
        return report

    async def _generate_one(self, qualified_name: str, force: bool) -> str:
        table_name, column_name = self._split_qualified_name(qualified_name)
        table = await self.meta_repository.get_table_by_name(table_name)
        if table is None:
            raise RuntimeError(f"Meta 中缺少表 [{table_name}]，请先执行 DW Schema 同步")
        column = await self.meta_repository.get_column(table.id, column_name)
        if column is None:
            raise RuntimeError(f"Meta 中缺少字段 [{qualified_name}]，请先执行 DW Schema 同步")

        # 已发布过别名的字段视为完成语义治理，默认不重复打扰审核人。
        if not force and column.alias:
            return "skipped_enriched"

        # 同一 Prompt 版本下被驳回过的字段不再重复提交，避免反复打扰审核人；
        # 优化 Prompt 产生新版本后可自动重试，也可用 force 强制重来。
        if not force and await self.meta_repository.get_rejected_semantic_draft(
            column.id, self.prompt_version
        ):
            return "skipped_rejected"

        existing_pending = await self.meta_repository.get_pending_semantic_draft(column.id)
        outcome = "created"
        if existing_pending is not None:
            if not force:
                return "skipped_pending"
            # 重新生成时，把历史待审核草稿标记为被取代，保留完整追溯链。
            existing_pending.status = "superseded"
            await self.meta_repository.save_semantic_draft(existing_pending)
            outcome = "superseded"

        sample_values, sampling_evidence = await self._collect_safe_samples(
            table_name, column_name, column.type or ""
        )
        related_metrics = await self._find_related_metrics(table_name, column_name)
        prompt = self._build_prompt(
            table_name=table_name,
            column_name=column_name,
            data_type=column.type or "",
            ddl_comment=column.description or "",
            current_description=column.description or "",
            sample_values=sample_values,
            sampling_note=sampling_evidence["policy_reason"],
            related_metrics=related_metrics,
        )
        candidate = await self._invoke_candidate(prompt)
        aliases = self._normalize_terms(candidate.alias)
        if not aliases:
            raise RuntimeError("模型未产出任何有效别名候选，请检查 Prompt 或模型输出")

        evidence = {
            "table_name": table_name,
            "column_name": column_name,
            "data_type": column.type,
            "ddl_comment": column.description,
            "sampling": sampling_evidence,
            "sample_values_used": sample_values,
            "related_metrics": related_metrics,
            "model_reasoning": candidate.reasoning,
        }
        draft = ColumnSemanticDraftMySQL(
            column_id=column.id,
            revision=await self.meta_repository.get_next_semantic_draft_revision(column.id),
            status="pending",
            candidate_description=candidate.description.strip(),
            candidate_alias=aliases,
            candidate_value_examples=sample_values,
            candidate_role=column.role,
            evidence=evidence,
            model_name=self.model_name,
            prompt_version=self.prompt_version,
            published_content={},
        )
        await self.meta_repository.save_semantic_draft(draft)
        return outcome

    async def _collect_safe_samples(
        self, table_name: str, column_name: str, data_type: str
    ) -> tuple[list[str], dict[str, Any]]:
        """按采样策略从 DW 采集真实枚举值；策略不允许时返回空列表。"""
        decision = decide(table_name, column_name, data_type)
        evidence: dict[str, Any] = {
            "allowed": decision.allowed,
            "policy_reason": decision.reason,
        }
        if not decision.allowed:
            return [], evidence

        distinct_count = await self.connector.probe_distinct_value_count(
            table_name, column_name, MAX_ALLOWED_CARDINALITY + 1
        )
        if distinct_count > MAX_ALLOWED_CARDINALITY:
            evidence["distinct_value_count"] = f">{MAX_ALLOWED_CARDINALITY}"
            evidence["policy_reason"] = (
                f"去重取值超过 {MAX_ALLOWED_CARDINALITY} 个，基数过高，不写入枚举样例"
            )
            return [], evidence

        raw_values = await self.connector.get_frequent_column_values(
            table_name, column_name, MAX_SAMPLE_VALUES
        )
        # 统一转字符串：JSON 列不能序列化 Decimal 等数据库类型。
        values = [str(value).strip() for value in raw_values if str(value).strip()]
        values = [value for value in values if len(value) <= 100]
        evidence["distinct_value_count"] = distinct_count
        if not values:
            evidence["policy_reason"] = "字段当前没有可采样的非空取值"
        return values, evidence

    async def _find_related_metrics(self, table_name: str, column_name: str) -> list[str]:
        """找出直接依赖该字段的已发布指标，作为模型推导上下文。"""
        metrics = await self.meta_repository.list_metrics_by_status("published")
        related: list[str] = []
        for metric in metrics:
            if metric.measure_column == column_name and metric.fact_table == table_name:
                related.append(metric.name)
                continue
            filters = metric.filters or []
            if isinstance(filters, list):
                for item in filters:
                    if isinstance(item, dict) and str(item.get("column", "")).endswith(f"{table_name}.{column_name}"):
                        related.append(metric.name)
                        break
        return related

    def _build_prompt(self, **kwargs: Any) -> str:
        template = load_prompt("enrich_column_semantic_draft")
        sample_values = kwargs["sample_values"]
        return template.format(
            table_name=kwargs["table_name"],
            column_name=kwargs["column_name"],
            data_type=kwargs["data_type"],
            ddl_comment=kwargs["ddl_comment"] or "无",
            current_description=kwargs["current_description"] or "无",
            sample_values="、".join(sample_values) if sample_values else "无可用安全样例",
            sampling_note=kwargs["sampling_note"],
            related_metrics="、".join(kwargs["related_metrics"]) if kwargs["related_metrics"] else "无",
        )

    async def _invoke_candidate(self, prompt: str) -> ColumnSemanticCandidate:
        response = await self.llm.ainvoke(prompt)
        raw_content = response if isinstance(response, str) else getattr(response, "content", "")
        payload = self._extract_json(raw_content)
        return ColumnSemanticCandidate.model_validate(payload)

    # ==================== 人工审核 ====================

    async def approve(
        self,
        draft_id: str,
        reviewer: str,
        review_comment: str | None = None,
        description: str | None = None,
        alias: list[str] | None = None,
        value_examples: list[str] | None = None,
        role: str | None = None,
    ) -> ReviewReport:
        """审核通过并把内容发布到 column_info。"""
        self._require_reviewer(reviewer)
        if role is not None and role not in ALLOWED_PUBLISH_ROLES:
            raise RuntimeError(
                f"非法字段角色 [{role}]，仅允许：{'、'.join(sorted(ALLOWED_PUBLISH_ROLES))}"
            )

        draft = await self._require_pending_draft(draft_id)
        column = await self.meta_repository.get_column_by_id(draft.column_id)
        if column is None:
            raise RuntimeError(f"草稿 [{draft_id}] 关联的字段已不存在")
        table = await self.meta_repository.get_table_by_id(column.table_id)
        qualified_name = f"{table.name}.{column.name}" if table else column.name

        # 审核人可以直接改写候选内容后再发布，改写结果同样进入审计记录。
        publish_description = (
            description if description is not None else draft.candidate_description or ""
        ).strip()
        publish_alias = self._normalize_terms(
            alias if alias is not None else draft.candidate_alias or []
        )
        publish_examples = self._normalize_terms(
            value_examples if value_examples is not None else draft.candidate_value_examples or []
        )

        previous = {
            "description": column.description,
            "alias": column.alias or [],
            "examples": column.examples or [],
            "role": column.role,
        }
        published: dict[str, Any] = {}
        changed = False

        if publish_description and publish_description != (column.description or ""):
            column.description = publish_description
            published["description"] = publish_description
            changed = True
        if publish_alias and publish_alias != (column.alias or []):
            column.alias = publish_alias
            published["alias"] = publish_alias
            changed = True
        if publish_examples and publish_examples != (column.examples or []):
            column.examples = publish_examples
            published["examples"] = publish_examples
            changed = True
        if role is not None and role != column.role:
            column.role = role
            published["role"] = role
            changed = True

        if changed:
            await self.meta_repository.save_column(column)

        draft.status = "approved"
        draft.reviewer = reviewer
        draft.review_comment = review_comment
        draft.reviewed_at = datetime.now()
        draft.published_content = published
        await self.meta_repository.save_semantic_draft(draft)

        return ReviewReport(
            draft_id=draft.id,
            qualified_name=qualified_name,
            status=draft.status,
            reviewer=reviewer,
            published=published,
            previous=previous,
        )

    async def reject(
        self,
        draft_id: str,
        reviewer: str,
        review_comment: str | None = None,
    ) -> ReviewReport:
        """审核驳回：只更新草稿状态，column_info 保持原样。"""
        self._require_reviewer(reviewer)
        draft = await self._require_pending_draft(draft_id)
        column = await self.meta_repository.get_column_by_id(draft.column_id)
        table = await self.meta_repository.get_table_by_id(column.table_id) if column else None
        qualified_name = f"{table.name}.{column.name}" if table and column else draft.column_id

        draft.status = "rejected"
        draft.reviewer = reviewer
        draft.review_comment = review_comment
        draft.reviewed_at = datetime.now()
        await self.meta_repository.save_semantic_draft(draft)

        return ReviewReport(
            draft_id=draft.id,
            qualified_name=qualified_name,
            status=draft.status,
            reviewer=reviewer,
        )

    # ==================== 查询 ====================

    async def list_drafts(self, status: str | None = None) -> list[DraftView]:
        drafts = await self.meta_repository.list_semantic_drafts(status)
        views: list[DraftView] = []
        for draft in drafts:
            column = await self.meta_repository.get_column_by_id(draft.column_id)
            table = (
                await self.meta_repository.get_table_by_id(column.table_id) if column else None
            )
            views.append(
                DraftView(
                    id=draft.id,
                    qualified_name=(
                        f"{table.name}.{column.name}" if table and column else draft.column_id
                    ),
                    status=draft.status,
                    revision=draft.revision,
                    candidate_description=draft.candidate_description or "",
                    candidate_alias=list(draft.candidate_alias or []),
                    candidate_value_examples=list(draft.candidate_value_examples or []),
                    candidate_role=draft.candidate_role,
                    model_name=draft.model_name,
                    prompt_version=draft.prompt_version,
                    reviewer=draft.reviewer,
                    review_comment=draft.review_comment,
                    created_at=draft.created_at,
                    evidence=dict(draft.evidence or {}),
                )
            )
        return views

    # ==================== 内部工具 ====================

    @staticmethod
    def _split_qualified_name(qualified_name: str) -> tuple[str, str]:
        if "." not in qualified_name:
            raise RuntimeError(f"字段标识 [{qualified_name}] 格式错误，应为 表名.字段名")
        table_name, column_name = qualified_name.split(".", 1)
        return table_name.strip(), column_name.strip()

    @staticmethod
    def _normalize_terms(terms: list[Any]) -> list[str]:
        """清洗别名／样例：去空、去重并保持原有顺序。"""
        normalized: list[str] = []
        for term in terms or []:
            text = str(term).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _require_reviewer(reviewer: str) -> None:
        if not reviewer or not reviewer.strip():
            raise RuntimeError("审核人不能为空，审核动作必须可追溯到人")

    async def _require_pending_draft(self, draft_id: str) -> ColumnSemanticDraftMySQL:
        draft = await self.meta_repository.get_semantic_draft_by_id(draft_id)
        if draft is None:
            raise RuntimeError(f"未找到草稿 [{draft_id}]")
        if draft.status != "pending":
            raise RuntimeError(f"草稿 [{draft_id}] 当前状态为 {draft.status}，只能审核 pending 草稿")
        return draft

    @staticmethod
    def _resolve_model_name(llm: Any) -> str:
        return str(
            getattr(llm, "model_name", None) or getattr(llm, "model", None) or "unknown-model"
        )

    @staticmethod
    def _extract_json(raw_content: str) -> dict[str, Any]:
        """解析模型输出，兼容代码块包裹与前后附加说明。"""
        text = (raw_content or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise RuntimeError(f"模型输出无法解析为 JSON：{raw_content[:200]}")
            return json.loads(text[start : end + 1])
