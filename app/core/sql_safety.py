"""无仓储依赖的 SQL 只读预检；不是 SQL 解析器或数据库权限边界。"""
import re


FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|grant|revoke|load|call|lock|unlock|into)\b",
    flags=re.IGNORECASE,
)
EXECUTABLE_COMMENT_PATTERN = re.compile(r"/\*(?:!|M!)", re.IGNORECASE)
SIDE_EFFECT_PATTERN = re.compile(
    r"\bFOR\s+SHARE\b|:=|"
    r"\b(?:GET_LOCK|RELEASE_LOCK|RELEASE_ALL_LOCKS|SLEEP|BENCHMARK|LOAD_FILE)\s*\(",
    flags=re.IGNORECASE,
)


class SQLSafetyPolicy:
    """保守拦截常见写入及副作用，执行账号仍须使用最小只读权限。

    规则直接匹配 SQL 文本，字符串/标识符内的关键词或分号也可能被拒绝；
    不尝试覆盖全部 MySQL 方言、存储函数及资源消耗风险。
    """

    @staticmethod
    def ensure_read_only_sql(sql: str) -> None:
        cleaned = sql.strip()
        # 仅允许一个结尾分号，不能用 rstrip(';') 吞掉多个语句分隔符。
        if cleaned.endswith(";"):
            cleaned = cleaned[:-1].strip()
        if not cleaned:
            raise ValueError("SQL 不能为空")
        if ";" in cleaned:
            raise ValueError("只允许一条查询语句")
        # MySQL / MariaDB 会执行这类注释中的代码，不能按普通注释忽略。
        if EXECUTABLE_COMMENT_PATTERN.search(cleaned):
            raise ValueError("禁止 SQL 可执行注释")
        # 合并普通注释为空白，防止 FOR/**/SHARE 等绕过多词规则。
        inspected = re.sub(r"/\*.*?\*/|--[^\n]*|#[^\n]*", " ", cleaned, flags=re.DOTALL)
        if FORBIDDEN_SQL_PATTERN.search(cleaned) or FORBIDDEN_SQL_PATTERN.search(inspected):
            raise ValueError("只允许查询，禁止 INSERT、UPDATE、DELETE、DDL、INTO 等写操作")
        if SIDE_EFFECT_PATTERN.search(cleaned) or SIDE_EFFECT_PATTERN.search(inspected):
            raise ValueError("只允许只读查询，禁止锁定、变量赋值及已知副作用函数")
        if not re.match(r"^(?:SELECT|WITH)\b", cleaned, re.IGNORECASE):
            raise ValueError("SQL 必须以 SELECT 或 WITH 开头")
