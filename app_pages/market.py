from datetime import date,timedelta
import json
import altair as alt
import pandas as pd
import streamlit as st
from core import store
from core.market_compare import INDICES,DEFAULT,CATALOG,MARGIN_KEY,refresh_many,align,normalized,comparison_stats,records
from core.freshness import describe,show as freshness

st.title("指数与两融对比")
st.caption("比较宽基、板块与行业的历史强弱，再与沪深两融余额对照。日收盘价格指数，非实时行情。")
st.info("两融曲线代表沪深市场汇总，不是所选行业或指数成分股的融资余额。上证、深证、创业板及科创板指数的样本可能交叉，不能相加。",icon=":material/info:")

def option(symbol):return INDICES[symbol]["name"]+" · "+symbol

broad=[s for s in INDICES if INDICES[s]["group"]=="宽基与板块"]
industries=[s for s in INDICES if INDICES[s]["group"]=="行业指数"]
preset=st.segmented_control("观察组合",["主要板块","大小盘","行业对比","自选"],default="主要板块",required=True,key="market-preset")
presets={"主要板块":DEFAULT,"大小盘":["sh000016","sh000300","sh000905","sh000852","csi932000","sh000510"],
         "行业对比":["sh000300","sh000990","sh000991","sh000993","sz399986","sz399975"],"自选":DEFAULT}
if st.session_state.get("market-previous-preset")!=preset:
    st.session_state["market-broad"]=[s for s in presets[preset] if s in broad]
    st.session_state["market-industry"]=[s for s in presets[preset] if s in industries]
    st.session_state["market-previous-preset"]=preset
picked=st.multiselect("宽基与板块指数",broad,format_func=option,key="market-broad")
sector=st.multiselect("行业指数（可搜索名称、代码）",industries,format_func=option,key="market-industry")
symbols=picked+sector
today=date.today();yesterday=today-timedelta(days=1)

with st.expander("采集 / 更新历史数据"):
    with st.form("market-refresh"):
        a,b=st.columns(2)
        acquire_start=a.date_input("采集起点",date(today.year-5,1,1),min_value=date(2010,1,1),max_value=yesterday)
        acquire_end=b.date_input("采集终点",yesterday,min_value=date(2010,1,1),max_value=yesterday)
        include_margin=st.checkbox("同时更新沪深两融历史",value=True)
        submitted=st.form_submit_button("更新所选指数与两融",type="primary")
        st.caption("仅采集已结束日期。逐指数保存成功快照，失败保留旧数据；此次区间替换该指数当前快照，更早版本仍保存在本机。")
    if submitted:
        try:
            with st.spinner("正在读取历史指数与两融汇总，逐项校验…"):
                results=refresh_many(symbols,acquire_start.isoformat(),acquire_end.isoformat(),include_margin)
            st.success(f"完成：{sum(r['状态']=='成功' for r in results)}/{len(results)} 个数据集成功")
            st.dataframe(results,hide_index=True,alt="本次逐指数与两融获取结果")
        except Exception as ex:st.error(str(ex))

if not symbols:
    st.info("选择至少一个宽基或行业指数开始比较。")
    st.stop()
payloads={s:store.load("index:"+s) for s in symbols}
missing=[INDICES[s]["name"] for s,p in payloads.items() if not p]
if missing:
    st.warning("尚未采集："+"、".join(missing)+"。请在上方展开采集；以下仅展示有数据的指数。")
payloads={s:p for s,p in payloads.items() if p}
if not payloads:st.stop()

range_name=st.segmented_control("对比区间",["近3月","近6月","近1年","近3年","近5年","全部","自定义"],default="近1年",required=True,key="market-range")
available_start=min(r["日期"] for p in payloads.values() for r in p["records"])
if range_name=="自定义":
    a,b=st.columns(2)
    start=a.date_input("对比起点",max(date.fromisoformat(available_start),today-timedelta(days=365)),key="market-start")
    end=b.date_input("对比终点",yesterday,key="market-end")
    start,end=start.isoformat(),end.isoformat()
else:
    days={"近3月":92,"近6月":183,"近1年":365,"近3年":1096,"近5年":1827}
    start=(today-timedelta(days=days[range_name])).isoformat() if range_name in days else available_start
    end=yesterday.isoformat()
if start>end:
    st.warning("对比起点不能晚于终点。")
    st.stop()
margin=store.load(MARGIN_KEY)
margin_field=st.selectbox("市场杠杆对照",["两融余额（亿元）","融资余额（亿元）","融券余额（亿元）"],key="market-margin-field")
include=st.toggle("叠加沪深市场两融（统一共同日期）",value=True,key="market-overlay")
if include and not margin:st.warning("暂无两融历史，当前先展示指数；上方可同时获取两融历史。")
common,coverage=align(payloads,start,end,margin if include and margin else None,margin_field)
if len(common)<2:
    st.warning("所选区间至少需要两个共同日期。请扩大范围、更新缺失数据或关闭两融叠加查看指数。")
    st.dataframe(coverage,hide_index=True,alt="所选指数实际日期覆盖")
    st.stop()

focal=st.selectbox("重点观察指数",list(payloads),format_func=option,key="market-focus")
stats=comparison_stats(common)
rebased=normalized(common)
first,last=common.index[0],common.index[-1]
st.caption(f"实际共同区间：{first} → {last}，{len(common)} 个共同观测日。只取交集，不向前填充或把缺失补零；侧栏时间范围不改变本页独立区间。")
with st.container(horizontal=True):
    st.metric("共同观测日",len(common),border=True)
    ret=(common[focal].iloc[-1]/common[focal].iloc[0]-1)*100
    st.metric(INDICES[focal]["name"]+"区间涨跌",f"{ret:+.2f}%",border=True)
    if "市场两融对照" in common:
        st.metric("期末市场"+margin_field.split("（")[0],f"{common['市场两融对照'].iloc[-1]:,.2f} 亿元",border=True)

display={s:INDICES[s]["name"] for s in payloads}
display["市场两融对照"]="沪深市场 · "+margin_field.split("（")[0]
long=rebased.rename(columns=display).rename_axis("日期").reset_index().melt("日期",var_name="序列",value_name="起点100")
long["类别"]=long["序列"].map(lambda s:"市场两融" if s.startswith("沪深市场 · ") else "指数")
with st.container(border=True):
    st.subheader("历史强弱 · 同日起点 = 100")
    selection=alt.selection_point(fields=["序列"],bind="legend")
    chart=alt.Chart(alt.Data(values=records(long))).mark_line(strokeWidth=2).encode(
        x=alt.X("日期:T",title=None),y=alt.Y("起点100:Q",title="共同起点=100",scale=alt.Scale(zero=False)),
        color=alt.Color("序列:N",scale=alt.Scale(scheme="tableau20"),legend=alt.Legend(orient="bottom",title=None,columns=3,labelLimit=160)),
        strokeDash=alt.StrokeDash("类别:N",scale=alt.Scale(domain=["指数","市场两融"],range=[[1,0],[7,4]]),legend=None),
        opacity=alt.condition(selection,alt.value(1),alt.value(.15)),
        tooltip=["日期:N","序列:N",alt.Tooltip("起点100:Q",format=",.2f")]).add_params(selection).properties(height=370)
    st.altair_chart(chart,alt="各指数与沪深市场两融余额在相同日期起点归一后的历史走势")
    st.caption("指数归一值=当日收盘÷共同起点收盘×100；两融同样按余额归一。点击图例突出曲线。两融增加表示余额扩大，不代表流入某一板块。")

view=st.segmented_control("进一步对比",["区间强弱","点位与余额","变动相关","数据与来源"],default="区间强弱",required=True,key="market-detail")
if view=="区间强弱":
    st.subheader("同一区间 · 指数涨跌比较")
    bar=alt.Chart(stats).mark_bar(cornerRadiusEnd=3).encode(y=alt.Y("指数:N",sort="-x",title=None),x="区间涨跌（%）:Q",
        color=alt.condition(alt.datum['区间涨跌（%）']>=0,alt.value('#39B8A5'),alt.value('#E58483')),
        tooltip=["指数:N","共同起点:N","共同终点:N",alt.Tooltip("区间涨跌（%）:Q",format="+.2f"),alt.Tooltip("区间最大回撤（%）:Q",format=".2f")]).properties(height=max(230,len(stats)*29))
    st.altair_chart(bar,alt="统一起止日的指数涨跌幅，避免不同成立日期造成比较误差")
    st.dataframe(stats,hide_index=True,alt="指数同区间涨跌、回撤及变动相关统计")
elif view=="点位与余额":
    st.subheader(INDICES[focal]["name"]+"点位与市场余额")
    data=common.rename_axis("日期").reset_index()
    price=alt.Chart(data).mark_line(color="#39B8A5").encode(x=alt.X("日期:T",title=None),y=alt.Y(focal+":Q",title="指数收盘点位",scale=alt.Scale(zero=False)),tooltip=["日期:N",alt.Tooltip(focal+":Q",title="收盘点位",format=",.2f")]).properties(height=240)
    if "市场两融对照" in data:
        balance=alt.Chart(data).mark_line(color="#E7B45D").encode(x=alt.X("日期:T",title=None),y=alt.Y("市场两融对照:Q",title="沪深市场"+margin_field,scale=alt.Scale(zero=False)),tooltip=["日期:N",alt.Tooltip("市场两融对照:Q",title=margin_field,format=",.2f")]).properties(height=190)
        st.altair_chart(alt.vconcat(price,balance).resolve_scale(x="shared"),alt="重点指数收盘点位与沪深市场余额上下对齐，分别标注单位")
    else:st.altair_chart(price,alt="重点指数历史收盘点位")
    st.caption("上下图单位和纵轴范围不同，视觉斜率不可直接比较。")
elif view=="变动相关":
    st.subheader("指数涨跌与两融余额变动")
    st.caption("对相邻共同观测计算百分比变化，再计算Pearson相关系数；不对点位和余额水平直接求相关。少于20对或任一序列无变化时留空。共同日期有缺口时是区间变动，不能称为日收益。相关不说明因果、领先性或预测能力。")
    if "市场两融对照" not in common:st.info("开启两融叠加后可查看变动关系。")
    else:
        changes=common[[focal,"市场两融对照"]].pct_change(fill_method=None).mul(100).iloc[1:].rename(columns={focal:"指数区间涨跌（%）","市场两融对照":"余额区间变化（%）"}).rename_axis("日期").reset_index()
        changes["前次观测日期"]=list(common.index[:-1])
        st.altair_chart(alt.Chart(changes).mark_circle(size=40,opacity=.55).encode(x="余额区间变化（%）:Q",y="指数区间涨跌（%）:Q",tooltip=["前次观测日期:N","日期:N",alt.Tooltip("余额区间变化（%）:Q",format=".3f"),alt.Tooltip("指数区间涨跌（%）:Q",format=".3f")]).properties(height=310),alt="相同观测间隔的指数涨跌与市场余额变化散点，不代表因果关系")
        st.dataframe(stats[["指数","变动相关系数","变动配对样本"]],hide_index=True,alt="各指数涨跌与市场余额百分比变化的配对相关系数")
else:
    st.subheader("来源、覆盖与计算")
    st.dataframe(coverage,hide_index=True,alt="每个指数在当前筛选范围的原始起止日期与观测数")
    st.caption(CATALOG["note"]+" 目录核验："+CATALOG["checked_on"]+"。行业为中证指数口径，与国家队页面的财报行业分类不同，不直接拼接。")
    for url in CATALOG["sources"]:st.markdown(f"[指数编制与代码核验入口]({url})")
    for symbol,p in payloads.items():
        with st.expander(option(symbol)+" · 获取链与数据审计"):
            st.json(p.get("audit",{}),expanded=False)
    if margin:
        with st.expander("沪深两融历史 · 来源与交易所交叉核验"):
            for warning in margin.get("audit",{}).get("warnings",[]):st.caption(warning)
            st.json(margin.get("audit",{}),expanded=False)

st.subheader("数据时效")
freshness("index:"+focal,payloads[focal])
if include and margin:freshness(MARGIN_KEY,margin)
with st.expander("全部所选指数的获取日期与预计更新"):
    st.dataframe(pd.DataFrame([describe("index:"+s,p) for s,p in payloads.items()]),hide_index=True,alt="所选指数逐项的截至日、获取时间及预计更新")
    st.caption("各指数独立采集，日期可能不同；上述走势图仅使用共同日期，截短范围不改写源数据截至日。")
with st.expander("下载图表数据"):
    export=common.rename(columns={**{s:INDICES[s]['name']+'（'+s+'）' for s in payloads},"市场两融对照":"沪深市场 · "+margin_field}).rename_axis("日期").reset_index()
    st.download_button("下载共同日期原始点位与余额 CSV",export.to_csv(index=False).encode("utf-8-sig"),"指数与两融共同日期.csv","text/csv")
    st.download_button("下载统一起点数据 CSV",long.to_csv(index=False).encode("utf-8-sig"),"指数与两融起点100.csv","text/csv")
    st.download_button("下载区间比较结果 CSV",stats.to_csv(index=False).encode("utf-8-sig"),"指数与两融区间比较.csv","text/csv")
