"""字段值采样安全策略。

决定某个 DW 字段是否允许被自动采样为枚举样例。策略同时承担安全职责
（避免个人身份信息进入 Meta 与向量库）与效果职责（避免高基数流水号
污染值召回索引）。

判定顺序：
    1. 数值型与时间型字段直接拒绝，取值不可枚举；
    2. 命中标识符、姓名、联系方式等模式直接拒绝；
    3. 只有显式列在业务枚举白名单中的字段才允许采样。
"""
import re
from dataclasses import dataclass

# 单字段最多入库的样例数量，避免 Meta 与 ES 被长尾值撑大。
MAX_SAMPLE_VALUES = 12

# 允许的最大去重取值数。超过该值认为字段基数过高，不适合作为枚举样例。
MAX_ALLOWED_CARDINALITY = 30

# 允许自动采样的业务枚举字段白名单，格式为「表名.字段名」。
ALLOWED_COLUMNS: frozenset[str] = frozenset({
    "dim_store.region",
    "dim_store.city",
    "dim_store.channel_type",
    "dim_store.store_name",
    "dim_user.city",
    "dim_user.province",
    "dim_user.gender",
    "dim_user.user_level",
    "dim_user.register_channel",
    "dim_promotion.promo_type",
    "dim_promotion.promo_name",
    "dim_product.category_level1",
    "dim_product.category_level2",
    "dim_product.brand",
    "dim_product.product_name",
    "fact_order.order_status",
})

# 明确禁止采样的字段名模式：(正则, 拒绝原因)
DENIED_NAME_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"(^|_)id$", "主键或外键编码，属于标识符而非业务枚举"),
    (r"(^|_)sn$", "业务流水号，基数高且对问数没有检索价值"),
    (r"^(user_name|manager_name|real_name|customer_name|name)$", "可能包含个人姓名"),
    (
        r"(phone|mobile|email|mail|address|addr|id_card|idcard|passwd|password|bank_card)",
        "可能包含个人联系信息或凭证",
    ),
    (r"(_time|_date|_at)$", "时间字段，取值不可枚举"),
)

# 数值型字段一律不采样：度量值属于指标计算口径，不属于枚举召回范围。
NUMERIC_TYPE_PREFIXES: tuple[str, ...] = (
    "INT",
    "BIGINT",
    "SMALLINT",
    "TINYINT",
    "DECIMAL",
    "NUMERIC",
    "FLOAT",
    "DOUBLE",
    "REAL",
)


@dataclass(frozen=True)
class SamplingDecision:
    """一次采样判定的结果与原因，原因会写入草稿 evidence 供审计。"""

    allowed: bool
    reason: str


def decide(table_name: str, column_name: str, data_type: str = "") -> SamplingDecision:
    """判断指定字段能否被自动采样为枚举样例。"""
    normalized_type = (data_type or "").upper()
    for prefix in NUMERIC_TYPE_PREFIXES:
        if normalized_type.startswith(prefix):
            return SamplingDecision(False, f"数据类型为 {normalized_type}，数值型取值不适合作为枚举样例")

    for pattern, reason in DENIED_NAME_PATTERNS:
        if re.search(pattern, column_name, flags=re.IGNORECASE):
            return SamplingDecision(False, reason)

    qualified_name = f"{table_name}.{column_name}"
    if qualified_name not in ALLOWED_COLUMNS:
        return SamplingDecision(False, "未列入业务枚举白名单，需数据开发审核后加入")

    return SamplingDecision(True, "命中业务枚举白名单，允许采样")


def is_safe_to_index(table_name: str, column_name: str, data_type: str = "") -> SamplingDecision:
    """仅执行安全拒绝规则（数值、时间、PII、标识符），不做白名单限制。

    用于 value_type=closed 的字段：是否索引由业务人员决定，但 PII 与标识符
    仍然必须被强制排除，避免个人隐私进入倒排索引。
    """
    normalized_type = (data_type or "").upper()
    for prefix in NUMERIC_TYPE_PREFIXES:
        if normalized_type.startswith(prefix):
            return SamplingDecision(False, f"数据类型为 {normalized_type}，数值型不建立值索引")

    for pattern, reason in DENIED_NAME_PATTERNS:
        if re.search(pattern, column_name, flags=re.IGNORECASE):
            return SamplingDecision(False, reason)

    return SamplingDecision(True, "通过安全校验")
