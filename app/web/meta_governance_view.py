"""字段语义审批视图：可独立启动，也可嵌入问数 Web UI。"""
import requests
import streamlit as st


def _split_terms(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _fetch_drafts(api_base: str, status: str) -> list[dict]:
    try:
        response = requests.get(
            f"{api_base}/meta-governance/semantic-drafts",
            params={"status": status},
            timeout=15,
        )
        if response.status_code != 200:
            st.error(f"接口返回异常：{response.status_code} {response.text}")
            return []
        return response.json().get("items", [])
    except requests.RequestException as exc:
        st.error(f"无法连接治理接口，请确认 FastAPI 已启动：{exc}")
        return []


def _render_draft(api_base: str, draft: dict) -> None:
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

            submitted = st.form_submit_button("提交审核", use_container_width=True)
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
    """渲染字段语义审批页面。embedded 为 True 时不占用侧边栏，供问数页面嵌入。"""
    st.title("✅ 字段语义审批台")
    st.caption("模型只负责推导候选；通过人工审批后才发布到 Meta 的 column_info。")

    status_labels = {
        "pending": "待审核",
        "approved": "已通过",
        "rejected": "已驳回",
        "all": "全部",
    }
    if embedded:
        filter_col, refresh_col = st.columns([3, 1])
        selected_status = filter_col.radio(
            "查看范围",
            list(status_labels.keys()),
            format_func=status_labels.get,
            horizontal=True,
            key="embedded_governance_status",
        )
        refresh_col.write("")
        if refresh_col.button("刷新列表", use_container_width=True):
            st.rerun()
    else:
        with st.sidebar:
            st.subheader("草稿状态")
            selected_status = st.radio(
                "查看范围",
                list(status_labels.keys()),
                format_func=status_labels.get,
            )
            if st.button("刷新列表", use_container_width=True):
                st.rerun()

    drafts = _fetch_drafts(api_base, selected_status)
    st.subheader(f"共 {len(drafts)} 条草稿")
    st.markdown("---")

    if not drafts:
        st.info("当前没有对应状态的字段语义草稿。请先运行候选生成脚本。")
    else:
        for draft in drafts:
            _render_draft(api_base, draft)
