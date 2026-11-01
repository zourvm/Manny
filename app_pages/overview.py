import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
from core import store
from core.freshness import describe
from core.data import refresh_many
from core.ui import cached, frame, metric, line

st.title("宏观概览")
st.caption("从外需、景气和地产观察变化。先看数据，再形成自己的判断。")
left, right = st.columns([4, 2], vertical_alignment="bottom")
left.markdown("**最新有效观测**")
if right.button("刷新宏观数据", type="primary", icon=":material/refresh:"):
    with st.spinner("正在刷新进出口、PMI、房价与地产景气…"):
        results = refresh_many(["trade", "pmi", "estate", "house"])
    cached.clear()
    st.toast(f"成功 {sum(results.values())}/4 个数据集")

trade, pmi, estate = frame("trade"), frame("pmi"), frame("estate")
if not estate.empty:
    age = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - pd.Timestamp(estate["日期"].max()).date()).days
    if age > 70:
        st.warning(f"国房景气指数源仅更新至 {estate['日期'].max()}，距今 {age} 天。请结合房地产面板的城市房价观察。", icon=":material/schedule:")
with st.container(horizontal=True):
    for data, field, label, unit, dataset in zip([trade, trade, pmi, estate],
        ["当月出口额-同比增长", "当月进口额-同比增长", "制造业-指数", "最新值"],
        ["出口同比", "进口同比", "制造业 PMI", "国房景气指数"], ["%", "%", "", ""], ["trade","trade","pmi","estate"]):
        with st.container(width=220):
            metric(data, field, label, unit, dataset_name=dataset)

a, b = st.columns([1.25, 1])
with a:
    with st.container(border=True):
        st.subheader("外需走势")
        st.caption("进出口同比 / %")
        line(trade, ["当月出口额-同比增长", "当月进口额-同比增长"], "%", "中国进出口同比变化", baseline=0)
with b:
    with st.container(border=True):
        st.subheader("经济景气")
        st.caption("制造业与非制造业 / 指数点，50 为参考线")
        line(pmi, ["制造业-指数", "非制造业-指数"], "指数点", "中国 PMI 变化", baseline=50)

st.subheader("研究入口")
for col, title, text, page in zip(st.columns(3), ["房地产", "国家队公开持仓", "美联储预期"],
    ["对照新房、二手房与行业景气。", "按汇金、证金、国新、诚通、社保和养老主体比较具名披露。", "自动读取CME公开网页，比较各会议概率与历史预期。"],
    ["app_pages/housing.py", "app_pages/state.py", "app_pages/fed.py"]):
    with col, st.container(border=True):
        st.subheader(title)
        st.caption(text)
        st.page_link(page, label="打开面板", icon=":material/arrow_forward:")

with st.expander("采集状态与阅读口径"):
    rows = []
    for key, label in [("trade", "进出口"), ("pmi", "PMI"), ("estate", "国房景气"), ("house:北京:上海", "北京/上海房价")]:
        p = cached(key)
        rows.append(describe(key if key!="house:北京:上海" else key,p,title=label))
    st.dataframe(rows, hide_index=True, alt="宏观数据的统计期与采集状态")
    st.caption("同比为原数据发布口径；不同统计期不可视作同一时点的同步信号。图表缺失值不补零，最新值不等于实时值。")
