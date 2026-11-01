import altair as alt
import pandas as pd
import streamlit as st
from core import cme,store
from core.fed_web import refresh_due,refresh_public,meeting_outcomes,PUBLIC
from core.fed_entry_ui import render_entry
from core.ui import cached, frame, raw_data
from core.freshness import show as freshness

st.title("美联储预期")
st.caption("按 FOMC 会议观察目标利率区间分布，以及市场预期随时间的变化。")
st.info("自动读取CME公开网页并填入图表，无需订阅、API密钥或手工抄数。保留网页原始日期与更新时间。", icon=":material/language:")
st.link_button("打开 CME FedWatch 公开网页", PUBLIC, icon=":material/open_in_new:")
st.link_button("查看 CME 官方使用指南", cme.GUIDE, icon=":material/help:")

left,right=st.columns([3,2],vertical_alignment="bottom")
auto=left.toggle("打开页面时自动读取公开数据",value=True,key="fed-auto-web")
force=right.button("立即自动读取并填入",icon=":material/sync:",type="primary",key="fed-refresh-web")
st.caption("页面打开或重新运行时检查更新，15分钟内最多自动请求一次；按钮可立即重读。后台关闭页面后不会持续采集。")
if force or (auto and refresh_due()):
    with st.spinner("正在自动读取CME各场会议的公开概率表…"):
        ok=refresh_public()
    cached.clear()
    if ok:st.success("已自动填入各场会议及网页提供的历史比较数据。")
    else:st.warning("公开网页读取未完成，保留上次成功数据；稍后可点击重试。")
saved=store.load("cme")
freshness("cme",saved)
if saved and saved.get("attempt") and saved["attempt"][1]:
    st.warning("最近自动读取失败，当前是已有快照："+saved["attempt"][1])
if saved and saved.get("public_audit"):
    audit=saved["public_audit"]
    st.caption(f"已读取 {audit['meeting_count']} 场会议 ｜ 网页数据日 {audit['data_date']} ｜ 本地读取 {saved['fetched_at'][:19].replace('T',' ')}（北京时间）")
    with st.expander("自动读取来源与校验"):
        st.markdown(f"来源：[CME官方中文FedWatch页面]({PUBLIC})及其公开嵌入工具。逐场切换会议，校验会议日期、概率合计与网页降息/维持/加息汇总。")
        st.json(audit,expanded=False)

render_entry(backup=True)

data = frame("cme")
if data.empty:
    with st.container(border=True):
        st.subheader("等待第一份网页快照")
        st.markdown("录入或导入真实数据后可查看 **会议 × 利率区间** 的概率分布、历史变化和相对指定基准利率的宽松概率。")
        st.caption("暂不使用样例概率填充图表。")
else:
    outcomes=meeting_outcomes(data)
    if not outcomes.empty:
        st.subheader("各场会议 · 降息 / 维持 / 加息预期")
        chart_data=outcomes.melt(["会议日期","观测日期","网页基准下限（%）"],value_vars=["降息（%）","维持（%）","加息（%）"],var_name="方向",value_name="概率（%）")
        st.altair_chart(alt.Chart(chart_data).mark_bar().encode(y=alt.Y("会议日期:N",sort="ascending",title="FOMC会议"),
            x=alt.X("概率（%）:Q",stack="zero",scale=alt.Scale(domain=[0,100])),
            color=alt.Color("方向:N",scale=alt.Scale(domain=["降息（%）","维持（%）","加息（%）"],range=["#39B8A5","#77A8EF","#EAB66B"])),
            tooltip=["会议日期:N","观测日期:N","方向:N",alt.Tooltip("概率（%）:Q",format=".1f"),"网页基准下限（%）:Q"]).properties(height=max(220,len(outcomes)*32)),alt="自动读取的各场会议相对网页基准的降息、维持与加息概率")
        st.caption("以网页Current利率为基准，比较会议后的利率水平，不代表相邻会议之间的单次调整概率。优先保留网页汇总值，舍入后合计可能略有偏差。")
        st.dataframe(outcomes,hide_index=True,alt="各场会议自动填入的降息、维持、加息概率和原始观测日")
    meeting = st.selectbox("FOMC 会议", sorted(data["会议日期"].unique()))
    history = data[data["会议日期"] == meeting].copy()
    reporting_date = st.selectbox("概率报告日", sorted(history["报告日期"].unique(), reverse=True))
    snapshot = history[history["报告日期"] == reporting_date].copy()
    snapshot["目标利率区间"] = snapshot.apply(lambda r: f"{r['利率下限（%）']:.2f}–{r['利率上限（%）']:.2f}%", axis=1)
    snapshot["概率（%）"] = snapshot["概率"] * 100
    a, b = st.columns(2)
    a.metric("会议日期", meeting, border=True)
    b.metric("报告日期", reporting_date, border=True)
    sources = snapshot["来源"].dropna().unique() if "来源" in snapshot else [cached("cme").get("source", "历史来源待核对")]
    for source in sources:
        st.caption("来源：" + str(source))
    times = snapshot["来源时间（原文）"].dropna().unique() if "来源时间（原文）" in snapshot else []
    st.caption("网页更新时间（原文）：" + "；".join(map(str,times)) if len(times) else "网页更新时间未记录；不以本地录入时间代替。")
    st.caption(f"分布合计 {snapshot['概率（%）'].sum():.2f}%；报告日期保留网页原始日期。同一会议/报告日展示最后保存的一份快照。")
    chart = alt.Chart(snapshot).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=alt.X("目标利率区间:N", sort=None, title=None),
        y=alt.Y("概率（%）:Q", scale=alt.Scale(domain=[0,100])),
        tooltip=["目标利率区间:N", alt.Tooltip("概率（%）:Q", format=".2f")])
    st.altair_chart(chart.properties(height=300), alt="指定 FOMC 会议及报告日期的目标利率概率分布")
    known=snapshot["基准利率下限（%）"].dropna().unique() if "基准利率下限（%）" in snapshot else []
    baseline = st.number_input("相对基准：目标利率下限（%，网页有值时自动填写）", min_value=0.0, max_value=30.0, value=float(known[0]) if len(known)==1 else None, step=0.25,key=f"fed-baseline-{meeting}-{reporting_date}")
    if baseline is not None:
        probability = snapshot.loc[snapshot["利率下限（%）"] < baseline - 1e-9, "概率"].sum()
        stated=snapshot["网页降息概率"].dropna().unique() if "网页降息概率" in snapshot else []
        if len(known)==1 and len(stated)==1 and abs(baseline-float(known[0]))<1e-9:
            probability=float(stated[0])
        st.metric("目标利率低于指定基准的概率", f"{probability:.2%}", border=True)
        st.caption("比较会议后利率与指定基准，不是相对上一场会议的单次降息概率。基准一致时保留网页汇总；修改基准后按已披露明细计算，舍入可能略有差异。")
    if history["报告日期"].nunique() > 1:
        history["区间"] = history.apply(lambda r: f"{r['利率下限（%）']:.2f}–{r['利率上限（%）']:.2f}%", axis=1)
        history["报告日期"] = pd.to_datetime(history["报告日期"])
        history["概率（%）"] = history["概率"] * 100
        st.subheader("同一会议的历史预期")
        st.altair_chart(alt.Chart(history).mark_line(point=True).encode(x="报告日期:T", y=alt.Y("概率（%）:Q", scale=alt.Scale(domain=[0,100])), color="区间:N", tooltip=["报告日期:T", "区间:N", "概率（%）:Q"]).properties(height=280), alt="同一会议各目标利率区间概率随报告日期的变化")
    st.caption("历史曲线仅包含已保存的观测，未录入区间不补零；每天继续记录可逐步积累历史。")
    raw_data(data, "CME利率概率")
