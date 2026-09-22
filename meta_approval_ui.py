"""字段语义审批独立入口。

启动：
    streamlit run meta_approval_ui.py --server.port 8502
"""
import streamlit as st

from app.web.meta_governance_view import render_meta_governance_page

st.set_page_config(page_title="字段语义审批", page_icon="✅", layout="wide")
render_meta_governance_page(embedded=False)
