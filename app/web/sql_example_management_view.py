"""金标 SQL 管理视图：录入与查看 Few-shot SQL 样例。"""
import requests
import streamlit as st

from app.models.qdrant.sql_example_info_qdrant import CALCULATION_PATTERNS, SQL_DIALECTS


def _split_terms(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _fetch_examples(api_base: str) -> list[dict]:
    try:
        response = requests.get(f"{api_base}/sql-example/list", timeout=20)
        if response.status_code != 200:
            st.error(f"金标 SQL 接口返回异常：{response.status_code} {response.text}")
            return []
        return response.json().get("items", [])
    except requests.RequestException as exc:
        st.error(f"无法连接金标 SQL 接口，请确认 FastAPI 与 Qdrant 已启动：{exc}")
        return []


def _delete_example(api_base: str, example_id: str) -> bool:
    try:
        response = requests.delete(f"{api_base}/sql-example/{example_id}", timeout=20)
        if response.status_code == 200:
            return True
        detail = response.json().get("detail", response.text)
        st.error(f"删除失败：{detail}")
        return False
    except requests.RequestException as exc:
        st.error(f"删除接口通信异常：{exc}")
        return False


def _update_example(api_base: str, example_id: str, payload: dict) -> bool:
    try:
        response = requests.put(f"{api_base}/sql-example/{example_id}", json=payload, timeout=30)
        if response.status_code == 200:
            return True
        detail = response.json().get("detail", response.text)
        st.error(f"更新失败：{detail}")
        return False
    except requests.RequestException as exc:
        st.error(f"更新接口通信异常：{exc}")
        return False


def render_sql_example_management_page(
    api_base: str = "http://localhost:8000/api",
    embedded: bool = False,
) -> None:
    st.title("📚 金标 SQL 管理")
    st.caption("录入经过业务确认的标准 SQL，作为 Text-to-SQL 的 Few-shot 检索资产。")

    st.markdown("---")
    st.subheader("录入金标 SQL")
    with st.form("sql_example_upload_form", clear_on_submit=True):
        question = st.text_input("业务自然语言提问", placeholder="统计华东地区本月已完成订单的销售额")
        sql = st.text_area("标准 SQL", height=160, placeholder="SELECT ...")
        col1, col2, col3 = st.columns(3)
        with col1:
            pattern = st.selectbox("计算范式", CALCULATION_PATTERNS)
        with col2:
            difficulty = st.selectbox("复杂度", ["简单", "中等", "复杂"], index=1)
        with col3:
            dialect = st.selectbox("SQL 方言", SQL_DIALECTS)
        submitted = st.form_submit_button("录入并向量化", use_container_width=True)

        if submitted:
            if not question.strip() or not sql.strip():
                st.error("业务提问与标准 SQL 均不能为空。")
            else:
                payload = {
                    "question": question.strip(),
                    "sql": sql.strip(),
                    "pattern": pattern,
                    "difficulty": difficulty,
                    "dialect": dialect.strip() or "mysql",
                }
                try:
                    response = requests.post(f"{api_base}/sql-example/upload", json=payload, timeout=30)
                    if response.status_code == 200:
                        st.success("金标 SQL 已录入并建立向量索引。")
                        st.rerun()
                    else:
                        detail = response.json().get("detail", response.text)
                        st.error(f"录入失败：{detail}")
                except requests.RequestException as exc:
                    st.error(f"金标 SQL 接口通信异常：{exc}")

    st.markdown("---")
    st.subheader("已录入样例")
    examples = _fetch_examples(api_base)
    st.caption(f"共 {len(examples)} 条")
    if not examples:
        st.info("当前 Qdrant 中没有金标 SQL 样例。")
    for example in examples:
        example_id = example.get("id")
        with st.expander(f"{example.get('question', '未命名问题')}"):
            st.markdown(f"**范式**：{example.get('pattern')}　　**难度**：{example.get('difficulty')}　　**方言**：{example.get('dialect')}")
            st.markdown(f"**涉及表**：{example.get('tables')}")
            st.code(example.get("sql", ""), language="sql")

            col_del, col_edit = st.columns([1, 1])
            if col_del.button("删除", key=f"delete_sql_{example_id}", use_container_width=True):
                if _delete_example(api_base, example_id):
                    st.success("样例已删除，列表即将刷新。")
                    st.rerun()
            if col_edit.button("编辑", key=f"edit_sql_{example_id}", use_container_width=True):
                st.session_state[f"editing_sql_{example_id}"] = True

            if st.session_state.get(f"editing_sql_{example_id}"):
                with st.form(f"edit_sql_form_{example_id}"):
                    question = st.text_input("业务自然语言提问", value=example.get("question", ""))
                    sql = st.text_area("标准 SQL", value=example.get("sql", ""), height=160)
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        pattern_index = (
                            CALCULATION_PATTERNS.index(example.get("pattern"))
                            if example.get("pattern") in CALCULATION_PATTERNS
                            else 0
                        )
                        pattern = st.selectbox("计算范式", CALCULATION_PATTERNS, index=pattern_index)
                    with col2:
                        difficulty_options = ["简单", "中等", "复杂"]
                        difficulty_index = (
                            difficulty_options.index(example.get("difficulty"))
                            if example.get("difficulty") in difficulty_options
                            else 1
                        )
                        difficulty = st.selectbox("复杂度", difficulty_options, index=difficulty_index)
                    with col3:
                        dialect_index = (
                            SQL_DIALECTS.index(example.get("dialect"))
                            if example.get("dialect") in SQL_DIALECTS
                            else 0
                        )
                        dialect = st.selectbox("SQL 方言", SQL_DIALECTS, index=dialect_index)
                    submitted = st.form_submit_button("保存修改", use_container_width=True)
                    if submitted:
                        if not question.strip() or not sql.strip():
                            st.error("业务提问与标准 SQL 均不能为空。")
                        else:
                            payload = {
                                "question": question.strip(),
                                "sql": sql.strip(),
                                "pattern": pattern,
                                "difficulty": difficulty,
                                "dialect": dialect.strip() or "mysql",
                            }
                            if _update_example(api_base, example_id, payload):
                                st.session_state[f"editing_sql_{example_id}"] = False
                                st.success("样例已更新，列表即将刷新。")
                                st.rerun()
                if st.button("取消编辑", key=f"cancel_edit_sql_{example_id}", use_container_width=True):
                    st.session_state[f"editing_sql_{example_id}"] = False
                    st.rerun()
