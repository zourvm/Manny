"""Streamlit research views: provenance and chart units stay visible."""
import json
from datetime import date
import altair as alt
import pandas as pd
import streamlit as st
from core import store
from core.freshness import show as freshness
from core.state_industry import industry_map, industry_snapshot, industry_changes, attach_industries


def audit_panel(payload,key):
    if not payload:return
    audit=payload.get("audit",{})
    cutoff=audit.get("data_cutoff","未知")
    st.caption(f"报告/观测期至 {cutoff} ｜ 采集于 {payload.get('fetched_at','')[:19].replace('T',' ')}（北京时间）")
    if payload.get("attempt") and payload["attempt"][1]:
        st.warning("最近刷新失败，正在展示上次成功数据："+payload["attempt"][1])
    for warning in audit.get("warnings",[]):
        st.caption(warning)
    with st.expander("来源、披露滞后与数据审计",key="audit-"+key):
        st.markdown("**事实**：源站披露或聚合的记录。**计算**：图表汇总及两期可比数量变动。**推断**：需要结合原文与其他证据，不能当作实时资金流。")
        st.write("公告日期缺失时仅显示报告期距今天数，不估算实际披露日期。具名股东数据按各条公告日期计算滞后。")
        if key=="public-holdings":
            st.markdown("[已抽查：华夏成长000001的2026年一季报](https://fund.chinaamc.com/upload/resources/file/2026/04/22/e102fd6f341f4e4badd99b1fc55facaf.pdf)，中际旭创20万股、1.13882亿元，与转换后记录一致。单项抽查不代表全表已核验。")
        for source in audit.get("sources",[]):
            if source.get("source_url"):st.markdown(f"[公开来源 / 核验入口]({source['source_url']})")
        st.json({k:v for k,v in audit.items() if k!="rows"},expanded=False)
        st.download_button("下载数据与研究口径 JSON",json.dumps({"records":payload["records"],"audit":audit},ensure_ascii=False,indent=2),key+".json","application/json",key="download-audit-"+key)


def bar(frame,value,title,color=None,height=310,xmax=None):
    if frame.empty or frame[value].notna().sum()==0:
        st.info(title+"：暂无可用数值")
        return
    fields=["行业:N",alt.Tooltip(value+":Q",format="+,.2f")]
    encoding={"y":alt.Y("行业:N",sort="-x",title=None),"x":alt.X(value+":Q",title=value),"tooltip":fields}
    if xmax is not None:encoding["x"]=alt.X(value+":Q",title=value,scale=alt.Scale(domain=[0,xmax]))
    if color:encoding["color"]=alt.Color(color+":N",scale=alt.Scale(domain=["买入","卖出"],range=["#39B8A5","#E58483"]),legend=None)
    else:encoding["color"]=alt.condition(alt.datum[value]>=0,alt.value("#39B8A5"),alt.value("#E58483"))
    st.altair_chart(alt.Chart(frame).mark_bar().encode(**encoding).properties(height=height,title=title),alt=title)


def holdings_view(frame,prefix,payload=None):
    if frame.empty:
        st.info("尚无成功采集的具名持仓。使用下方采集表单获取公开数据。")
        return
    periods=sorted(frame["报告期"].unique())
    a,b=st.columns(2)
    end=a.selectbox("持仓报告期",periods,index=len(periods)-1,key=prefix+"-end")
    choices=[p for p in periods if p<end]
    start=b.selectbox("对比报告期",["不比较",*choices],index=len(choices) if choices else 0,key=prefix+"-start")
    key="research:fund-holdings" if prefix=="public" else "research:"+prefix+":"+end
    source=payload if payload is not None else store.load(key)
    freshness(key,{**source,"historical":end<periods[-1]} if source else None,observed=end,title="当前所选持仓报告期")
    with st.expander("期初数据与行业映射的获取时间 / 预计更新"):
        if start!="不比较":
            old=source if prefix=="public" else store.load("research:"+prefix+":"+start)
            freshness(key if prefix=="public" else "research:"+prefix+":"+start,{**old,"historical":True} if old else None,observed=start,title="期初持仓（历史比较）")
        freshness("state-industries",store.load("state-industries"))
    selected=frame[frame["报告期"]==end]
    accounts=sorted(frame["账户名称"].unique())
    picked=st.multiselect("筛选具名账户（留空显示全部可见账户）",accounts,key=prefix+"-accounts")
    if picked:
        frame=frame[frame["账户名称"].isin(picked)];selected=frame[frame["报告期"]==end]
    metrics=st.columns(3)
    metrics[0].metric("本期可见账户",selected["账户名称"].nunique(),border=True)
    metrics[1].metric("本期可见股票",selected["标的代码"].nunique(),border=True)
    metrics[2].metric("报告期距今",f"{(date.today()-date.fromisoformat(end)).days} 天",border=True)
    st.caption("显示的是可见样本，账户数、股票数及行业市值均非全市场规模。基金净值占比与股东流通股占比分母不同。")
    mapping=industry_map()
    if mapping.empty:
        st.info("尚无行业映射，请先在国家队页面刷新行业分类。")
    else:
        snap=industry_snapshot(selected,mapping)
        l,r=st.columns(2)
        with l:bar(snap.head(15),"可见市值（亿元）","行业持仓分布 · 可见样本前15")
        with r:
            if start!="不比较":
                sectors,changes=industry_changes(frame,mapping,start,end)
                valid=sectors.dropna(subset=["净数量变动参考额（亿元）"])
                valid=valid.loc[valid["净数量变动参考额（亿元）"].abs().sort_values(ascending=False).index].head(15)
                bar(valid,"净数量变动参考额（亿元）","行业数量变动 · 期初价格参考额")
            else:st.info("选择更早的报告期，比较行业增减持。")
        st.caption("行业标签使用已缓存的东方财富财报行业映射，采集时分类用于所有报告期，不代表历史时点分类；未匹配股票保留为未分类。")
        if start!="不比较":
            common=changes[changes["披露状态"]=="两期均披露"]
            st.caption(f"对比 {start} → {end}：两期可比 {len(common)} 条；新披露 {(changes['披露状态']=='新披露（非确认新买入）').sum()} 条；本期未披露 {(changes['披露状态']=='本期未披露').sum()} 条。")
            st.caption("参考额 = 股数变化 × 期初市值/期初股数，仅计算两期均披露的同一账户和股票。未作送转、拆分与账户划转调整，不能解释为实际买卖金额。新披露不等于买入，未披露不等于清仓。")
            with st.expander("行业增减持明细"):
                st.dataframe(sectors,hide_index=True)
                st.dataframe(changes,hide_index=True)
                st.download_button("下载两期账户变化 CSV",changes.to_csv(index=False).encode("utf-8-sig"),prefix+"-changes.csv","text/csv",key=prefix+"-download-changes")
    search=st.text_input("查找股票或账户",key=prefix+"-search")
    detail=selected.copy()
    if search:
        detail=detail[detail[["标的代码","标的名称","账户名称"]].astype(str).apply(lambda c:c.str.contains(search,regex=False)).any(axis=1)]
    st.dataframe(detail,hide_index=True,column_config={"核验入口":st.column_config.LinkColumn("核验入口")},alt="所选报告期的具名持仓与来源")
    st.download_button("下载所选持仓 CSV",detail.to_csv(index=False).encode("utf-8-sig"),prefix+"-holdings.csv","text/csv",key=prefix+"-download")


def major_trades_view(buy,sell,code):
    available=[]
    for payload in [buy,sell]:
        if payload:available.extend(payload["records"])
    if not available:
        st.info("尚无重大买卖数据。下方可按基金代码和年份采集。")
        return
    frame=pd.DataFrame(available)
    periods=sorted(frame["报告期"].unique())
    period=st.selectbox("重大买卖累计报告期",periods,index=len(periods)-1,key="major-period")
    frame=frame[frame["报告期"]==period]
    for kind,payload in [("fund-buy",buy),("fund-sell",sell)]:
        freshness(f"research:{kind}:{code}",payload,observed=period)
    with st.expander("行业映射的获取时间与预计更新"):
        freshness("state-industries",store.load("state-industries"))
    st.caption(f"累计期间：{period[:4]}-01-01 → {period}。半年与全年名单不能相减来还原下半年交易。")
    frame=attach_industries(frame,industry_map())
    frame["累计成交金额（亿元）"]=frame["累计成交金额（元）"]/1e8
    xmax=max(float(frame.groupby(["行业","方向"])["累计成交金额（亿元）"].sum().max())*1.1,.01)
    left,right=st.columns(2)
    for direction,col in [("买入",left),("卖出",right)]:
        part=frame[frame["方向"]==direction]
        with col:
            st.metric("已披露重大"+direction,"—" if part.empty else f"{part['累计成交金额（亿元）'].sum():,.2f} 亿元",border=True)
            if part.empty:
                st.info("该侧未取得披露数据，金额未知。")
            else:
                sectors=part.groupby("行业",as_index=False)["累计成交金额（亿元）"].sum().sort_values("累计成交金额（亿元）",ascending=False).head(15)
                sectors["方向"]=direction
                bar(sectors,"累计成交金额（亿元）","已披露重大"+direction+" · 行业",color="方向",xmax=xmax)
    st.warning("两侧各自为部分披露名单，不能相减得到基金或行业净流入；未上榜或未取得的数据保持未知。")
    st.caption("行业为已缓存财报分类，不匹配的标的保留未分类；不直接汇总不同基金份额类别。公告日期未提供，所有记录仍待原报告复核。")
    st.dataframe(frame,hide_index=True,column_config={"核验入口":st.column_config.LinkColumn("核验入口"),"累计成交金额（元）":st.column_config.NumberColumn(format="%.2f"),"累计成交金额（亿元）":st.column_config.NumberColumn(format="%.4f")},alt="基金重大买卖披露明细，保留累计期间和真实方向")
    st.download_button("下载重大买卖 CSV",frame.to_csv(index=False).encode("utf-8-sig"),code+"-major-trades.csv","text/csv")


def refresh_message(kind,args):
    from core.fund_research import refresh,dataset_key
    with st.spinner("正在读取公开数据并校验；失败时保留上次成功快照…"):
        ok=refresh(kind,args)
    if ok:st.success("已更新公开数据")
    else:
        logs={r["数据集"]:r["状态"] for r in store.status()}
        st.warning("本次未更新："+logs.get(dataset_key(kind,args),"接口未完成"))
    return ok
