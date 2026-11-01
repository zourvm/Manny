from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
import altair as alt
import streamlit as st
from core.data import refresh_etf
from core.national_team import GROUPS, matched_changes, broad_comparison
from core.disclosure import disclosure_notice
from core.ui import cached, frame, filtered, line, raw_data, stamp

st.title("ETF 申赎参考")
st.caption("辅助市场数据：观察全部投资者的 ETF 总份额变化。国家队具名披露请打开“国家队公开持仓”。")
st.page_link("app_pages/state.py", label="查看国家队公开持仓", icon=":material/account_balance:")
st.page_link("app_pages/funds.py", label="查看公募、保险、私募与 ETF 交易参考", icon=":material/finance_mode:")
st.warning("ETF 份额变动包含所有投资者的申赎，不能确认买卖主体为国家队；份额变化不等于资金金额。", icon=":material/info:")
disclosure_notice()
with st.expander("如何结合公开持仓观察"):
    st.markdown("""
    - 先看具名持仓、主体公告和统计期，确认已披露事实；定报是滞后快照。
    - 再比较多只宽基、多个采样区间的份额变化。同步增加或持续变化仅是待核验线索。
    - 份额增加可能来自套利、资产配置、联接基金、基金转换或实物申购；二级市场放量不等于净申购。
    - 本页未接入同步指数跌幅、成交额、折溢价和投资者身份，不能据此确认急跌托底、退出或国家队金额。
    """)
today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
with st.form("etf-collect"):
    a, b, c = st.columns([2, 2, 1])
    start = a.date_input("采集起点", value=today - timedelta(days=90), max_value=today)
    end = b.date_input("采集终点", value=today - timedelta(days=1), max_value=today)
    frequency = c.selectbox("采样频率", ["每日（工作日）", "每周", "每月"], index=1)
    submit = st.form_submit_button("增量采集 ETF 份额", icon=":material/sync:", type="primary")
    st.caption("仅采集未成功缓存的采样点；两路并发。节假日最多回溯 4 天，保留实际日期。单次最多 60 个采样点。")
if submit:
    if start > end:
        st.error("起点不能晚于终点")
    else:
        bar = st.progress(0.0, text="准备采集…")
        try:
            ok, failed = refresh_etf(start, end, {"每日（工作日）":"B", "每周":"W-FRI", "每月":"ME"}[frequency],
                                    lambda n, total: bar.progress(n / total, text=f"完成 {n}/{total} 个采样点"))
            cached.clear()
            bar.empty()
            st.info(f"可用 {ok} 个采样点，失败 {failed} 个。已保留所有成功的历史快照。")
        except ValueError as ex:
            bar.empty()
            st.error(str(ex))

stamp("etf")
df = filtered(frame("etf"))
st.caption("覆盖：上交所代表性宽基 ETF；非全部基金，未纳入深交所。A500 按基金简称筛选，其余为固定代码清单。")
if not df.empty:
    view = st.segmented_control("观察范围", ["多宽基对比", "逐宽基明细"], default="多宽基对比", required=True, key="etf-view")
    if view == "多宽基对比":
        comparison = broad_comparison(df)
        st.subheader("多宽基 · 份额同步观察")
        st.caption("各宽基在全部实际采样日使用固定基金篮子。归一到各自首期=100，比较变化方向；不比较资金金额，不认定国家队操作。")
        st.dataframe(comparison.groupby("宽基",as_index=False)["可比基金数"].first(),hide_index=True,alt="每个宽基全区间可比基金数量")
        valid = comparison.dropna(subset=["归一份额"])
        if valid.empty:
            st.info("全区间没有可比基金篮子，请缩短侧栏时间范围或查看逐宽基明细。")
        else:
            chart = alt.Chart(valid).mark_line(point=True).encode(x=alt.X("日期:T",title="实际采样日期"),y=alt.Y("归一份额:Q",title="首期份额=100",scale=alt.Scale(zero=False)),color="宽基:N",
                tooltip=["日期:N","宽基:N",alt.Tooltip("归一份额:Q",format=".2f"),"可比基金数:Q"])
            st.altair_chart(chart.properties(height=290),alt="各宽基固定ETF篮子的归一份额走势，比较变化方向而非资金规模")
            intervals = comparison[comparison["上期日期"].notna()].copy()
            intervals["采样区间"] = intervals["上期日期"] + " → " + intervals["日期"]
            if not intervals.empty:
                base = alt.Chart(intervals).mark_rect(cornerRadius=2).encode(x=alt.X("采样区间:N",sort=None,title=None,axis=alt.Axis(labelAngle=-35)),y=alt.Y("宽基:N",sort=GROUPS,title=None))
                known = base.transform_filter("isValid(datum['区间份额变化率（%）'])").encode(color=alt.Color("区间份额变化率（%）:Q",scale=alt.Scale(domainMid=0,scheme="redblue")),
                    tooltip=["宽基:N","采样区间:N",alt.Tooltip("区间份额变化率（%）:Q",format="+.2f"),"间隔自然日:Q","可比基金数:Q"])
                st.altair_chart((base.encode(color=alt.value("#334355")) + known).properties(height=230),alt="各宽基相邻实际采样区间的份额变化率，灰色为不可比或缺失")
                st.caption("蓝色增加、红色减少、灰色不可比；色阶以 0 为中心。周/月采样不是日净申购；间隔长短见悬停，不把跨日区间称为连续多日信号。")
            st.download_button("下载多宽基对比 CSV",comparison.to_csv(index=False).encode("utf-8-sig"),"多宽基份额对比.csv","text/csv")
        st.stop()
    group = st.selectbox("宽基指数", GROUPS)
    subset = df[df["宽基"] == group]
    if subset.empty:
        st.info("该宽基在所选区间没有有效观测。")
    else:
        dates = sorted(subset["日期"].unique())
        universes = [set(subset[subset["日期"] == d]["基金代码"]) for d in dates]
        stable = set.intersection(*universes)
        for col, label, value in zip(st.columns(3), ["最近观测基金数", "实际采样日期数", "全区间可比基金数"],
                                      [len(universes[-1]), len(dates), len(stable)]):
            col.metric(label, value, border=True)
        st.caption("可比基金指所选时间范围内每个有效日期都有数据的相同基金；避免基金新增或缺失造成假增长。")
        if stable:
            comparable = subset[subset["基金代码"].isin(stable)]
            series = comparable.groupby("日期")["基金份额（份）"].sum(min_count=len(stable)).div(1e8).reset_index(name="同一基金篮子份额")
            with st.container(border=True):
                st.subheader(group + " · 同口径份额走势")
                line(series, ["同一基金篮子份额"], "亿份", group + "可比 ETF 份额变化", height=300)
        else:
            st.info("没有全区间可比基金，请缩小时间范围，或查看逐只 ETF 数据。")
        if len(dates) >= 2:
            a, b = st.columns(2)
            d1 = a.selectbox("对比起点（实际交易日期）", dates, index=0)
            d2 = b.selectbox("对比终点（实际交易日期）", dates, index=len(dates)-1)
            if d1 >= d2:
                st.info("请选择晚于起点的终点")
            else:
                changes = matched_changes(subset, d1, d2)
                if not changes.empty:
                    display = changes[["基金代码", "基金简称_期末", "份额变化（亿份）", "份额变化率（%）"]].rename(columns={"基金简称_期末": "基金简称"})
                    st.bar_chart(display.set_index("基金简称")["份额变化（亿份）"], horizontal=True, alt="相同 ETF 在所选两个日期之间的份额变化")
                    st.dataframe(display, hide_index=True, alt="逐只 ETF 的同口径份额变化")
                    st.caption(f"两端可比 {len(changes)} 只；未匹配基金不视作清仓或新增买入。")
        else:
            st.info("至少需要两个有效采样日期才能比较份额变化。")
        raw_data(subset, "宽基ETF份额")
st.caption("脚本改造来源：[national-team-position（MIT）](https://github.com/Xiaoyuan-Liu/national-team-position)。保留逐只基金、日期、覆盖范围和失败记录，不输出推定持仓金额。")
