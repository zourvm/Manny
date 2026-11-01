"""Daily price indices versus market-wide margin balances, with explicit dates."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import json
import math
import pandas as pd
import requests
from core import store
from core.vendor.research_contracts import DataEnvelope, SourceRef, ProviderAttempt, QualityFlag, stable_hash

CATALOG=json.loads((store.ROOT/"resources/market_indices.json").read_text(encoding="utf-8"))
INDICES={r["symbol"]:r for r in CATALOG["indices"]}
DEFAULT=["sh000001","sz399001","sz399006","sh000688","sh000905","sh000852"]
MARGIN_KEY="market:margin-history"
MARGIN_SOURCE="https://data.eastmoney.com/rzrq/total.html"
MARGIN_FIELDS={"RZYE":"融资余额（亿元）","RQYE":"融券余额（亿元）","RZRQYE":"两融余额（亿元）"}


def records(frame):return json.loads(frame.to_json(orient="records",force_ascii=False))


def validate_range(start,end):
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if a>b or b>=date.today() or a<date(2010,1,1):
        raise ValueError("请选择2010年起的已结束日期，起点不能晚于终点")
    return a,b


def normalize_index(raw,symbol,start,end):
    validate_range(start,end)
    if symbol not in INDICES:raise ValueError("不在已核验的指数目录中")
    frame=pd.DataFrame(raw).rename(columns={"date":"日期","close":"收盘点位","收盘":"收盘点位"})
    if frame.empty or not {"日期","收盘点位"}.issubset(frame):raise ValueError("指数源未返回日期和收盘点位")
    parsed=pd.to_datetime(frame["日期"],errors="coerce")
    if parsed.isna().any():raise ValueError("指数含无法识别的日期")
    frame["日期"]=parsed.dt.strftime("%Y-%m-%d")
    frame=frame.loc[frame["日期"].between(start,end),["日期","收盘点位"]].copy()
    frame["收盘点位"]=pd.to_numeric(frame["收盘点位"],errors="coerce")
    if len(frame)<2 or not frame["收盘点位"].map(lambda v:pd.notna(v) and math.isfinite(v) and v>0).all():
        raise ValueError("指数有效观测不足或收盘点位非法；未用零补缺")
    if frame["日期"].duplicated().any():raise ValueError("指数返回重复日期")
    frame["指数代码"]=symbol;frame["指数名称"]=INDICES[symbol]["name"]
    return records(frame.sort_values("日期"))


def make_payload(rows,raw,query,attempts,sources,warnings,flags=None):
    audit=DataEnvelope(request={**query,"currency":"CNY","frequency":"daily"},rows=rows,
        provider_chain=attempts,sources=sources,data_cutoff=max(r["日期"] for r in rows),
        quality_flags=flags or [QualityFlag.UNVERIFIABLE],warnings=warnings).to_dict()
    audit["raw_response_hash"]=stable_hash(raw)
    return {"records":rows,"source":sources[0].source_url,"audit":audit}


def fetch_index(args,ak):
    symbol=args["symbol"];start=args["start"];end=args["end"]
    if symbol not in INDICES:raise ValueError("未知指数代码")
    validate_range(start,end)
    chain=[];warnings=[];rows=None;source=None
    # The explicit market prefix avoids guessing a stock when index codes collide.
    providers=[("AKShare / 东方财富",lambda:ak.stock_zh_index_daily_em(symbol=symbol,start_date=start.replace("-",""),end_date=end.replace("-","")),
                "https://quote.eastmoney.com/"+symbol+".html")]
    if symbol.startswith("csi"):
        def official_history():
            frame=ak.stock_zh_index_hist_csindex(symbol=symbol[3:],start_date=start.replace("-",""),end_date=end.replace("-",""))
            # Some official response ranges include holiday carry-forward rows.
            # Keep only independently supplied trading dates, never create prices.
            calendar=ak.tool_trade_date_hist_sina()
            sessions={pd.Timestamp(d).date().isoformat() for d in calendar["trade_date"]}
            frame=frame.loc[frame["日期"].map(lambda d:pd.Timestamp(d).date().isoformat() in sessions)].copy()
            warnings.append("中证官方历史已与新浪交易日历取交集，剔除非交易日记录；指数发布前数据可能为回溯计算。")
            return frame
        providers.append(("AKShare / 中证指数官方",official_history,"https://www.csindex.com.cn/zh-CN/indices/index-detail/"+symbol[3:]))
    if symbol.startswith(("sh","sz")):
        providers.append(("AKShare / 新浪",lambda:ak.stock_zh_index_daily(symbol=symbol),
                          f"https://finance.sina.com.cn/realstock/company/{symbol}/nc.shtml"))
        providers.append(("AKShare / 腾讯",lambda:ak.stock_zh_index_daily_tx(symbol=symbol,start_date=start.replace("-",""),end_date=end.replace("-","")),
                          "https://gu.qq.com/"+symbol+"/zs"))
    partial=None
    for provider,call,url in providers:
        attempt=ProviderAttempt.start(provider)
        try:
            raw=call();rows=normalize_index(raw,symbol,start,end)
            chain.append(attempt.finish("success",len(rows)));source=SourceRef(provider=provider,source_url=url,priority="primary" if "官方" in provider else "convenience")
            if provider=="AKShare / 新浪" and len(rows)<60 and (date.fromisoformat(end)-date.fromisoformat(start)).days>365:
                warnings.append("新浪仅返回短期样本，继续尝试其他公开源；不把短期样本视作完整长期历史。")
                partial=(rows,raw,source);rows=None
                continue
            break
        except Exception as ex:
            message=str(ex)[:180];chain.append(attempt.finish("error",message=message));warnings.append(provider+"："+message)
    if rows is None and partial:rows,raw,source=partial
    if rows is None:raise ValueError("指数来源均失败："+"；".join(warnings))
    warnings.append("价格指数收盘点位，不含分红再投资；可能含发布前回溯历史，不等同可投资收益。")
    flags=[QualityFlag.UNVERIFIABLE]+([QualityFlag.FALLBACK] if len(chain)>1 else [])
    sources=[source]
    if "官方" in source.provider:sources.append(SourceRef(provider="新浪交易日历",source_url="https://finance.sina.com.cn/realstock/company/klc_td_sh.txt"))
    return make_payload(rows,records(raw),{"dataset":"index","symbol":symbol,"start":start,"end":end,"unit":"指数点","adjustment":"价格指数，无证券复权、无分红再投资"},chain,sources,warnings,flags)


def normalize_margin(raw,start,end):
    validate_range(start,end)
    rows=[]
    for r in raw:
        day=pd.Timestamp(r["DIM_DATE"]).date().isoformat()
        if not start<=day<=end:continue
        vals={field:float(r[field]) for field in MARGIN_FIELDS}
        if any(not math.isfinite(v) or v<0 for v in vals.values()) or vals["RZYE"]<=0:
            raise ValueError("两融历史余额缺失、零占位或非法")
        if abs(vals["RZYE"]+vals["RQYE"]-vals["RZRQYE"])>max(vals["RZRQYE"],1)*.001:
            raise ValueError("历史两融余额恒等式校验失败")
        rows.append({"日期":day,**{label:vals[field]/1e8 for field,label in MARGIN_FIELDS.items()}})
    frame=pd.DataFrame(rows)
    if frame.empty or frame["日期"].duplicated().any():raise ValueError("两融历史为空或返回重复日期")
    return records(frame.sort_values("日期"))


def normalize_exchange_history(raw,start,end):
    """The site's default total includes Beijing; explicitly join SH and SZ."""
    if any(str(r.get("SCDM")) not in ["007","001"] for r in raw):
        raise ValueError("返回含沪深之外的市场，不能作为沪深两融合计")
    markets={code:pd.DataFrame(normalize_margin([r for r in raw if str(r["SCDM"])==code],start,end)).set_index("日期") for code in ["007","001"]}
    common=markets["007"].index.intersection(markets["001"].index)
    if not len(common):raise ValueError("两融历史没有沪深同日交集")
    combined=markets["007"].loc[common]+markets["001"].loc[common]
    return records(combined.sort_index().reset_index()),len(set(markets['007'].index)|set(markets['001'].index))-len(common)


def fetch_margin_history(args,ak):
    start,end=args["start"],args["end"];validate_range(start,end)
    attempt=ProviderAttempt.start("东方财富 / 沪深两融交易总量")
    params={"reportName":"RPTA_WEB_RZRQ_LSSH","columns":"ALL","source":"WEB","sortColumns":"DIM_DATE,SCDM","sortTypes":"-1,1","pageSize":500,"pageNumber":1,
            "filter":f'(DIM_DATE>=\'{start}\')(DIM_DATE<=\'{end}\')(SCDM in ("007","001"))'}
    raw=[];pages=1
    for page in range(1,30):
        params["pageNumber"]=page
        response=requests.get("https://datacenter-web.eastmoney.com/api/data/v1/get",params=params,timeout=(6,15))
        response.raise_for_status();body=response.json()
        if not body.get("success") or not body.get("result"):raise ValueError("两融历史端点未返回成功结果")
        result=body["result"];pages=int(result["pages"])
        if pages>29:raise ValueError("两融历史页数异常")
        raw.extend(result["data"])
        if page>=pages:break
    rows,unmatched=normalize_exchange_history(raw,start,end)
    warnings=["沪深市场汇总，不是所选指数成分股或行业自身的两融余额；与既有交易所直连快照分开保存。",
              "余额变化不等于现金净流入；价格走势与杠杆同时变化不能证明因果或未来收益。"]
    warnings.append(f"源站默认全部市场合计含北交所，本适配器仅请求沪市007及深市001，逐日内连接；排除缺少任一市场的{unmatched}个日期。")
    sources=[SourceRef(provider="东方财富 / 沪深汇总",source_url=MARGIN_SOURCE)]
    audit_checks=[]
    # Check the latest available day against both official exchanges. Failure is
    # visible and never silently grants the aggregation 'verified' status.
    latest=rows[-1]["日期"]
    try:
        from core.fund_research import checked_margin,margin_panel
        sh=ak.stock_margin_sse(start_date=latest.replace("-",""),end_date=latest.replace("-",""))
        sh=sh.rename(columns={"信用交易日期":"日期","融券余量金额":"融券余额"})
        sz=ak.stock_margin_szse(date=latest.replace("-",""))
        if len(sz)!=1:raise ValueError("深市未返回单日汇总")
        official=checked_margin(records(sh),"SSE",latest)+checked_margin(records(sz.assign(日期=latest)),"SZSE",latest,unit="亿元")
        panel=margin_panel(official)
        selected=panel.loc[panel["日期"]==latest]
        if selected.empty:raise ValueError("沪深实际日期未对齐")
        for label in MARGIN_FIELDS.values():
            a=float(selected.iloc[0][label]);b=rows[-1][label]
            if abs(a-b)>.021:raise ArithmeticError("聚合值与交易所同日合计不一致："+label)
        audit_checks.append({"日期":latest,"核验":"融资、融券及两融余额均与沪深交易所同日汇总在0.021亿元舍入容差内一致","单位":"上交所元，深交所亿元；分别转换后相加"})
        sources.extend([SourceRef(provider="上交所",source_url="https://www.sse.com.cn/market/othersdata/margin/sum/",priority="primary"),SourceRef(provider="深交所",source_url="https://www.szse.cn/disclosure/margin/margin/index.html",priority="primary")])
    except ArithmeticError:raise
    except Exception as ex:warnings.append("本次最新日交易所交叉核验未完成："+str(ex)[:150])
    warnings.append("单日抽查不代表全部历史已与交易所逐日核验。")
    payload=make_payload(rows,raw,{"dataset":"margin-history","start":start,"end":end,"unit":"亿元","adjustment":"余额原值除以1亿，未平滑或填补"},[attempt.finish("success",len(rows))],sources,warnings,[QualityFlag.PARTIAL,QualityFlag.UNVERIFIABLE])
    payload["audit"]["official_checks"]=audit_checks
    return payload


def refresh_one(name,args):
    from core.data import run_worker
    try:
        key="market-margin" if name==MARGIN_KEY else "market-index"
        payload=run_worker(key,args,timeout=110)
        store.save(name,payload)
        return {"数据集":name,"状态":"成功","记录数":len(payload["records"])}
    except Exception as ex:
        message=str(ex)[:220];store.failure(name,message)
        return {"数据集":name,"状态":"失败，保留成功快照","原因":message}


def refresh_many(symbols,start,end,include_margin=True):
    validate_range(start,end)
    if not symbols or any(s not in INDICES for s in symbols):raise ValueError("请选择目录中的指数")
    jobs=[("index:"+s,{"symbol":s,"start":start,"end":end}) for s in dict.fromkeys(symbols)]
    if include_margin:jobs.append((MARGIN_KEY,{"start":start,"end":end}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(refresh_one,name,args) for name,args in jobs]
        return [f.result() for f in as_completed(futures)]


def align(payloads,start,end,margin=None,margin_field="两融余额（亿元）"):
    """Inner join the requested interval. No zero/forward fill or shifted dates."""
    series=[];coverage=[]
    for symbol,p in payloads.items():
        frame=pd.DataFrame(p["records"])
        frame=frame.loc[frame["日期"].between(start,end)].sort_values("日期")
        if frame.empty:
            coverage.append({"指数":INDICES[symbol]["name"],"可用起点":None,"可用终点":None,"观测数":0});continue
        if frame["日期"].duplicated().any():raise ValueError("本地指数含重复日期")
        series.append(frame.set_index("日期")["收盘点位"].rename(symbol))
        coverage.append({"指数":INDICES[symbol]["name"],"可用起点":frame["日期"].min(),"可用终点":frame["日期"].max(),"观测数":len(frame)})
    if not series or len(series)!=len(payloads):return pd.DataFrame(),pd.DataFrame(coverage)
    common=pd.concat(series,axis=1,join="inner").dropna().sort_index()
    if margin:
        m=pd.DataFrame(margin["records"])
        m=m.loc[m["日期"].between(start,end)].set_index("日期")
        if m.index.duplicated().any():raise ValueError("本地两融含重复日期")
        common=common.join(m[margin_field].rename("市场两融对照"),how="inner").dropna()
    return common,pd.DataFrame(coverage)


def normalized(common):
    if common.empty or (common.iloc[0]<=0).any():return pd.DataFrame()
    return common.div(common.iloc[0]).mul(100)


def comparison_stats(common):
    if len(common)<2:return pd.DataFrame()
    changes=common.pct_change(fill_method=None).iloc[1:]
    rows=[]
    for symbol in common.columns:
        if symbol not in INDICES:continue
        values=common[symbol];row={"指数":INDICES[symbol]["name"],"代码":symbol,"共同起点":common.index[0],"共同终点":common.index[-1],
            "区间涨跌（%）":(values.iloc[-1]/values.iloc[0]-1)*100,"区间最大回撤（%）":(values/values.cummax()-1).min()*100,"共同观测数":len(common)}
        if "市场两融对照" in common:
            pair=changes[[symbol,"市场两融对照"]].dropna()
            enough=len(pair)>=20 and pair[symbol].std()>0 and pair["市场两融对照"].std()>0
            row["变动相关系数"]=pair[symbol].corr(pair["市场两融对照"]) if enough else None
            row["变动配对样本"]=len(pair)
        rows.append(row)
    return pd.DataFrame(rows).sort_values("区间涨跌（%）",ascending=False)
