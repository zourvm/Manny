import streamlit as st
from core.ui import dataset_header, source_note, metric, line, raw_data

st.title("房地产")
st.caption("城市住宅价格与全国行业景气分开观察。")
view = st.segmented_control("观察维度", ["城市房价", "行业景气"], default="城市房价", required=True)
if view == "城市房价":
    cities = ["北京", "上海", "广州", "深圳", "杭州", "南京", "成都", "武汉", "西安", "重庆", "天津", "苏州", "合肥", "宁波", "郑州"]
    a, b = st.columns(2)
    c1 = a.selectbox("城市一", cities)
    c2 = b.selectbox("城市二", cities, index=1)
    df, name = dataset_header("house", "新房与二手房", {"city1": c1, "city2": c2})
    source_note("house")
    kind = st.segmented_control("住宅类型", ["新建商品住宅", "二手住宅"], default="新建商品住宅", required=True)
    basis = st.segmented_control("涨幅口径", ["同比", "环比"], default="同比", required=True)
    field = kind + "价格指数-" + basis
    if not df.empty:
        derived = df.copy()
        derived["涨跌幅（%）"] = derived[field] - 100
        for col, city in zip(st.columns(2), [c1, c2]):
            with col:
                metric(derived[derived["城市"] == city], "涨跌幅（%）", city + " · " + kind + basis, "%")
        wide = derived.pivot(index="日期", columns="城市", values="涨跌幅（%）").reset_index()
        with st.container(border=True):
            st.subheader(kind + "价格变化")
            line(wide, list(dict.fromkeys([c1, c2])), "%", "所选城市住宅价格涨跌幅", baseline=0, height=340)
    raw_data(df, "住宅价格指数")
else:
    df, name = dataset_header("estate", "国房景气指数")
    source_note("estate")
    metric(df, "最新值", "最新景气指数")
    with st.container(border=True):
        line(df, ["最新值"], "指数点", "全国房地产开发景气指数", height=340)
    raw_data(df, "国房景气指数")
st.caption("第一版覆盖房价与国房景气；房地产投资、销售、新开工和竣工待接入。")
