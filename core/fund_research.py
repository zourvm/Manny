"""Public research adapters; observable disclosures are never full investor flows."""
from __future__ import annotations

from datetime import date
import json
import re
import pandas as pd
import requests
from core import store
from core.state_holdings import EASTMONEY, FIELDS, KEY, clean_name, finite_or_none
from core.vendor.research_contracts import DataEnvelope, SourceRef, ProviderAttempt, QualityFlag, stable_hash
from core.vendor.fund_rotation import safe_call, normalize_margin_rows, eastmoney_etf_spot_candidates

LIMITS = {
    "fund-holdings": "基金持仓聚合表按报告期最多返回100条；季报通常为重仓样本，不视为完整组合。不同份额类别可能共用组合，禁止直接跨代码相加。公告日期未由接口提供，无法做严格历史时点回测。",
    "fund-buy": "半年度/年度报告的累计重大买入名单，可能按阈值或前20名披露；不是单季度交易明细，也不是全量买入。未上榜金额未知。",
    "fund-sell": "半年度/年度报告的累计重大卖出名单，可能按阈值或前20名披露；不是单季度交易明细，也不是全量卖出。未上榜金额未知。",
    "insurance": "上市公司十大流通股东中被源站标为保险的具名账户；包括保险公司及产品。含长期战略股权，不代表保险行业全部持仓或现金净流入。",
    "private": "上市公司十大流通股东中被源站标为私募基金的账户；混有证券与股权投资产品，分类及管理人关系待核验。未识别产品、未进入前十及间接持仓不覆盖，不能等同量化资金。",
    "margin": "交易所沪深融资融券汇总，包含相应规则下的合资格证券。只合并同日沪深数据；未含北交所，不等于散户或机构净流入。缺少同日市值与成交额分母，不生成杠杆热度分数。",
    "etf-quality": "延迟行情快照，只显示返回的实际更新时间。IOPV与价格同步性未证实，不生成可交易信号；成交额不是净申购，ETF数据不能识别资金主体。",
}


def as_records(frame):
    return json.loads(frame.to_json(orient="records", force_ascii=False))


def envelope(kind, args, rows, raw, urls, started, cutoff, unit, warnings=None):
    if not rows:
        raise ValueError("没有可用记录，保留上次成功数据")
    notes = [LIMITS[kind], *(warnings or [])]
    audit = DataEnvelope(request={"dataset":kind, **args, "currency":"CNY", "unit":unit,
            "adjustment":"原始披露数量/金额，未作送转拆分调整"}, rows=rows,
        provider_chain=[started.finish("success", len(rows))],
        sources=[SourceRef(provider="AKShare / 东方财富" if kind not in ["margin"] else "AKShare / 交易所", source_url=u,
            priority="primary" if kind=="margin" else "convenience") for u in urls],
        data_cutoff=cutoff, quality_flags=[QualityFlag.PARTIAL] if kind=="margin" else [QualityFlag.PARTIAL,QualityFlag.UNVERIFIABLE], warnings=notes).to_dict()
    # Preserve a fingerprint of the returned source records as well as normalized rows.
    audit["raw_response_hash"] = stable_hash(raw)
    return {"records":rows, "source":urls[0], "audit":audit}


def parse_quarter(text):
    match = re.search(r"(20\d{2})年\s*([1-4])季度", str(text))
    if not match:
        raise ValueError("无法识别来源报告期："+str(text))
    year, quarter = map(int, match.groups())
    return f"{year}-{['03-31','06-30','09-30','12-31'][quarter-1]}"


def validate_fund(code, year):
    if not re.fullmatch(r"\d{6}", str(code)):
        raise ValueError("基金代码须为六位数字")
    if not 2000 <= int(year) <= date.today().year:
        raise ValueError("查询年份超出范围")


def stock_code(value):
    text=str(value).strip()
    # Mainland six-digit and Hong Kong five-digit identifiers must never collide.
    return "HK:"+text if re.fullmatch(r"\d{5}",text) else text


def normalize_public_holdings(raw, code, year):
    rows=[]
    for row in raw:
        period=parse_quarter(row["季度"])
        if period[:4] != str(year) or period > date.today().isoformat():
            raise ValueError("基金源返回错误或未来报告期")
        qty=finite_or_none(row.get("持股数")); mv=finite_or_none(row.get("持仓市值"))
        if qty is None or qty < 0 or mv is None or mv < 0:
            raise ValueError("基金持仓数量或市值缺失/非法")
        stock=stock_code(row["股票代码"])
        # Do not fabricate mainland industry tags for HK/overseas positions.
        rows.append({"主体":"公募样本", "账户名称":f"公募基金 {code}","基金代码":code,"资产类型":"股票",
            "标的代码":stock,"标的名称":row["股票名称"],"报告期":period,"公告日期":None,
            "持有数量":round(qty*1e4,2),"持仓市值（元）":round(mv*1e4,2),"持有比例（%）":finite_or_none(row.get("占净值比例")),
            "披露口径":"基金股票持仓聚合样本","核验状态":"聚合数据·待原文复核","原文链接":"",
            "核验入口":f"https://fundf10.eastmoney.com/jjgg_{code}.html","比例分母":"基金净资产"})
    result=pd.DataFrame(rows)
    if not result.empty and result.duplicated(KEY).any():
        raise ValueError("基金持仓重复或同一股票代码发生口径冲突")
    return as_records(result)


def normalize_major_trades(raw, code, year, direction):
    if direction not in ["买入","卖出"]:
        raise ValueError("未知交易方向")
    rows=[]
    for row in raw:
        if direction not in str(row.get("季度","")):
            raise ValueError("来源标题与请求的买卖方向不一致")
        end=parse_quarter(row["季度"])
        if end[:4]!=str(year) or end>date.today().isoformat() or end[5:] not in ["06-30","12-31"]:
            raise ValueError("重大买卖来源不是可识别的半年/全年累计报告")
        # AKShare hardcodes this same field name for both buy and SELL responses.
        amount=finite_or_none(row.get("本期累计买入金额"))
        if amount is None or amount < 0:
            raise ValueError("重大买卖金额缺失/非法")
        rows.append({"基金代码":code,"标的代码":stock_code(row["股票代码"]),"标的名称":row["股票名称"],
            "方向":direction,"期间起点":f"{year}-01-01","报告期":end,"公告日期":None,
            "累计成交金额（元）":round(amount*1e4,2),"占期初净资产比例（%）":finite_or_none(row.get("占期初基金资产净值比例")),
            "来源标题":row["季度"],"核验状态":"聚合数据·待原文复核",
            "核验入口":f"https://fundf10.eastmoney.com/jjgg_{code}.html"})
    result=pd.DataFrame(rows)
    if not result.empty and result.duplicated(["基金代码","标的代码","方向","报告期"]).any():
        raise ValueError("重大买卖出现重复记录")
    return as_records(result)


def fetch_public(kind, args, ak):
    code=str(args["code"]); year=int(args["year"]); validate_fund(code,year)
    attempt=ProviderAttempt.start("AKShare / 天天基金")
    warnings=[]
    if kind=="fund-holdings":
        raw=safe_call(warnings,"基金持仓",ak.fund_portfolio_hold_em,[{"symbol":code,"date":str(year)}])
        rows=normalize_public_holdings(raw,code,year); page="ccmx"
    else:
        direction="买入" if kind=="fund-buy" else "卖出"
        raw=safe_call(warnings,"重大"+direction,ak.fund_portfolio_change_em,[{"symbol":code,"indicator":"累计"+direction,"date":str(year)}])
        rows=normalize_major_trades(raw,code,year,direction); page="ccbd"
    if not rows:
        raise ValueError("；".join(warnings) or "暂无披露数据")
    return envelope(kind,args,rows,raw,[f"https://fundf10.eastmoney.com/{page}_{code}.html",
        f"https://fundf10.eastmoney.com/jjgg_{code}.html"],attempt,max(r["报告期"] for r in rows),"股、人民币元、%",warnings)


def normalize_institutions(raw, period, kind):
    rows=[]
    for row in raw:
        if kind=="insurance" and row.get("HOLDER_NEWTYPE")!="保险":
            raise ValueError("保险源分类与查询不一致")
        if kind=="private" and row.get("HOLDER_TYPE")!="私募基金":
            raise ValueError("私募源分类与查询不一致")
        code=str(row["SECURITY_CODE"]).zfill(6)
        if code.startswith(("200","900")):
            continue
        actual=str(row["END_DATE"])[:10]; notice=str(row.get("NOTICE_DATE") or "")[:10]
        if actual!=period or not notice or date.fromisoformat(notice)>date.today() or notice<period:
            raise ValueError("具名账户报告期或公告日期不合法")
        qty=finite_or_none(row.get("HOLD_NUM"))
        if qty is None or qty<0:
            raise ValueError("具名账户数量非法")
        name=clean_name(row["HOLDER_NAME"])
        rows.append({"主体":"保险" if kind=="insurance" else "私募（源分类）", "账户名称":name,"资产类型":"股票",
            "标的代码":code,"标的名称":row["SECURITY_NAME_ABBR"],"报告期":actual,"公告日期":notice,
            "持有数量":qty,"持仓市值（元）":finite_or_none(row.get("HOLDER_MARKET_CAP")),
            "持有比例（%）":finite_or_none(row.get("FREE_HOLDNUM_RATIO")),"披露口径":"十大流通股东",
            "核验状态":"源分类·待原文及账户关系复核","原文链接":"","核验入口":"https://www.cninfo.com.cn/new/index",
            "源账户类型":row.get("HOLDER_TYPE"),"源资金分类":row.get("HOLDER_NEWTYPE"),
            "披露滞后天数":(date.fromisoformat(notice)-date.fromisoformat(period)).days,
            "比例分母":"上市公司流通股本"})
    frame=pd.DataFrame(rows)
    return as_records(frame.sort_values("公告日期").drop_duplicates(KEY,keep="last")) if rows else []


def fetch_institutions(kind,args):
    period=str(args["period"]); day=date.fromisoformat(period)
    if day>date.today() or period[5:] not in ["03-31","06-30","09-30","12-31"]:
        raise ValueError("请选择已经结束的季度末")
    condition='(HOLDER_NEWTYPE="保险")' if kind=="insurance" else '(HOLDER_TYPE="私募基金")'
    attempt=ProviderAttempt.start("东方财富十大流通股东")
    params={"reportName":"RPT_F10_EH_FREEHOLDERS","columns":"ALL","pageSize":2000,"pageNumber":1,
        "sortColumns":"SECURITY_CODE,HOLDER_RANK","sortTypes":"1,1",
        "filter":f"(END_DATE='{period}')(NOTICE_DATE<='{date.today().isoformat()}')"+condition}
    raw=[]
    for page in range(1,31):
        params["pageNumber"]=page
        response=requests.get(EASTMONEY,params=params,timeout=(6,18));response.raise_for_status()
        result=response.json().get("result")
        if not result or int(result["pages"])>30:
            raise ValueError("具名账户源未返回完整分页，保留旧数据")
        raw.extend(result.get("data") or [])
        if page>=int(result["pages"]):
            if len(raw)!=int(result["count"]):
                raise ValueError("具名账户分页数量校验失败")
            break
    rows=normalize_institutions(raw,period,kind)
    payload=envelope(kind,args,rows,raw,["https://data.eastmoney.com/gdfx/","https://www.cninfo.com.cn/new/index"],attempt,period,"股、人民币元、%")
    payload["audit"]["query"]=params
    return payload


def checked_margin(raw,exchange,cutoff,*,unit="元"):
    if unit not in ["元","亿元"]:raise ValueError("未知两融原始金额单位")
    rows=normalize_margin_rows(raw,exchange,"AKShare / 交易所",unit=unit,cutoff=cutoff)
    for row in rows:
        row["source_unit"]=unit
        f,l,t=[row[k] for k in ["financing_balance","lending_balance","margin_balance"]]
        if any(v is None or not pd.notna(v) or v<0 for v in [f,l,t]) or f<=0:
            raise ValueError("两融余额存在缺失、零占位或非法值")
        if abs(f+l-t)>max(t,1)*.001:
            raise ValueError("两融余额恒等式校验失败")
    return rows


def fetch_margin(args,ak):
    start=date.fromisoformat(args["start"]);end=date.fromisoformat(args["end"])
    if start>end or end>=date.today() or (end-start).days>100:
        raise ValueError("两融单次最多100个自然日，终点须为已结束日期")
    attempt=ProviderAttempt.start("AKShare / 沪深交易所")
    raw=[];rows=[];warnings=[]
    # The SSE response contains its actual trade date. Query SZSE for those
    # sessions only; weekends and exchange holidays are never relabelled.
    sh=ak.stock_margin_sse(start_date=start.strftime("%Y%m%d"),end_date=end.strftime("%Y%m%d"))
    sh=sh.rename(columns={"信用交易日期":"日期","融券余量金额":"融券余额"})
    shraw=as_records(sh);raw.extend(shraw)
    shrows=checked_margin(shraw,"SSE",end.isoformat())
    if not shrows or any(r["trade_date"]<start.isoformat() for r in shrows):
        raise ValueError("沪市未返回所选区间的有效交易日期")
    rows.extend(shrows)
    old=store.load("research:margin")
    cached_sz={r["trade_date"]:r for r in old["records"] if r["exchange"]=="SZSE" and r.get("source_unit")=="亿元"} if old else {}
    reused=0
    for r in shrows:
        day=r["trade_date"]
        if day in cached_sz:
            rows.append(cached_sz[day]);raw.append(cached_sz[day]);reused+=1
            continue
        try:
            sz=ak.stock_margin_szse(date=day.replace("-",""))
            if len(sz)!=1:
                raise ValueError("深交所未返回单日汇总")
            records=as_records(sz.assign(日期=day));raw.extend(records)
            rows.extend(checked_margin(records,"SZSE",end.isoformat(),unit="亿元"))
        except Exception as ex:
            warnings.append(day+" 深市缺失："+str(ex)[:100])
    if reused:warnings.append(f"复用 {reused} 个已校验的深市日期，未重复采集。")
    return envelope("margin",args,rows,raw,["https://www.sse.com.cn/market/othersdata/margin/sum/",
        "https://www.szse.cn/disclosure/margin/margin/index.html"],attempt,max(r["trade_date"] for r in rows),"人民币元",warnings)


def margin_panel(rows):
    # Old adapter snapshots lacked SZSE source units and treated billions as
    # yuan. Require reacquisition instead of perpetuating the wrong scale.
    rows=[r for r in rows if not (r.get("exchange")=="SZSE" and r.get("provider")=="AKShare / 交易所" and not r.get("source_unit"))]
    frame=pd.DataFrame(rows)
    if frame.empty:return pd.DataFrame()
    frame=frame.drop_duplicates(["trade_date","exchange"],keep="last").sort_values("trade_date")
    # Inner join, no fill, no summing Shanghai on t with Shenzhen on t-1.
    sh=frame[frame.exchange=="SSE"].set_index("trade_date")
    sz=frame[frame.exchange=="SZSE"].set_index("trade_date")
    common=sh.index.intersection(sz.index)
    result=pd.DataFrame({"日期":common})
    for field,label in [("financing_balance","融资余额（亿元）"),("lending_balance","融券余额（亿元）"),("financing_buy","融资买入额（亿元）"),("margin_balance","两融余额（亿元）")]:
        result[label]=(sh.loc[common,field].values+sz.loc[common,field].values)/1e8
    result["融资余额变化（亿元）"]=result["融资余额（亿元）"].diff()
    result["间隔自然日"]=pd.to_datetime(result["日期"]).diff().dt.days
    # Across long gaps the change is an observed-interval change, not daily flow.
    return result


def fetch_etf_quality(args):
    codes=[str(c).strip() for c in args["codes"]]
    if not codes or len(codes)>20 or any(not re.fullmatch(r"(?:5\d{5}|15\d{4})",c) for c in codes):
        raise ValueError("请选择1至20个沪深ETF代码（5或15开头的六位代码）")
    attempt=ProviderAttempt.start("fund-rotation / 东方财富延迟行情")
    warnings=[]
    try:
        raw=eastmoney_etf_spot_candidates(codes)
    except Exception as ex:
        raw=[];warnings.append("全市场行情采集器失败："+str(ex)[:100])
    if {r["代码"] for r in raw} != set(codes):
        # A vendor page-size cap may truncate the full-universe helper. Query
        # the explicitly requested securities instead of assuming completeness.
        response=requests.get("https://push2delay.eastmoney.com/api/qt/ulist.np/get",params={
            "secids":",".join(("1." if c.startswith("5") else "0.")+c for c in codes),
            "fields":"f2,f3,f6,f12,f14,f124,f297,f402,f441","fltt":2,"invt":2},timeout=(6,12))
        response.raise_for_status()
        data=(response.json().get("data") or {}).get("diff") or []
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from core.vendor.fund_rotation import parse_number
        targeted=[]
        for row in data:
            timestamp=parse_number(row.get("f124"))
            targeted.append({"代码":str(row["f12"]),"名称":row.get("f14"),"最新价":parse_number(row.get("f2")),
                "IOPV实时估值":parse_number(row.get("f441")),"基金折价率":parse_number(row.get("f402")),
                "涨跌幅":parse_number(row.get("f3")),"成交额":parse_number(row.get("f6")),"数据日期":row.get("f297"),
                "更新时间":datetime.fromtimestamp(timestamp,ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds") if timestamp else None})
        raw=as_records(pd.DataFrame([*raw,*targeted]).drop_duplicates("代码",keep="last")) if raw or targeted else []
        warnings.append("全市场采集不完整，已使用按代码定向公开行情补取；字段单位与更新时间仍逐条保留。")
    rows=[]
    for row in raw:
        updated=row.get("更新时间")
        if row.get("代码") not in codes or not updated or pd.Timestamp(updated).date()>date.today():
            warnings.append(str(row.get("代码"))+" 缺失有效更新时间");continue
        r=dict(row); r["成交额（亿元）"]=finite_or_none(row.get("成交额"))
        if r["成交额（亿元）"] is not None:r["成交额（亿元）"]/=1e8
        r["数据状态"]="延迟快照·价格与IOPV同步性未核验"
        timestamp=pd.Timestamp(updated)
        if timestamp.tzinfo is None:timestamp=timestamp.tz_localize("Asia/Shanghai")
        r["采集时快照年龄（分钟）"]=round((pd.Timestamp.now(tz="Asia/Shanghai")-timestamp).total_seconds()/60,1)
        if r["采集时快照年龄（分钟）"]>5:r["数据状态"]="超过5分钟·仅作历史快照"
        rows.append(r)
    missing=set(codes)-{r["代码"] for r in rows}
    if missing:warnings.append("未返回代码："+"、".join(sorted(missing)))
    return envelope("etf-quality",args,rows,raw,["https://quote.eastmoney.com/center/gridlist.html#fund_etf"],attempt,
        max((r["更新时间"][:10] for r in rows),default=None),"人民币元、亿元、%",warnings)


def fetch(kind,args,ak=None):
    if kind in ["insurance","private"]:return fetch_institutions(kind,args)
    if kind=="etf-quality":return fetch_etf_quality(args)
    if kind=="margin":return fetch_margin(args,ak)
    if kind in ["fund-holdings","fund-buy","fund-sell"]:return fetch_public(kind,args,ak)
    raise ValueError("未知研究数据集")


def dataset_key(kind,args):
    if kind.startswith("fund-"):return f"research:{kind}:{args['code']}:{args['year']}"
    if kind in ["insurance","private"]:return f"research:{kind}:{args['period']}"
    return "research:"+kind


def refresh(kind,args):
    from core.data import run_worker
    key=dataset_key(kind,args)
    try:
        payload=run_worker("research",{"kind":kind,"params":args},timeout=240 if kind=="margin" else 100)
        if kind=="margin":
            old=store.load(key)
            if old:
                # A degraded refresh must not erase validated dates/exchanges.
                previous=[r for r in old["records"] if not (r.get("exchange")=="SZSE" and r.get("provider")=="AKShare / 交易所" and not r.get("source_unit"))]
                combined=pd.concat([pd.DataFrame(previous),pd.DataFrame(payload["records"])])
                payload["records"]=as_records(combined.drop_duplicates(["trade_date","exchange"],keep="last").sort_values(["trade_date","exchange"]))
                payload["audit"]["warnings"].append("合并既有成功历史；本次查询范围与原始指纹见请求记录。")
                payload["audit"]["warnings"].append("沪市原始金额为元，深市原始金额为亿元；旧版未记录深市单位的快照不再参与合计，须重新采集。")
                payload["audit"]["previous_snapshot"]={"fetched_at":old["fetched_at"],"normalized_hash":old.get("audit",{}).get("raw_hash")}
                payload["audit"]["rows"]=payload["records"]
                payload["audit"]["raw_hash"]=stable_hash(payload["records"])
                payload["audit"]["data_cutoff"]=max(r["trade_date"] for r in payload["records"])
        store.save(key,payload);return True
    except Exception as ex:
        store.failure(key,type(ex).__name__+": "+str(ex)[:220]);return False


def institution_history(kind):
    with store.connect() as con:
        names=[r[0] for r in con.execute("SELECT DISTINCT name FROM snapshots WHERE name LIKE ?",(f"research:{kind}:%",))]
    frames=[pd.DataFrame(store.load(n)["records"]) for n in names]
    if not frames:return pd.DataFrame(columns=FIELDS)
    return pd.concat(frames,ignore_index=True).sort_values("公告日期").drop_duplicates(KEY,keep="last")
