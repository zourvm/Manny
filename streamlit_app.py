from pathlib import Path
import streamlit as st

st.set_page_config(page_title="观数 · 宏观研究台", page_icon=":material/monitoring:", layout="wide")

with st.sidebar:
    st.title("观数")
    st.caption("个人宏观研究台 / 第一版")
    st.divider()

page = st.navigation({
    "研究面板": [
        st.Page("app_pages/overview.py", title="宏观概览", icon=":material/space_dashboard:", default=True),
        st.Page("app_pages/trade.py", title="进出口", icon=":material/swap_horiz:"),
        st.Page("app_pages/housing.py", title="房地产", icon=":material/apartment:"),
        st.Page("app_pages/state.py", title="国家队公开持仓", icon=":material/account_balance:"),
        st.Page("app_pages/funds.py", title="机构与杠杆观察", icon=":material/finance_mode:"),
        st.Page("app_pages/market.py", title="指数与两融对比", icon=":material/monitoring:"),
        st.Page("app_pages/etf.py", title="ETF 申赎参考", icon=":material/show_chart:"),
        st.Page("app_pages/fed.py", title="美联储预期", icon=":material/account_balance_wallet:"),
    ],
    "工作台": [
        st.Page("app_pages/help.py", title="使用说明", icon=":material/help:"),
        st.Page("app_pages/settings.py", title="数据与设置", icon=":material/database:"),
    ],
})
with st.sidebar:
    st.divider()
    st.selectbox("图表时间范围", ["近 1 年", "近 3 年", "近 5 年", "全部"], index=1, key="years")
    st.caption("指标卡展示最新有效观测；时间范围仅筛选图表。")
    st.caption("本地数据缓存 · 北京时间")
page.run()
