"""Verified public disclosure boundary, checked 2026-10-29."""
RULE_URL = "https://www.csrc.gov.cn/csrc/c101954/c7619929/content.shtml"
TEMPLATE_URL = "https://www.amac.org.cn/xwfb/xhyw/202603/t20260313_27409.html"
RULE_NOTE = (
    "披露规则已变化：2026-05-01 起施行的新准则第三十一条列示持有人户数、机构/个人比例等信息，"
    "不再保留普通上市 ETF 前十名持有人的常规定报强制列示要求。"
    "历史具名样本仍可查证，但不能假设能从后续中报、年报连续补齐具名持仓；缺失不代表卖出。"
)


def disclosure_notice():
    import streamlit as st
    with st.expander("ETF 披露规则变化 · 2026-05-01 起", expanded=False):
        st.markdown(RULE_NOTE)
        st.caption("货币市场基金及 REITs 等特定类型应结合专门规定和模板判断，不能笼统表述为所有基金、所有渠道均停止披露。匿名持有人类别也不能映射成中央汇金。最近规则核验：2026-10-29；预计下次更新：规则调整时，不定期。")
        st.markdown(f"[证监会公告及准则原文]({RULE_URL}) · [基金业协会 XBRL 模板]({TEMPLATE_URL})")
