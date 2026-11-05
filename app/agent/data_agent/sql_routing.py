"""完整问数与只取数子图共享的 SQL 恢复路由。"""


def _route_error(state: dict, success: str) -> str:
    if state.get("error") is None:
        return success
    if state.get("sql_error_kind") == "repairable" and state.get("retry_count", 0) < 3:
        return "correct_sql"
    return "translate_error"


def route_after_validation(state: dict) -> str:
    return _route_error(state, "execute_sql")


def route_after_execution(state: dict) -> str:
    return _route_error(state, "result_analysis")
