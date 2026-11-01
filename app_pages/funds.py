from datetime import date,timedelta
import re
import pandas as pd
import altair as alt
import streamlit as st
from core import store
from core.freshness import show as freshness
from core.fund_research import dataset_key,institution_history,margin_panel,LIMITS
from core.research_ui import audit_panel,holdings_view,major_trades_view,refresh_message

st.title("机构与杠杆观察")
st.caption("从公开披露观察公募、保险及私募具名账户，结合沪深两融和 ETF 行情交叉核验。")
st.page_link("app_pages/help.py", label="查看详细使用说明", icon=":material/help:")
st.page_link("app_pages/market.py",label="指数与两融对比 · 宽基、板块与行业历史走势",icon=":material/monitoring:")
view=st.segmented_control("研究模块",["公募基金","保险与私募","沪深两融","ETF 交易参考","研究口径"],default="公募基金",required=True,key="funds-view")
today=date.today()

if view=="公募基金":
    st.subheader("公募 · 重大买卖与持仓")
    st.caption("初始代码000001为功能观察样本，不代表你的持仓或基金推荐。每次单独查看一只基金，避免共用组合的不同份额重复汇总。")
    a,b=st.columns([2,1])
    code=a.text_input("公募基金六位代码",value="000001",max_chars=6,key="public-code").strip()
    year=b.selectbox("查询年份",list(range(today.year,2019,-1)),key="public-year")
    if not re.fullmatch(r"\d{6}",code):
        st.info("输入六位基金代码后查看。")
    else:
        mode=st.radio("公募观察视角",["重大买卖","持仓与行业"],horizontal=True,key="public-mode")
        args={"code":code,"year":year}
        if st.button("采集 / 更新本基金",type="primary",icon=":material/sync:"):
            for kind in (["fund-buy","fund-sell"] if mode=="重大买卖" else ["fund-holdings"]):refresh_message(kind,args)
        if mode=="重大买卖":
            buy=store.load(dataset_key("fund-buy",args));sell=store.load(dataset_key("fund-sell",args))
            major_trades_view(buy,sell,code)
            audit_panel(buy,"public-buy");audit_panel(sell,"public-sell")
        else:
            payload=store.load(dataset_key("fund-holdings",args))
            st.info(LIMITS["fund-holdings"])
            holdings_view(pd.DataFrame(payload["records"]) if payload else pd.DataFrame(),"public",payload=payload)
            audit_panel(payload,"public-holdings")

elif view=="保险与私募":
    kind=st.radio("具名账户类别",["保险","私募（源分类）"],horizontal=True,key="institution-kind")
    kind_key="insurance" if kind=="保险" else "private"
    st.info(LIMITS[kind_key])
    st.caption("仅按披露文件中的具名账户观察；公募/私募是产品形式，量化是策略，国家队是主体角色，这些维度可能交叉，不能直接相加。")
    periods=sorted([f"{year}-{suffix}" for year in range(today.year-3,today.year+1) for suffix in ["03-31","06-30","09-30","12-31"] if f"{year}-{suffix}"<today.isoformat()],reverse=True)
    with st.expander("采集其他报告期 / 更新账户数据"):
        with st.form("institution-collect"):
            period=st.selectbox("采集报告期",periods,index=1 if len(periods)>1 else 0)
            submit=st.form_submit_button("采集本报告期",type="primary")
            st.caption("可分别采集两个报告期后比较。最新季报在披露窗口内可能尚未齐全；聚合表按十大流通股东范围取数。")
    if submit:refresh_message(kind_key,{"period":period})
    history=institution_history(kind_key)
    if kind_key=="insurance" and st.toggle("仅看保险产品账户（排除保险公司本级）",key="insurance-products"):
        history=history[history["源账户类型"]=="保险产品"] if not history.empty else history
    holdings_view(history,kind_key)
    if not history.empty:
        latest=max(history["报告期"])
        payload=store.load(dataset_key(kind_key,{"period":latest}))
        st.caption("最近报告期具名记录的公告滞后："+f"{history.loc[history['报告期']==latest,'披露滞后天数'].min()}–{history.loc[history['报告期']==latest,'披露滞后天数'].max()} 天。")
        audit_panel(payload,kind_key)

elif view=="沪深两融":
    st.subheader("杠杆资金 · 同日沪深汇总")
    st.info(LIMITS["margin"])
    with st.form("margin-collect"):
        a,b=st.columns(2)
        start=a.date_input("两融采集起点",today-timedelta(days=21),max_value=today-timedelta(days=1))
        end=b.date_input("两融采集终点",today-timedelta(days=1),max_value=today-timedelta(days=1))
        submitted=st.form_submit_button("增量更新两融",type="primary")
        st.caption("单次最多100个自然日；沪市返回交易日期后逐日核对深市，缺少任一交易所的日期不纳入合计。")
    if submitted:refresh_message("margin",{"start":start.isoformat(),"end":end.isoformat()})
    payload=store.load("research:margin")
    freshness("research:margin",payload)
    if payload:
        panel=margin_panel(payload["records"])
        if panel.empty:st.warning("暂无沪深同日数据，不能计算合计。")
        else:
            last=panel.iloc[-1];a,b,c=st.columns(3)
            a.metric("沪深融资余额",f"{last['融资余额（亿元）']:,.2f} 亿元",border=True)
            b.metric("最近观测间隔余额变化","—" if pd.isna(last['融资余额变化（亿元）']) else f"{last['融资余额变化（亿元）']:+,.2f} 亿元",border=True)
            c.metric("沪深同日观测",len(panel),border=True)
            st.caption(f"合计最新日期 {last['日期']}；余额变化不等于净买入，期间可能含偿还、调出及其他调整。缺口不会补零。")
            long=panel.melt("日期",value_vars=["融资余额（亿元）","两融余额（亿元）"],var_name="指标",value_name="金额（亿元）")
            st.altair_chart(alt.Chart(long).mark_line(point=True).encode(x="日期:T",y=alt.Y("金额（亿元）:Q",scale=alt.Scale(zero=False)),color="指标:N",tooltip=["日期:N","指标:N",alt.Tooltip("金额（亿元）:Q",format=",.2f")]).properties(height=300),alt="沪深同日融资与两融余额走势")
            st.altair_chart(alt.Chart(panel).mark_bar().encode(x="日期:T",y="融资余额变化（亿元）:Q",color=alt.condition(alt.datum['融资余额变化（亿元）']>=0,alt.value('#39B8A5'),alt.value('#E58483')),tooltip=["日期:N","间隔自然日:Q","融资余额变化（亿元）:Q"]).properties(height=210),alt="相邻可见观测之间的融资余额变化，不等于现金净流入")
            st.dataframe(panel,hide_index=True)
            st.download_button("下载沪深同日两融 CSV",panel.to_csv(index=False).encode("utf-8-sig"),"margin.csv","text/csv")
        audit_panel(payload,"margin")
    else:st.info("尚无两融快照，点击更新开始采集。")

elif view=="ETF 交易参考":
    st.subheader("ETF · 成交与延迟行情")
    st.page_link("app_pages/etf.py",label="查看多宽基份额与申赎参考",icon=":material/show_chart:")
    st.caption("复用 fund-rotation-analyst 的 ETF 行情采集器，展示更新时间、成交额和IOPV字段。")
    codes=st.text_input("ETF代码（逗号分隔，最多20只）",value="510300,510500,510050,588000,159915")
    if st.button("更新 ETF 交易快照",type="primary"):
        refresh_message("etf-quality",{"codes":[c.strip() for c in re.split(r"[,，\s]+",codes) if c.strip()]})
    payload=store.load("research:etf-quality")
    freshness("research:etf-quality",payload)
    st.info(LIMITS["etf-quality"])
    if payload:
        frame=pd.DataFrame(payload["records"])
        ages=(pd.Timestamp.now(tz="Asia/Shanghai")-pd.to_datetime(frame["更新时间"],utc=True)).dt.total_seconds()/60
        if (ages>5).any():st.warning("部分本地行情快照已超过5分钟，请按表内实际更新时间判断；需要时点击更新。")
        st.altair_chart(alt.Chart(frame).mark_bar().encode(y=alt.Y("名称:N",sort="-x"),x="成交额（亿元）:Q",tooltip=["代码:N","名称:N","更新时间:N","成交额（亿元）:Q"]).properties(height=270),alt="返回ETF的成交额与实际更新时间，非资金净流入")
        st.dataframe(frame,hide_index=True)
        audit_panel(payload,"etf-quality")
    else:st.info("暂无快照；可先查看已接入的 ETF 份额历史。")

else:
    st.subheader("来源与结论边界")
    st.markdown("""
    **来源优先级**：基金公司、上市公司及交易所原始披露 → 公开聚合页面 → AKShare 采集包装。

    **时间口径**：报告期是持仓快照或累计交易的截止日期；公告日期决定何时可以知道。采集日期只说明本地什么时候读到数据。未提供公告日期的公募聚合数据不能用于严格历史回测。

    **事实与计算**：已披露账户、股数和金额属于可核对记录；行业加总、同日沪深合计、数量变动参考额属于计算。它们都不能直接证明未来收益或未披露账户的操作。

    **可比性**：同一账户、同一股票、同一披露口径且两期均出现，才计算数量变化；新披露与消失保持未知。送转、拆分、账户划转也可能改变数量，需再核验。

    **覆盖范围**：公募重大买卖是达到披露阈值的名单；保险和私募为十大流通股东可见账户；ETF份额包含所有投资者；两融包含个人及机构融资交易。量化资金没有完整公开身份标签。

    **失败处理**：按数据集保存成功快照、参数、时间、来源、指纹及尝试日志。失败保留旧数据，空表不补零，不将部分样本描述为全市场。
    """)
    st.markdown("研究规范：[china-stock-fund-analysis](https://github.com/kidulthhh-dotcom/china-stock-fund-analysis-skill/tree/main/china-stock-fund-analysis)；采集与可视化参考：[fund-rotation-analyst](https://github.com/thincat75/fund-rotation-analyst)。")
    st.page_link("app_pages/settings.py",label="查看各数据集采集日志",icon=":material/database:")
