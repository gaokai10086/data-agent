"""字段语义治理视图：表筛选 + 已发布字段修改 + 草稿审批。

可独立启动，也可嵌入问数 Web UI 的「元数据管理」二级页面。
"""
import requests
import streamlit as st

ROLE_OPTIONS = ["primary_key", "foreign_key", "measure", "dimension"]
ROLE_LABELS = {
    "primary_key": "主键",
    "foreign_key": "外键",
    "measure": "度量",
    "dimension": "维度",
}

VALUE_TYPE_LABELS = {
    "closed": "封闭枚举",
    "open": "开放枚举",
}


def _split_terms(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _split_lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _fetch_drafts(api_base: str, status: str) -> list[dict]:
    try:
        response = requests.get(
            f"{api_base}/meta-governance/semantic-drafts",
            params={"status": status},
            timeout=15,
        )
        if response.status_code != 200:
            st.error(f"草稿接口返回异常：{response.status_code} {response.text}")
            return []
        return response.json().get("items", [])
    except requests.RequestException as exc:
        st.error(f"无法连接治理接口，请确认 FastAPI 已启动：{exc}")
        return []


def _fetch_fields(api_base: str) -> list[dict]:
    try:
        response = requests.get(f"{api_base}/meta-governance/fields", timeout=15)
        if response.status_code != 200:
            st.error(f"字段接口返回异常：{response.status_code} {response.text}")
            return []
        return response.json().get("items", [])
    except requests.RequestException as exc:
        st.error(f"无法连接治理接口，请确认 FastAPI 已启动：{exc}")
        return []


def _update_field(api_base: str, column_id: str, payload: dict) -> bool:
    try:
        response = requests.post(
            f"{api_base}/meta-governance/fields/{column_id}/update",
            json=payload,
            timeout=20,
        )
        if response.status_code == 200:
            return True
        detail = response.json().get("detail", response.text)
        st.error(f"保存失败：{detail}")
        return False
    except requests.RequestException as exc:
        st.error(f"字段接口通信异常：{exc}")
        return False


def _fetch_value_diff(api_base: str, column_id: str) -> dict | None:
    try:
        response = requests.get(
            f"{api_base}/meta-governance/fields/{column_id}/value-diff",
            timeout=30,
        )
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            st.error(f"读取 DW 取值失败：{detail}")
            return None
        return response.json()
    except requests.RequestException as exc:
        st.error(f"取值对照接口通信异常：{exc}")
        return None


def _render_field(api_base: str, field: dict) -> None:
    """渲染单个已发布字段，并提供直接修改入口。"""
    column_id = field["column_id"]
    role_label = ROLE_LABELS.get(field.get("role", ""), field.get("role") or "未知")
    value_type = field.get("value_type") or "open"
    value_type_label = VALUE_TYPE_LABELS.get(value_type, value_type)
    is_closed = value_type == "closed"

    with st.expander(
        f"{field['qualified_name']}    「{role_label}」「{value_type_label}」", expanded=False
    ):
        st.markdown(f"**字段说明**：{field.get('description') or '无'}")
        st.markdown(
            "**字段别名**：" + ("、".join(f"`{item}`" for item in field.get("alias", [])) or "无")
        )
        if is_closed:
            st.markdown(
                f"**枚举值（{len(field.get('examples', []))} 个）**："
                + ("、".join(f"`{item}`" for item in field.get("examples", [])) or "未维护")
            )
        else:
            st.caption("开放枚举：不维护枚举值，由大模型根据字段语义推导。")
        st.markdown(f"**数据类型**：`{field.get('data_type') or '未知'}`")

        if is_closed:
            if st.button("对照 DW 实际取值", key=f"diff_field_{column_id}", width="stretch"):
                # 注意：按钮的 key 由 Streamlit 自己管理，数据必须存到另一个 key，
                # 否则会触发 StreamlitWidgetAlreadyInstantiatedError。
                st.session_state[f"diff_data_{column_id}"] = _fetch_value_diff(api_base, column_id)
            diff = st.session_state.get(f"diff_data_{column_id}")
            if diff:
                left, right = st.columns(2)
                left.markdown("**Meta 已登记**")
                for item in diff.get("registered", []) or ["（空）"]:
                    left.markdown(f"- {item}")
                right.markdown("**DW 实际取值**")
                for item in diff.get("in_dw", []) or ["（空）"]:
                    unregistered = item in (diff.get("unregistered") or [])
                    right.markdown(f"- {item} ⚠️ 未登记" if unregistered else f"- {item}")
                if diff.get("unregistered"):
                    st.warning(
                        "DW 中存在未登记的取值：" + "、".join(diff["unregistered"])
                    )
                if diff.get("missing_in_dw"):
                    st.info("Meta 已登记但 DW 中不存在：" + "、".join(diff["missing_in_dw"]))

        if st.button("修改字段语义", key=f"edit_field_{column_id}", width="stretch"):
            st.session_state[f"editing_field_{column_id}"] = True

        if not st.session_state.get(f"editing_field_{column_id}"):
            return

        with st.form(f"field_form_{column_id}"):
            description = st.text_area(
                "字段说明",
                value=field.get("description") or "",
                key=f"desc_{column_id}",
                height=80,
            )
            alias = st.text_input(
                "字段别名（英文逗号分隔）",
                value=",".join(field.get("alias", [])),
                key=f"alias_{column_id}",
            )
            selected_value_type = st.radio(
                "取值类型",
                ["closed", "open"],
                index=0 if is_closed else 1,
                format_func=lambda value: (
                    f"{VALUE_TYPE_LABELS[value]}（人工维护枚举值）"
                    if value == "closed"
                    else f"{VALUE_TYPE_LABELS[value]}（大模型推导）"
                ),
                horizontal=True,
                key=f"value_type_{column_id}",
            )
            examples = ""
            if selected_value_type == "closed":
                examples = st.text_area(
                    "枚举值（一行一个）",
                    value="\n".join(field.get("examples", [])),
                    key=f"examples_{column_id}",
                    height=120,
                )
            else:
                st.caption("开放枚举不需要维护枚举值，保存后会清空该字段的枚举值。")
            role = st.selectbox(
                "字段角色",
                ROLE_OPTIONS,
                index=ROLE_OPTIONS.index(field["role"]) if field.get("role") in ROLE_OPTIONS else 3,
                format_func=lambda value: f"{ROLE_LABELS.get(value, value)}（{value}）",
                key=f"role_{column_id}",
            )
            reviewer = st.text_input("修改人", key=f"reviewer_{column_id}")
            comment = st.text_input("修改原因", key=f"comment_{column_id}")
            submitted = st.form_submit_button("保存修改", width="stretch")

            if not submitted:
                return
            if not reviewer.strip():
                st.error("修改人不能为空。")
                return

            payload = {
                "reviewer": reviewer.strip(),
                "review_comment": comment.strip() or None,
                "description": description.strip(),
                "alias": _split_terms(alias),
                "examples": _split_lines(examples),
                "role": role,
                "value_type": selected_value_type,
            }
            if _update_field(api_base, column_id, payload):
                st.session_state[f"editing_field_{column_id}"] = False
                st.success("字段语义已更新。")
                st.rerun()

        if st.button("取消编辑", key=f"cancel_field_{column_id}", width="stretch"):
            st.session_state[f"editing_field_{column_id}"] = False
            st.rerun()


def _render_draft(api_base: str, draft: dict) -> None:
    """渲染模型候选草稿，并提供通过/驳回入口。"""
    with st.expander(f"{draft['qualified_name']}   「{draft['status']}」", expanded=False):
        st.markdown(f"**字段角色**：`{draft.get('candidate_role') or '未建议'}`")
        st.markdown(f"**候选说明**：{draft.get('candidate_description') or '无'}")
        st.markdown(
            "**候选别名**："
            + ("、".join(f"`{item}`" for item in draft.get("candidate_alias", [])) or "无")
        )
        st.markdown(
            "**真实样例**："
            + ("、".join(f"`{item}`" for item in draft.get("candidate_value_examples", [])) or "无")
        )
        st.markdown(
            f"**生成模型**：{draft.get('model_name')}　　**Prompt 版本**：{draft.get('prompt_version')}"
        )

        with st.expander("查看推导依据", expanded=False):
            st.json(draft.get("evidence", {}))

        if draft.get("reviewer"):
            st.info(
                f"已由 {draft['reviewer']} 审核"
                + (f"，意见：{draft['review_comment']}" if draft.get("review_comment") else "")
            )

        if draft["status"] != "pending":
            return

        with st.form(f"review_form_{draft['id']}"):
            action = st.radio(
                "审核动作",
                ["通过并发布", "驳回"],
                horizontal=True,
                key=f"action_{draft['id']}",
            )
            reviewer = st.text_input("审核人", key=f"reviewer_{draft['id']}")
            review_comment = st.text_area("审核意见", key=f"comment_{draft['id']}", height=68)

            description = None
            alias = None
            examples = None
            if action == "通过并发布":
                description = st.text_area(
                    "发布说明（可修改）",
                    value=draft.get("candidate_description") or "",
                    key=f"desc_{draft['id']}",
                )
                alias = st.text_input(
                    "别名（英文逗号分隔，可修改）",
                    value=",".join(draft.get("candidate_alias", [])),
                    key=f"alias_{draft['id']}",
                )
                examples = st.text_input(
                    "真实样例（英文逗号分隔，可修改）",
                    value=",".join(draft.get("candidate_value_examples", [])),
                    key=f"examples_{draft['id']}",
                )

            submitted = st.form_submit_button("提交审核", width="stretch")
            if not submitted:
                return

            if not reviewer.strip():
                st.error("审核人不能为空。")
                return

            if action == "通过并发布":
                payload = {
                    "reviewer": reviewer.strip(),
                    "review_comment": review_comment.strip() or None,
                    "description": description.strip() if description else None,
                    "alias": _split_terms(alias) if alias else None,
                    "examples": _split_terms(examples) if examples else None,
                }
                endpoint = f"{api_base}/meta-governance/semantic-drafts/{draft['id']}/approve"
            else:
                payload = {
                    "reviewer": reviewer.strip(),
                    "review_comment": review_comment.strip() or None,
                }
                endpoint = f"{api_base}/meta-governance/semantic-drafts/{draft['id']}/reject"

            try:
                response = requests.post(endpoint, json=payload, timeout=20)
                if response.status_code == 200:
                    st.success("审核已完成，列表即将刷新。")
                    st.rerun()
                else:
                    detail = response.json().get("detail", response.text)
                    st.error(f"审核失败：{detail}")
            except requests.RequestException as exc:
                st.error(f"审核接口通信异常：{exc}")


def render_meta_governance_page(
    api_base: str = "http://localhost:8000/api",
    embedded: bool = False,
) -> None:
    """渲染字段语义治理页面。embedded 为 True 时不占用侧边栏，供问数页面嵌入。"""
    st.title("✅ 字段语义治理")
    st.caption("先按表筛选，再查看和修改字段语义；模型候选需人工审核后才发布。")

    all_fields = _fetch_fields(api_base)
    table_names = sorted({item["table_name"] for item in all_fields})

    if embedded:
        filter_col, refresh_col = st.columns([3, 1])
        selected_table = filter_col.selectbox(
            "选择表", ["全部"] + table_names, key="embedded_field_table"
        )
        refresh_col.write("")
        if refresh_col.button("刷新", width="stretch"):
            st.rerun()
    else:
        with st.sidebar:
            st.subheader("表筛选")
            selected_table = st.selectbox("选择表", ["全部"] + table_names)
            if st.button("刷新", width="stretch"):
                st.rerun()

    table_filter = "" if selected_table == "全部" else selected_table
    fields = [item for item in all_fields if not table_filter or item["table_name"] == table_filter]

    st.subheader(f"已发布字段（{len(fields)} 个）")
    st.caption("这些语义已经写入 column_info，Agent 正在使用。可以直接修改，修改会留审计记录。")
    if not fields:
        st.info("当前没有匹配的字段。")
    else:
        for field in fields:
            _render_field(api_base, field)

    st.markdown("---")
    st.subheader("模型候选草稿")
    draft_status = st.radio(
        "草稿状态",
        ["pending", "approved", "rejected", "all"],
        format_func={
            "pending": "待审核",
            "approved": "已通过",
            "rejected": "已驳回",
            "all": "全部",
        }.get,
        horizontal=True,
        key="embedded_draft_status",
    )
    drafts = _fetch_drafts(api_base, draft_status)
    if table_filter:
        drafts = [item for item in drafts if item["qualified_name"].startswith(f"{table_filter}.")]
    st.caption(f"共 {len(drafts)} 条草稿")
    if not drafts:
        st.info("当前没有匹配的草稿。")
    else:
        for draft in drafts:
            _render_draft(api_base, draft)
