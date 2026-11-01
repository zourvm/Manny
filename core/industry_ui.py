"""Industry view for the disclosed state holdings page."""
import altair as alt
import pandas as pd
import streamlit as st
from core import store
from core.freshness import show as freshness,state_payload
from core.state_industry import MAP_KEY, industry_map, industry_snapshot, industry_changes, attach_industries, refresh_industries


@st.cache_data(ttl=300,max_entries=2,show_spinner=False)
def _cached_map(scope):
    return industry_map()


def cached_map():return _cached_map(str(store.data_dir()))


cached_map.clear = _cached_map.clear


def render_industries(data, groups, window_open):
    st.subheader("行业配置与增减持")
    st.caption("仅统计已披露的具名 A 股持仓，不穿透 ETF。行业分布使用期末市值；增减持使用相同账户、股票、披露口径的数量变化。")
    mapping = cached_map()
    stocks = data[(data["资产类型"]=="股票") & data["主体"].isin(groups)].copy()
    if stocks.empty:
        st.info("所选主体暂无可见个股持仓，请选择资金主体或先采集持仓。")
        return
    if st.toggle("隐藏中央汇金投资本体账户，观察其他账户",key="industry-hide-huijin",value=False):
        stocks = stocks[stocks["账户名称"]!="中央汇金投资有限责任公司"]
    st.caption("该筛选只按账户名称排除，不认定其全部持仓都是战略投资；汇金资产管理等其他账户仍保留。")
    if stocks.empty:
        st.info("筛选后暂无个股持仓。")
        return
    basis = st.selectbox("行业分析披露口径",sorted(stocks["披露口径"].unique()),key="industry-basis")
    stocks = stocks[stocks["披露口径"]==basis]
    periods = sorted(stocks["报告期"].unique())
    full = [p for p in periods if not window_open(p)]
    default = full[-1] if full else periods[-1]
    left,right = st.columns(2)
    end = right.selectbox("行业期末 / 分布报告期",periods,index=periods.index(default),key="industry-end")
    earlier = [p for p in periods if p<end]
    start = left.selectbox("行业对比期初",earlier,index=len(earlier)-1,key="industry-start") if earlier else None
    freshness("state:"+end,state_payload(end),observed=end,title="期末国家队具名持仓")
    with st.expander("期初持仓与行业分类的获取时间 / 预计更新"):
        if start:freshness("state:"+start,state_payload(start),observed=start,title="期初国家队具名持仓（历史比较）")
        freshness(MAP_KEY,store.load(MAP_KEY),title="行业分类映射")
    if window_open(end) or (start and window_open(start)):
        st.warning("所选报告期仍在披露窗口内，覆盖可能不完整。不能把未披露或样本减少解释为减持。")
    snapshot = stocks[stocks["报告期"]==end]
    attached = attach_industries(snapshot,mapping)
    n = len(attached)
    classified = int((attached["行业"]!="未分类").sum())
    summary = industry_snapshot(snapshot,mapping)
    total = summary["可见市值（亿元）"].sum(min_count=1)
    with st.container(horizontal=True):
        for label,value in [("期末可见记录",f"{n:,}"),("行业匹配覆盖",f"{classified/n:.1%}"),("已匹配行业数",attached.loc[attached["行业"]!="未分类","行业"].nunique())]:
            with st.container(width=180):
                st.metric(label,value,border=True)
        with st.container(width=260):
            st.metric("期末可见市值（亿元）","—" if pd.isna(total) else f"{total:,.2f}",border=True)
    saved = store.load(MAP_KEY)
    if saved:
        st.caption(f"分类：{saved['classification']} ｜ 来源财报期 {saved['source_period']} ｜ 采集于 {saved['fetched_at'][:19].replace('T',' ')}（北京时间）")
        st.caption("同一采集版行业映射用于回看各期，非历史时点行业分类。侧栏时间范围不改变本页报告期。")
        if saved.get("attempt") and saved["attempt"][1]:
            st.warning("行业分类最近刷新失败，保留已有映射。")
    else:
        st.warning("尚无行业映射，股票暂列“未分类”。请在本页底部采集行业分类。")
    if classified<n:
        st.caption(f"{n-classified} 条持仓未匹配行业，保留在“未分类”；缺失市值不补零。行业市值占比以有市值的可见样本为分母。")
    metric = st.segmented_control("行业增减持指标",["期初价折算参考额","增减持记录数"],default="期初价折算参考额",required=True,key="industry-metric")
    st.caption("参考额 =（期末股数−期初股数）×（期初持仓市值÷期初股数），再按行业求和。它剔除了直接使用两期市值差额的价格影响，但未剔除送转、划转、换购等影响，不是实际成交额或现金净流入。")
    a,b = st.columns(2)
    with a, st.container(border=True):
        st.subheader("期末行业分布 · 前 15 行业")
        plotted = summary.dropna(subset=["可见市值（亿元）"]).head(15)
        if plotted.empty:
            st.info("本期没有可用市值。")
        else:
            chart = alt.Chart(plotted).mark_bar(cornerRadiusEnd=3).encode(
                y=alt.Y("行业:N",sort="-x",title=None,axis=alt.Axis(labelLimit=130,labelOverlap=False)),
                x=alt.X("可见市值（亿元）:Q",title="可见市值（亿元）"),color=alt.value("#77A8EF"),
                tooltip=["行业:N",alt.Tooltip("可见市值（亿元）:Q",format=",.2f"),alt.Tooltip("可见市值占比（%）:Q",format=".2f"),"股票数量:Q","持仓记录:Q","有市值记录:Q"])
            st.altair_chart(chart.properties(height=430),alt="期末具名持仓按行业汇总的可见股票市值，最多显示十五个行业")
        st.caption("长期银行战略持股可能占据较高比重；可用上方账户筛选观察其他账户。")
    sectors = details = None
    with b, st.container(border=True):
        st.subheader("两期行业增减持 · 前 15 行业")
        if not start:
            st.info("至少需要两个报告期才能比较增减持。")
        else:
            sectors,details = industry_changes(stocks,mapping,start,end)
            if metric=="期初价折算参考额":
                plotted = sectors.dropna(subset=["净数量变动参考额（亿元）"]).copy()
                plotted = plotted.loc[plotted["净数量变动参考额（亿元）"].abs().nlargest(15).index]
                x = alt.X("净数量变动参考额（亿元）:Q",title="数量变动参考额（亿元）")
                color = alt.condition("datum['净数量变动参考额（亿元）'] >= 0",alt.value("#39B8A5"),alt.value("#E58483"))
                chart = alt.Chart(plotted).mark_bar(cornerRadiusEnd=3).encode(x=x,y=alt.Y("行业:N",sort="-x",title=None,axis=alt.Axis(labelLimit=130,labelOverlap=False)),color=color,
                    tooltip=["行业:N",alt.Tooltip("净数量变动参考额（亿元）:Q",format="+,.3f"),"增持记录:Q","减持记录:Q","两期可比记录:Q","估值覆盖记录:Q",alt.Tooltip("可比数量变动率（%）:Q",format="+.2f")])
            else:
                plotted = sectors[sectors["两期可比记录"]>0].copy()
                plotted["变动记录"] = plotted["增持记录"]+plotted["减持记录"]
                plotted = plotted.nlargest(15,"变动记录")
                plotted = plotted.melt(id_vars=["行业"],value_vars=["增持记录","减持记录"],var_name="方向",value_name="记录数")
                plotted["有向记录数"] = plotted["记录数"].where(plotted["方向"]=="增持记录",-plotted["记录数"])
                chart = alt.Chart(plotted).mark_bar().encode(x=alt.X("有向记录数:Q",title="← 减持记录数 ｜ 增持记录数 →",stack=None),y=alt.Y("行业:N",title=None,axis=alt.Axis(labelOverlap=False)),color=alt.Color("方向:N",scale=alt.Scale(domain=["增持记录","减持记录"],range=["#39B8A5","#E58483"]),legend=None),tooltip=["行业:N","方向:N","记录数:Q"])
            if plotted.empty:
                st.info("没有可比的有效记录；新披露和未披露记录不视为买入或清仓。")
            else:
                st.altair_chart(chart.properties(height=430),alt="同一账户与股票两期均披露的行业数量增减持对比，绿色增加红色减少")
            st.caption(f"{start} → {end}；只比较两期均披露记录。记录数按账户×股票计数，不能当作股票数量或资金规模。")
    if sectors is not None:
        with st.container(horizontal=True):
            for label,value in [("两期可比记录",int(sectors["两期可比记录"].sum())),("可按期初价折算",int(sectors["估值覆盖记录"].sum())),("新披露（不算买入）",int(sectors["新披露记录"].sum())),("未披露（不算卖出）",int(sectors["本期未披露"].sum()))]:
                st.metric(label,value,border=True)
        st.subheader("行业增减持汇总")
        st.dataframe(sectors,hide_index=True,alt="各行业可比记录数量、增减持参考额、估值覆盖和披露缺口",height=300)
        st.download_button("下载行业增减持 CSV",sectors.to_csv(index=False).encode("utf-8-sig"),"行业增减持.csv","text/csv")
        st.subheader("查看行业内的具体股票")
        chosen = st.selectbox("查看行业",["全部行业"]+sorted(details["行业"].unique()),key="industry-drill")
        selected = details if chosen=="全部行业" else details[details["行业"]==chosen]
        fields=["行业","主体","账户名称","标的代码","标的名称","持有数量_期初","持有数量_期末","数量变化","数量变化率（%）","期初参考价（元）","数量变动参考额（元）","披露状态"]
        st.dataframe(selected[fields],hide_index=True,alt="行业内相同账户股票持仓数量变化及新披露或未披露状态",height=320)
        st.download_button("下载行业内股票明细 CSV",selected[fields].to_csv(index=False).encode("utf-8-sig"),"行业股票变动.csv","text/csv")
    with st.expander("完整行业分布与分类来源 / 刷新"):
        st.dataframe(summary,hide_index=True,alt="全部行业可见市值、占比、股票数和数据覆盖")
        st.download_button("下载行业分布 CSV",summary.to_csv(index=False).encode("utf-8-sig"),"行业分布.csv","text/csv")
        st.caption("行业标签来自公开财报聚合页，不冒充申万一级等其他分类。更新映射会按新版标签重算历史图，不会改写持仓快照。")
        source_period = st.selectbox("行业分类来源财报期",periods,index=periods.index(default),key="industry-source-period")
        if st.button("采集 / 更新行业分类",key="industry-refresh"):
            with st.spinner("正在读取公开行业分类，最多等待 120 秒…"):
                ok = refresh_industries(source_period)
            if ok:
                cached_map.clear()
                st.rerun()
            else:
                st.error("本次分类刷新失败，保留原映射。请稍后重试。")
        if saved:
            st.link_button("查看行业数据来源",saved["source"])
