from datetime import date
import calendar
import io
import pandas as pd
import altair as alt
import streamlit as st

from core import store
from core.freshness import show as freshness,state_payload
from core.disclosure import disclosure_notice, RULE_NOTE
from core.state_holdings import GROUPS, ALIASES, load_all, load_events, compare, refresh_period, validate_import, save_import

st.title("国家队公开持仓")
st.caption("中央汇金、证金、国新、诚通、社保与养老资金 · 以具名公开披露跟踪配置变化")
st.info("这里展示已披露持仓。两期数量差额不是逐日资金流向；没有进入前十名的持仓无法完整观察。", icon=":material/info:")
disclosure_notice()
st.page_link("app_pages/etf.py", label="结合宽基 ETF 份额观察间接线索", icon=":material/show_chart:")


@st.cache_data(ttl=60, max_entries=4, show_spinner=False)
def _holdings(scope):
    return load_all()


def holdings():return _holdings(str(store.data_dir()))


holdings.clear = _holdings.clear


def window_open(period):
    d = date.fromisoformat(period)
    if d.month == 12:
        deadline = date(d.year + 1, 4, 30)
    else:
        month = {3:4,6:8,9:10}.get(d.month, d.month)
        deadline = date(d.year, month, calendar.monthrange(d.year, month)[1])
    return date.today() <= deadline


def table(df, key):
    cols = {"原文链接": st.column_config.LinkColumn("披露原文", display_text="打开原文"),
            "核验入口": st.column_config.LinkColumn("报告检索入口", display_text="检索报告")}
    st.dataframe(df, hide_index=True, column_config=cols, alt="具名公开持仓记录与披露来源", height=360)
    st.download_button("下载当前明细 CSV", df.to_csv(index=False).encode("utf-8-sig"), key + ".csv", "text/csv", key=key)


groups = st.pills("资金主体", GROUPS, selection_mode="multi", default=GROUPS, key="state-groups")
view = st.segmented_control("观察方式", ["行业观察", "持仓对比", "两期变动", "公开公告", "数据与方法"], default="行业观察", required=True, key="state-view")

if view == "行业观察":
    from core.industry_ui import render_industries
    render_industries(holdings(),groups,window_open)
    st.stop()

if view in ["持仓对比", "两期变动"]:
    data = holdings()
    if not groups:
        st.info("请选择至少一个资金主体")
        st.stop()
    data = data[data["主体"].isin(groups)]
    asset = st.segmented_control("资产类型", ["股票", "ETF"], default="股票", required=True, key="state-asset")
    data = data[data["资产类型"] == asset]
    if asset == "股票":
        st.caption("股票观察范围为 A 股；排除 B 股证券页面，避免同一公司股东列表重复汇总。市值沿用数据源披露口径。")
    if data.empty:
        st.info("所选主体暂无该类可见持仓。可以更换主体，或在“数据与方法”采集、导入。未检出不代表没有持仓。")
        st.stop()
    basis = st.selectbox("披露口径", sorted(data["披露口径"].unique()), key="state-basis")
    data = data[data["披露口径"] == basis]
    periods = sorted(data["报告期"].unique())
    full = [p for p in periods if not window_open(p)]
    default = full[-1] if full else periods[-1]
    if asset == "ETF":
        st.warning("ETF 当前为两个基金的已核验历史样本，截至 2025 年末。新规后不能保证更新具名名单；仅在自愿披露、专项公告等确有具名原文时补充。只比较同一只 ETF 的份额，不跨基金相加成资金。")
        fund = st.selectbox("ETF 标的", sorted(data["标的代码"].unique()),
            format_func=lambda c: c + " · " + data.loc[data["标的代码"] == c, "标的名称"].iloc[0], key="state-fund")
        data = data[data["标的代码"] == fund]
        periods = sorted(data["报告期"].unique())
        default = periods[-1]
    st.caption("全局侧栏时间范围不改变本页报告期。本页按具体报告期比较，不将不同期混合。")

    if view == "持仓对比":
        period = st.selectbox("报告期", periods, index=periods.index(default), key="state-period")
        freshness("state-etf-history" if asset=="ETF" else "state:"+period,state_payload(period,asset),observed=period,title="当前所选具名持仓")
        snapshot = data[data["报告期"] == period].copy()
        if window_open(period):
            st.warning("本报告期仍在常规披露窗口内。当前只是已披露样本，不能据此认定持仓减少或完整比较主体规模。")
        st.caption(f"报告期 {period}　｜　所选记录公告日期 {snapshot['公告日期'].min()} 至 {snapshot['公告日期'].max()}　｜　{basis}")
        saved = store.load("state:" + period) if asset == "股票" else None
        if saved:
            st.caption("本地采集于 " + saved["fetched_at"][:19].replace("T"," ") + "（北京时间）")
            if saved.get("attempt") and saved["attempt"][1]:
                st.warning("最近刷新失败，当前保留成功快照；详情见数据与方法。")
        with st.container(horizontal=True):
            for label, value in [("可见持仓记录", len(snapshot)), ("标的数量", snapshot["标的代码"].nunique()), ("具名账户", snapshot["账户名称"].nunique())]:
                with st.container(width=180):
                    st.metric(label, value, border=True)
            if asset == "股票":
                value = snapshot["持仓市值（元）"].sum(min_count=1) / 1e8
                with st.container(width=280):
                    st.metric("可见股票市值（亿元）", "—" if pd.isna(value) else f"{value:,.2f}", border=True)
        absent = sorted(set(groups) - set(snapshot["主体"]))
        if absent:
            st.caption("本期未在所选可见名单检出：" + "、".join(absent) + "。这不是零持仓。")
        if asset == "股票":
            plotted = snapshot.groupby("主体", as_index=False)["持仓市值（元）"].sum(min_count=1)
            plotted["市值（亿元）"] = plotted["持仓市值（元）"] / 1e8
            chart = alt.Chart(plotted).mark_bar(cornerRadiusEnd=4).encode(
                y=alt.Y("主体:N", sort="-x", title=None), x=alt.X("市值（亿元）:Q"), color=alt.Color("主体:N", legend=None),
                tooltip=["主体:N", alt.Tooltip("市值（亿元）:Q", format=",.2f")])
            with st.container(border=True):
                st.subheader("同一期 · 各主体可见股票市值")
                st.altair_chart(chart.properties(height=250), alt="同一报告期各主体在已披露样本中的股票持仓市值")
                st.caption("市值由数量与价格共同决定；不同主体可能有不同披露覆盖，不代表其全部资产规模。")
            top = snapshot.groupby(["标的代码", "标的名称"])["持仓市值（元）"].sum(min_count=1).nlargest(15).reset_index()
            tiles = snapshot[snapshot["标的代码"].isin(top["标的代码"])].groupby(["主体", "标的代码", "标的名称"], as_index=False)["持仓市值（元）"].sum(min_count=1)
            tiles["标的"] = tiles["标的名称"] + " " + tiles["标的代码"]
            tiles["市值（亿元）"] = tiles["持仓市值（元）"] / 1e8
            grid = pd.MultiIndex.from_product([groups, tiles["标的"].unique()], names=["主体", "标的"]).to_frame(index=False)
            grid = grid.merge(tiles[["主体", "标的", "市值（亿元）"]], how="left", on=["主体", "标的"])
            heat = alt.Chart(grid).mark_rect(cornerRadius=3).encode(x=alt.X("主体:N", title=None, sort=groups),
                y=alt.Y("标的:N", title=None, sort=top["标的名称"].str.cat(top["标的代码"], sep=" ").tolist()),
                color=alt.condition("isValid(datum['市值（亿元）'])", alt.Color("市值（亿元）:Q", scale=alt.Scale(scheme="tealblues", type="sqrt")), alt.value("#243241")),
                tooltip=["主体:N", "标的:N", alt.Tooltip("市值（亿元）:Q", format=",.2f")])
            with st.container(border=True):
                st.subheader("持仓交集 · 前 15 个样本标的")
                st.altair_chart(heat.properties(height=max(250, len(top)*26)), alt="主体与样本股票的持仓热力对比，深灰表示未检出而非零持仓")
                st.caption("颜色越深表示可见市值越高；深灰表示未检出，不补成零。悬停查看具体主体、标的与市值。")
        else:
            plotted = snapshot.copy()
            plotted["份额（亿份）"] = plotted["持有数量"] / 1e8
            plotted["账户"] = plotted["账户名称"].str.replace("有限责任公司", "", regex=False).str.replace("有限公司", "", regex=False)
            chart = alt.Chart(plotted).mark_bar(cornerRadiusEnd=4).encode(y=alt.Y("账户:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220)),
                x="份额（亿份）:Q", color="主体:N", tooltip=["主体:N", "账户名称:N", alt.Tooltip("份额（亿份）:Q", format=",.4f"), "持有比例（%）:Q"])
            with st.container(border=True):
                st.subheader("同一只 ETF · 具名持有人份额")
                st.altair_chart(chart.properties(height=max(220,len(snapshot)*42)), alt="同一 ETF 各具名账户的期末持有份额")
            if len(periods) > 1:
                history = data.copy()
                history["份额（亿份）"] = history["持有数量"] / 1e8
                history["报告期"] = pd.to_datetime(history["报告期"])
                chart = alt.Chart(history).mark_line(point=True).encode(x="报告期:T", y="份额（亿份）:Q", color="账户名称:N",
                    tooltip=["报告期:T", "账户名称:N", "份额（亿份）:Q"])
                st.altair_chart(chart.properties(height=260), alt="同一 ETF 各具名账户在公开报告期的持有份额历史")
                st.caption("只画实际披露点。没有该账户记录的报告期并不代表零持仓。")
        st.subheader("持仓明细与核验")
        st.caption("股票是第三方聚合数据，原文入口需进一步检索核验；ETF 样本提供原始报告直链。")
        table(snapshot.sort_values(["主体", "持有数量"], ascending=[True,False]), "state-holdings")
    else:
        if len(periods) < 2:
            st.info("至少需要两个报告期；请在“数据与方法”采集或导入上一期。")
            st.stop()
        a,b = st.columns(2)
        end = b.selectbox("期末报告期", periods, index=periods.index(default), key="state-end")
        earlier = [p for p in periods if p < end]
        if not earlier:
            st.info("请选择有上一期数据的期末报告期")
            st.stop()
        start = a.selectbox("期初报告期", earlier, index=len(earlier)-1, key="state-start")
        freshness("state-etf-history" if asset=="ETF" else "state:"+end,state_payload(end,asset),observed=end,title="期末具名持仓")
        with st.expander("查看期初数据的获取时间与更新预期"):
            freshness("state-etf-history" if asset=="ETF" else "state:"+start,state_payload(start,asset),observed=start,title="期初具名持仓（历史比较）")
        if window_open(start) or window_open(end):
            st.warning("所选报告期仍可能未披露齐全。新出现或消失的记录不等于实际新买入或卖出。")
        changes = compare(data,start,end)
        both = changes[changes["披露状态"] == "两期均披露"].copy()
        new = changes[changes["披露状态"] == "新披露（非确认新买入）"]
        missing = changes[changes["披露状态"] == "本期未披露"]
        with st.container(horizontal=True):
            for label, value in [("两期可比记录",len(both)),("新披露记录",len(new)),("本期未披露",len(missing))]:
                st.metric(label,value,border=True)
        st.caption("增减仅针对相同账户、标的和披露口径。数量变化尚未剔除送转、拆分、划转、换购等影响，不等于现金买卖。")
        unit = "万股" if asset == "股票" else "亿份"
        divisor = 1e4 if asset == "股票" else 1e8
        both["数量变化（"+unit+"）"] = both["数量变化"] / divisor
        candidates = both.dropna(subset=["数量变化率（%）"])
        show_zero = st.toggle("包含持有数量未变的记录", value=False)
        if not show_zero:
            candidates = candidates[candidates["数量变化"].abs() > .001]
        candidates = candidates.loc[candidates["数量变化率（%）"].abs().nlargest(20).index].copy()
        if not candidates.empty:
            candidates["记录"] = candidates["标的名称"] + " · " + candidates["主体"] + " #" + (candidates.index+1).astype(str)
            chart = alt.Chart(candidates).mark_bar().encode(y=alt.Y("记录:N", sort="-x", title=None), x="数量变化率（%）:Q",
                color=alt.condition("datum['数量变化率（%）'] >= 0",alt.value("#39B8A5"),alt.value("#E58483")),
                tooltip=["账户名称:N","标的名称:N","标的代码:N",alt.Tooltip("数量变化率（%）:Q",format=".2f"),"数量变化:Q"])
            with st.container(border=True):
                st.subheader("可比记录 · 持有数量变化率")
                st.altair_chart(chart.properties(height=max(220,len(candidates)*26)), alt="两期均有具名披露的相同账户与标的的持有数量变化率")
        else:
            st.info("当前筛选下没有非零可比变化；可打开“包含持有数量未变的记录”。")
        display = both[["主体","账户名称","标的名称","标的代码","持有数量_期初","持有数量_期末","数量变化（"+unit+"）","数量变化率（%）","披露状态"]]
        st.dataframe(display,hide_index=True,alt="两期均披露记录的持有数量对比",height=300)
        a,b = st.columns(2)
        for col, label, df in [(a,"新披露 · 不能直接认定新买入",new),(b,"本期未披露 · 不能直接认定清仓",missing)]:
            with col, st.expander(label + f"（{len(df)} 条）"):
                st.dataframe(df[["主体","账户名称","标的名称","标的代码","披露状态"]],hide_index=True,alt=label)
        st.download_button("下载两期对比 CSV", changes.drop(columns="_merge").to_csv(index=False).encode("utf-8-sig"),"国家队两期持仓对比.csv","text/csv")

elif view == "公开公告":
    events = load_events()
    import json
    freshness("state-events",json.loads((store.ROOT/"resources/state_events.json").read_text(encoding="utf-8")))
    st.caption("人工核验的历史事件选录，非完整公告流。近百亿元、超500亿元等模糊金额或累计资金使用口径不转成精确净流入，也不相加。")
    events = events[events["主体"].isin(groups)] if not events.empty else events
    if events.empty:
        st.info("所选主体暂无已整理的事件；可在“数据与方法”查看公开查询入口。")
    else:
        events = events.copy()
        events["日期"] = pd.to_datetime(events["公告日期"])
        timeline = alt.Chart(events).mark_circle(size=150).encode(x=alt.X("日期:T", title="公告日期"), y=alt.Y("主体:N", title=None),color=alt.Color("主体:N",legend=None),
            tooltip=["公告日期:N","主体:N","标题:N","行为:N","金额说明:N"],href="来源链接:N")
        st.altair_chart(timeline.properties(height=240).interactive(),alt="各主体已核验公开公告时间线，可点击查看来源")
        for _, row in events.sort_values("公告日期",ascending=False).iterrows():
            with st.container(border=True):
                st.markdown(f"**{row['公告日期']} · {row['主体']}**")
                st.markdown(row["标题"])
                st.caption(row["行为"] + " ｜ " + row["金额说明"])
                st.caption(row["摘要"])
                st.link_button("查看披露来源",row["来源链接"],icon=":material/open_in_new:")

else:
    st.subheader("采集具名个股持仓")
    freshness("state-import",store.load("state-import"),title="用户补充披露数据")
    st.caption("读取所选报告期已公开的十大流通股东名单，再本地匹配账户；单期最多等待 180 秒。只更新该期成功快照，不覆盖历史报告期。")
    today = date.today()
    options = sorted([date(y,m,d).isoformat() for y in range(today.year-2,today.year+1) for m,d in [(3,31),(6,30),(9,30),(12,31)] if date(y,m,d)<=today],reverse=True)
    with st.form("state-refresh"):
        chosen = st.multiselect("采集报告期",options,default=[p for p in [f"{today.year}-03-31",f"{today.year}-06-30"] if p in options])
        collect = st.form_submit_button("采集 / 刷新所选报告期",type="primary",icon=":material/sync:")
    if collect:
        if not chosen:
            st.info("请选择报告期")
        else:
            bar = st.progress(0.0)
            results = []
            for i,p in enumerate(chosen):
                bar.progress(i/len(chosen),text="正在采集 " + p)
                results.append((p,refresh_period(p)))
            holdings.clear()
            bar.empty()
            st.dataframe([{"报告期":p,"结果":"成功" if ok else "失败，保留旧数据"} for p,ok in results],hide_index=True,alt="报告期刷新结果")
    st.subheader("导入 ETF 或补充股票披露")
    st.caption("ETF 已放入两个基金的具名历史样本。新规后不保证定报提供具名名单，仅导入确有具名原文的自愿披露、专项公告或历史报告。机构类别汇总不作为具名持仓导入；导入前预览，其他历史保留。")
    template="账户名称,资产类型,标的代码,标的名称,报告期,公告日期,持有数量,持仓市值（元）,持有比例（%）,披露口径,原文链接\n"
    st.download_button("下载持仓导入模板",template.encode("utf-8-sig"),"国家队具名持仓模板.csv","text/csv")
    upload=st.file_uploader("上传具名持仓 CSV",type=["csv"],key="state-upload")
    if upload:
        try:
            imported=validate_import(pd.read_csv(upload,dtype={"标的代码":str},encoding="utf-8-sig"))
            st.dataframe(imported.head(15),hide_index=True,alt="具名持仓导入预览")
            if st.button("合并保存持仓",type="primary"):
                save_import(imported)
                holdings.clear()
                st.success("已保存；用户导入数据保留待核验标记。")
        except Exception as ex:
            st.error(str(ex))

    st.subheader("从哪里查，查什么")
    st.dataframe([
        {"目标":"个股持仓","入口":"东方财富股东明细 → 巨潮/交易所原文复核","看哪里":"十大股东 / 十大流通股东","频率":"季报、半年报、年报"},
        {"目标":"ETF具名历史持仓","入口":"历史定报；具名自愿披露/专项公告","看哪里":"新规前前十名持有人；后续仅按实际具名原文","频率":"2026-05-01起不保证连续可得"},
        {"目标":"宽基ETF间接观察","入口":"ETF申赎参考页 / 交易所份额数据","看哪里":"同一基金份额、跨宽基同步变化","频率":"日/周/月采样；不能识别资金主体"},
        {"目标":"明确增减持","入口":"巨潮、交易所和主体官网","看哪里":"增减持公告、权益变动、实施进展","频率":"不定期"},
    ],hide_index=True,alt="不同持仓信息的公开查询方法与频率")
    with st.container(horizontal=True):
        st.link_button("东方财富股东分析","https://data.eastmoney.com/gdfx/")
        st.link_button("巨潮资讯","https://www.cninfo.com.cn/new/index")
        st.link_button("上交所基金公告","https://www.sse.com.cn/disclosure/fund/announcement/")
    st.caption("看基金持有人是查‘谁持有ETF’；基金投资组合是查‘ETF买了哪些股票’，两者不能混用。匿名‘机构1/机构2’不自动映射成国家队。")
    with st.expander("主体与账户识别规则"):
        st.dataframe([{"主体":g,"匹配名称":"；".join(a)} for g,a in ALIASES.items()]+[
            {"主体":"中央汇金","匹配名称":"含汇金资管单一资产管理计划"},
            {"主体":"中国证金","匹配名称":"含中证金融资产管理计划"},
            {"主体":"中国诚通","匹配名称":"北京诚旸投资有限公司-具名产品"},
            {"主体":"社保基金","匹配名称":"全国社会保障基金理事会；含社保基金且以组合结尾"},
            {"主体":"基本养老基金","匹配名称":"含基本养老保险基金且以组合结尾"}],hide_index=True,alt="资金主体与具名账户匹配规则")
        st.caption("按账户名称归类，仅覆盖已识别账户；不穿透全部子公司，不把国有背景基金公司旗下产品一概归为国家队。社保与养老独立统计。")
    with st.expander("计算与披露边界"):
        st.markdown("""
        - 只有同一账户、标的、报告期口径的记录才比较数量；不同口径不混算。
        - 新进入可见名单叫“新披露”；消失叫“本期未披露”，不补零或认定清仓。
        - 未剔除送转、拆分、划转和实物换购，数量差额不直接等于买卖行为。
        - 市值随价格变动，不把市值差额称为净资金流入；没有披露的精确金额不计算。
        - 个股持仓源是第三方聚合数据，需原文复核；报告检索入口不冒充原文直链。
        - ETF原文样本截至2025年末。新规后具名持有人披露存在结构性缺口，不能仅靠补采定报解决。
        - 基金年报通常年末后三个月内披露，中期报告上半年结束后两个月内披露；季报不保证具名持有人完整。
        - 个股季报是期末快照。披露后账户可能已调仓；退出前十不等于清仓，小幅变动也可能不可见。
        - 社保基金年度报告主要提供总体运作信息，不是完整逐股账户持仓清单；本页组合持股来自上市公司披露。
        - 稳市、长期配置与个人投资目标可能不同，历史持仓及份额线索不能直接转为跟随买卖建议。
        """)
        st.markdown(RULE_NOTE)
        st.link_button("基金定报新规及原文","https://www.csrc.gov.cn/csrc/c101954/c7619929/content.shtml")
    with st.expander("本地采集状态"):
        logs=[r for r in store.status() if str(r["数据集"]).startswith("state:")]
        if logs:
            st.dataframe(logs,hide_index=True,alt="国家队持仓采集状态")
