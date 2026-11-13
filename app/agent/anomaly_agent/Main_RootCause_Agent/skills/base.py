"""Skill 的标准基类和元数据契约。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class AttributionToolContract:
    """未来归因工具的策略契约；未实现工具不会进入 Action 注册表。"""

    name: str
    purpose: str
    implemented: bool = False


@dataclass(frozen=True)
class SkillStrategy:
    """可被未来子 Agent 消费的确定性策略配置。"""

    change_inspection_required: bool = True
    ready_status: str = "ready"
    rejected_action: Literal["stop"] = "stop"
    failed_action: Literal["handle_failure"] = "handle_failure"
    dimension_source: str = "metric_context.dimensions"
    preserve_filters: bool = True
    ambiguity_action: Literal["reject"] = "reject"
    allow_dynamic_dimension_selection: bool = True
    max_analysis_rounds: int = 8
    max_dimensions: int = 6
    max_query_budget: int = 16
    max_depth: int = 3
    tool_contracts: tuple[AttributionToolContract, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    termination_conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillConfig:
    """描述一个 Skill 能处理什么，以及它受到哪些约束。

    这里先保存配置，不执行 LLM 调用或数据库查询。真正的业务执行逻辑
    应放到 Skill 对应的 service/tool 中，由后续节点显式调用。
    """

    name: str
    version: str
    description: str
    supported_intents: tuple[str, ...]
    supported_metrics: tuple[str, ...]
    instruction_file: str
    capabilities: tuple[str, ...]
    constraints: tuple[str, ...]
    related_metrics: tuple[str, ...] = ()
    business_rules: tuple[str, ...] = ()
    analysis_guidance: str = ""
    allowed_capabilities: tuple[str, ...] = ()
    enabled: bool = True
    open_question_scenarios: tuple[str, ...] = ()
    task_types: tuple[str, ...] = ()
    strategy: SkillStrategy = SkillStrategy()


class BaseSkill(ABC):
    """所有 Main_RootCause_Agent Skill 必须实现的统一接口。

    基类只规定 Skill 的身份、适用意图和能力边界；具体的策略生成、
    工具调用和结果处理由子类实现。这样注册表不需要知道具体 Skill 的
    内部实现，只通过这个统一接口加载和使用。
    """

    @property
    @abstractmethod
    def config(self) -> SkillConfig:
        """返回当前 Skill 的声明式配置。"""

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def version(self) -> str:
        return self.config.version

    @property
    def description(self) -> str:
        return self.config.description

    @property
    def supported_intents(self) -> tuple[str, ...]:
        return self.config.supported_intents

    @property
    def supported_metrics(self) -> tuple[str, ...]:
        return self.config.supported_metrics

    @property
    def related_metrics(self) -> tuple[str, ...]:
        return self.config.related_metrics

    @property
    def business_rules(self) -> tuple[str, ...]:
        return self.config.business_rules

    @property
    def analysis_guidance(self) -> str:
        return self.config.analysis_guidance

    @property
    def allowed_capabilities(self) -> tuple[str, ...]:
        return self.config.allowed_capabilities

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def open_question_scenarios(self) -> tuple[str, ...]:
        return self.config.open_question_scenarios

    @property
    def task_types(self) -> tuple[str, ...]:
        return self.config.task_types

    @property
    def strategy(self) -> SkillStrategy:
        return self.config.strategy

    @property
    def instruction_file(self) -> str:
        return self.config.instruction_file

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.config.capabilities

    @property
    def constraints(self) -> tuple[str, ...]:
        return self.config.constraints

    def load_instructions(self) -> str:
        """加载与当前 Python Skill 同目录的 Markdown 规则文件。"""

        file_name = Path(self.instruction_file)
        if file_name.name != self.instruction_file or file_name.suffix != ".md":
            raise ValueError(f"Skill instruction file must be a local .md file: {self.instruction_file}")
        implementation_path = Path(inspect.getfile(type(self))).parent
        document_path = implementation_path / file_name
        return document_path.read_text(encoding="utf-8")

    def supports_intent(self, intent: str) -> bool:
        """判断当前 Skill 是否支持 Orchestrator 已识别的意图。"""

        return intent.strip() in self.supported_intents

    def match_metric(self, metric: str) -> float:
        """返回 Skill 对解析后指标名的匹配分数，默认表示无法匹配。"""

        return 0.0

    def supports_metric(self, metric: str) -> bool:
        """判断指标是否属于主指标或关联指标。"""
        normalized = metric.strip().casefold()
        if any(name.casefold() == normalized for name in self.supported_metrics):
            return True
        return any(name.casefold() == normalized for name in self.related_metrics)

