import io
import pandas as pd
import streamlit as st
from core import store
from core.freshness import show as freshness
from core.data import records, validate_industry
from core.ui import cached, frame, dataset_header, source_note, metric, line, raw_data, filtered

st.title("进出口")
st.caption("分开看总量、增速与结构，保留币种、分类和统计口径。")
view = st.segmented_control("观察维度", ["总量与增速", "行业 / 商品结构"], default="总量与增速", required=True)

if view == "总量与增速":
    df, name = dataset_header("trade", "中国货物贸易")
    source_note("trade")
    for col, field, label in zip(st.columns(3), ["当月出口额-金额", "当月进口额-金额", "当月贸易差额"], ["当月出口", "当月进口", "当月贸易差额"]):
        with col:
            metric(df, field, label, " 亿美元")
    mode = st.segmented_control("统计口径", ["当月", "年初累计"], default="当月", required=True)
    prefix = "当月" if mode == "当月" else "累计"
    a, b = st.columns(2)
    with a, st.container(border=True):
        st.subheader(mode + "金额")
        line(df, [prefix + "出口额-金额", prefix + "进口额-金额"], "亿美元", mode + "进出口金额")
    with b, st.container(border=True):
        st.subheader(mode + "同比")
        line(df, [prefix + "出口额-同比增长", prefix + "进口额-同比增长"], "%", mode + "进出口同比", baseline=0)
    st.caption("一二月合并发布或缺失时，沿用源表，不拆分、不推算。")
    raw_data(df, "进出口总量")
else:
    st.subheader("行业 / 商品结构")
    st.info("第一版尚未接通自动行业细分源。可导入海关或自有数据；HS 商品分类与行业分类分别保留。", icon=":material/upload_file:")
    template = "日期,分类口径,类别,方向,金额,币种,单位,统计口径,来源\n"
    st.download_button("下载空白 CSV 模板", template.encode("utf-8-sig"), "行业进出口模板.csv", "text/csv")
    upload = st.file_uploader("导入行业 / 商品数据", type=["csv"], key="industry-upload")
    if upload:
        try:
            data = validate_industry(pd.read_csv(upload, encoding="utf-8-sig"))
            st.caption(f"校验通过，{len(data)} 条。确认后保存在本地。")
            st.dataframe(data.head(10), hide_index=True, alt="待导入的分类贸易数据预览")
            if st.button("保存导入", type="primary"):
                store.save("industry", {"records": records(data), "source": "用户导入，各行保留原始来源"})
                cached.clear()
                st.success("已保存")
        except Exception as ex:
            st.error(str(ex))
    data = frame("industry")
    freshness("industry",store.load("industry"))
    if not data.empty:
        # Never silently combine HS levels, currencies, units or cumulative data.
        for column in ["分类口径", "币种", "单位", "统计口径", "方向", "来源"]:
            value = st.selectbox(column, sorted(data[column].unique()), key="industry-" + column)
            data = data[data[column] == value]
        data = filtered(data)
        if not data.empty:
            latest = st.selectbox("统计期", sorted(data["日期"].unique(), reverse=True))
            snap = data[data["日期"] == latest]
            st.bar_chart(snap.set_index("类别")["金额"], horizontal=True, alt="同一口径下各类别的进出口金额")
            raw_data(data, "行业进出口")
