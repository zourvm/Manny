"""Data age, successful acquisition and explicit update expectations.

Expected dates describe a publisher schedule/window or an estimate, never a
promise that a manual collector or a third-party wrapper will update itself.
"""
from __future__ import annotations
from calendar import monthrange
from datetime import date,datetime,timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from core import store

CN=ZoneInfo("Asia/Shanghai")
CAL=json.loads((Path(__file__).resolve().parents[1]/"resources/update_calendar.json").read_text(encoding="utf-8"))


def now_cn():return datetime.now(CN)


def as_day(value):
    try:
        if value is None or pd.isna(value):return None
        text=str(value)
        if len(text)==8 and text.isdigit():text=f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return date.fromisoformat(text[:10])
    except (ValueError,TypeError):return None


def as_time(value):
    try:
        parsed=pd.Timestamp(value)
        if pd.isna(parsed):return None
        return parsed.to_pydatetime().replace(tzinfo=CN) if parsed.tzinfo is None else parsed.to_pydatetime().astimezone(CN)
    except (ValueError,TypeError):return None


def month_add(day,count=1):
    index=day.year*12+day.month-1+count
    return date(index//12,index%12+1,1)


def month_end(day):return date(day.year,day.month,monthrange(day.year,day.month)[1])


def session_day(day):
    if day.weekday()>4:return False
    return not any(date.fromisoformat(a)<=day<=date.fromisoformat(b) for a,b in CAL["cn_closed_2026"])


def next_session(day,count=1):
    result=day
    for _ in range(count):
        result+=timedelta(days=1)
        while not session_day(result):result+=timedelta(days=1)
    return result


def kind_for(name):
    if name.startswith("index:"):return "index"
    if name=="market:margin-history":return "margin-history"
    if name.startswith("house:"):return "house"
    if name.startswith("state:"):return "named"
    if name=="state-industries":return "mapping"
    if name.startswith("research:"):
        part=name.split(":")[1]
        return {"insurance":"named","private":"named","fund-holdings":"fund-holdings","fund-buy":"major","fund-sell":"major","margin":"margin","etf-quality":"quote"}.get(part,"manual")
    return {"state-import":"manual","state-etf-history":"history","state-events":"events","etf":"shares","cme":"cme","industry":"manual"}.get(name,name)


def label_for(name):
    if name.startswith("index:"):
        from core.market_compare import INDICES
        symbol=name.split(":",1)[1]
        return "指数 · "+INDICES.get(symbol,{}).get("name",symbol)
    if name=="market:margin-history":return "沪深两融历史汇总（东方财富）"
    labels={"trade":"进出口总量","pmi":"采购经理指数","estate":"国房景气指数","etf":"宽基ETF份额","industry":"行业/商品进出口导入","state-industries":"股票行业映射","state-import":"国家队补充导入","state-etf-history":"国家队ETF历史样本","state-events":"国家队公告选录","cme":"美联储预期"}
    if name in labels:return labels[name]
    if name.startswith("house:"):return "住宅价格 · "+" / ".join(name.split(":")[1:])
    if name.startswith("state:"):return "国家队个股 · "+name.split(":",1)[1]
    if name.startswith("research:"):
        parts=name.split(":");label={"insurance":"保险具名账户","private":"私募具名账户","fund-holdings":"公募持仓","fund-buy":"公募重大买入","fund-sell":"公募重大卖出","margin":"沪深两融","etf-quality":"ETF交易行情"}.get(parts[1],parts[1])
        return label+(" · "+" / ".join(parts[2:]) if len(parts)>2 else "")
    return name


def observed_day(name,payload):
    if not payload:return None
    kind=kind_for(name)
    if kind=="mapping":return as_day(payload.get("source_period"))
    if kind=="cme" and payload.get("public_audit"):
        return as_day(payload["public_audit"].get("data_date"))
    rows=payload.get("records",[])
    if kind=="margin":
        markets={exchange:{as_day(r.get("trade_date")) for r in rows if r.get("exchange")==exchange} for exchange in ["SSE","SZSE"]}
        common=(markets["SSE"]&markets["SZSE"])-{None}
        return max(common) if common else None
    fields=["报告期"] if kind in ["named","major","fund-holdings","history"] else ["报告日期"] if kind=="cme" else ["公告日期"] if kind=="events" else ["更新时间"] if kind=="quote" else ["日期","报告期"]
    values=[d for r in rows for field in fields if (d:=as_day(r.get(field))) is not None]
    return (min(values) if kind=="quote" else max(values)) if values else None


def quarter_after(day):
    end=month_end(month_add(date(day.year,((day.month-1)//3+1)*3,1),3))
    return end


def report_window(period,kind):
    if kind=="major":
        target=date(period.year,12,31) if period.month<=6 else date(period.year+1,6,30)
        deadline=date(target.year+1,3,31) if target.month==12 else date(target.year,8,31)
        return target+timedelta(days=1),deadline,"中报两个月内、年报三个月内（披露窗口）",CAL["fund_rule"]
    target=quarter_after(period)
    begin=target+timedelta(days=1)
    if kind=="fund-holdings":
        if begin.year==2026:
            deadline=next_session(target,15)
            note="季末后15个工作日，按已核验2026休市日估算；基金专门规定另核"
        else:
            deadline=month_end(begin)
            note="下一季报预计在季末后次月中下旬；新年度休市日未核验，非精确截止日"
        return begin,deadline,note,CAL["fund_rule"]
    deadline=date(target.year+1,4,30) if target.month==12 else date(target.year,8,31) if target.month==6 else month_end(begin)
    return begin,deadline,"上市公司季报1个月、中报2个月、年报4个月（披露窗口）；聚合接口可能另有延迟",CAL["company_rule"]


def monthly_window(kind,period):
    target=month_add(period)
    if kind=="estate" and target.month==1:target=month_add(target)  # No January activity release.
    if kind=="pmi":
        if target.year==2026:
            day=date.fromisoformat(CAL["pmi_observation_release_2026"][target.month-1])
            return day,day,"国家统计局2026发布日程（计划）",CAL["nbs_source"]
        return month_end(target),month_end(target),"按月末估计；该年度官方日程尚未核验",CAL["nbs_source"]
    release=month_add(target)
    if kind in ["house","estate"] and release.year==2026:
        days=CAL["house_release_days_2026" if kind=="house" else "estate_release_days_2026"]
        actual=days[release.month-1]
        if actual is not None:
            day=date(release.year,release.month,actual)
            return day,day,"国家统计局2026月度发布日程（计划），接口入库可滞后",CAL["nbs_source"]
    if kind=="trade":
        return date(release.year,release.month,7),date(release.year,release.month,15),"经验估计窗口；本机尚未核验海关该期精确日程，非官方承诺", "https://zj.singlewindow.cn/detail/article/2025_4/12_17/1765955546242_1.shtml"
    return date(release.year,release.month,10),date(release.year,release.month,20),"按次月中旬估计；未核验该年度精确日程",CAL["nbs_source"]


def describe(name,payload=None,*,observed=None,now=None,title=None):
    now=now or now_cn();now=now.replace(tzinfo=CN) if now.tzinfo is None else now.astimezone(CN)
    kind=kind_for(name);payload=payload or {};day=as_day(observed) if observed is not None else observed_day(name,payload)
    acquired=as_time(payload.get("fetched_at"))
    verified=payload.get("verified_on")
    fetched=acquired.strftime("%Y-%m-%d %H:%M:%S") if acquired else f"{verified}（原文核验日；获取时刻未记录）" if verified else "未记录 / 尚未成功获取"
    period=f"{day:%Y-%m}（月度）" if day and kind in ["trade","pmi","house","estate"] else day.isoformat() if day else "未知 / 未采集"
    row={"数据集":title or label_for(name),"数据截至 / 报告期":period,"最近成功获取（北京时间）":fetched,
        "预计下次更新":"无法准确预计","本地获取方式":"手动刷新；未设置自动定时获取","时效状态":"待核对","预计依据":"无固定更新日程","依据链接":""}
    if not payload.get("records"):
        row.update({"时效状态":"未采集","预计下次更新":"不定期；有新文件时导入" if kind=="manual" else "首次成功获取后按数据期推算"})
        return row
    if kind in ["history","events","manual"] or not day:
        row.update({"预计下次更新":"不定期；有新原文 / 文件时补充","预计依据":"历史选录或用户导入，无连续更新承诺","时效状态":"仅历史参考" if kind in ["history","events"] else "来源与时效待核对"})
    elif kind in ["quote","cme"]:
        rows=payload.get("records",[])
        if kind=="cme":rows=[r for r in rows if as_day(r.get("报告日期"))==day]
        fields=["更新时间"] if kind=="quote" else ["网页数据截至（北京时间）"]
        times=[t for r in rows for f in fields if (t:=as_time(r.get(f))) is not None]
        if times:
            source_time=min(times)
            row["数据截至 / 报告期"]=source_time.strftime("%Y-%m-%d %H:%M:%S")+("（最旧行情行）" if kind=="quote" else "（网页时间折合北京）")
            age=max(0,(now-source_time).total_seconds()/3600)
            row["时效状态"]=f"网页快照 · 源时间距今约{age:.1f}小时"
        if kind=="quote":
            due=(min(times)+timedelta(minutes=5)) if times else None
            row.update({"预计下次更新":due.strftime("%Y-%m-%d %H:%M")+"前后（5分钟检查建议）" if due else "交易时段动态更新，无固定发布时间",
                "预计依据":"延迟行情；5分钟为本地检查建议，不是源站保证","时效状态":"行情快照超过5分钟，仅历史参考" if due and now>due else "已获取行情快照"})
        else:
            with store.connect() as con:attempt=con.execute("SELECT attempted_at FROM attempts WHERE name='cme-web'").fetchone()
            last=as_time(attempt[0]) if attempt else acquired
            due=last+timedelta(minutes=15) if last else now
            row.update({"预计下次更新":due.strftime("%Y-%m-%d %H:%M")+"起可再次自动检查",
                "本地获取方式":"打开 / 操作美联储页时检查；15分钟限频，非后台定时任务",
                "预计依据":"本地检查时间；CME网页动态变化无固定发布日，不能保证出现新数据"})
            if times and (now.date()-min(times).date()).days>3:row["时效状态"]="网页源时间偏旧，需核验"
    else:
        if kind in ["trade","pmi","house","estate"]:
            begin,end,note,url=monthly_window(kind,day)
        elif kind in ["named","mapping","fund-holdings","major"]:
            begin,end,note,url=report_window(day,"named" if kind=="mapping" else kind)
        elif kind in ["shares","margin","index","margin-history"]:
            end=next_session(day,2 if kind=="margin" else 1);begin=end
            note="按下一交易日估计；沪深两融同日汇总额外留一交易日，实际发布可能延迟"
            if kind in ["index","margin-history"]:note="按下一交易日估计（日收盘 / 余额历史）；聚合源入库可能延迟，非实时行情"
            if end.year!=2026:note+="；新年度假期尚未核验"
            url=CAL["exchange_source"]
        else:
            row["时效状态"]="更新节奏待核对"
            return row
        row["预计下次更新"]=begin.isoformat() if begin==end else begin.isoformat()+" ～ "+end.isoformat()
        row["预计依据"]=note;row["依据链接"]=url
        row["时效状态"]="披露窗口中 · 不保证已齐全" if begin<=now.date()<=end and begin!=end else "在预计更新周期内"
        if now.date()>end:
            row["时效状态"]=f"已过预计更新日{(now.date()-end).days}天 · 核对是否已有下一期"
    if payload.get("historical") or (observed is not None and day and (latest:=observed_day(name,payload)) and day<latest):
        row["时效状态"]="历史对比期 · 不代表最新持仓 / 观测"
    if payload.get("attempt") and payload["attempt"][1]:row["时效状态"]+="；最近获取失败，保留旧数据"
    return row


def show(name,payload=None,*,observed=None,title=None):
    import streamlit as st
    payload=store.load(name) if payload is None else payload
    row=describe(name,payload,observed=observed,title=title)
    with st.container(border=True):
        st.caption("数据时效 · "+row["数据集"])
        st.markdown(f"**数据截至 / 报告期：** {row['数据截至 / 报告期']}　　**最近成功获取：** {row['最近成功获取（北京时间）']}（北京时间）")
        st.markdown(f"**预计下次更新：** {row['预计下次更新']}　　**时效判断：** {row['时效状态']}")
        link=f" [查看预计依据]({row['依据链接']})" if row["依据链接"] else ""
        st.caption(row["预计依据"]+"。"+row["本地获取方式"]+"。时效不等于准确性或完整性已核验。"+link)
        if payload and payload.get("acquisition_note"):st.caption(payload["acquisition_note"])
    return row


def state_payload(period,asset="股票"):
    if asset=="ETF":
        data=json.loads((store.ROOT/"resources/verified_etf_holdings.json").read_text(encoding="utf-8"))
        data["records"]=[r for r in data["records"] if r["报告期"]==period]
        imported=store.load("state-import")
        if imported:
            extra=[r for r in imported["records"] if r.get("报告期")==period and r.get("资产类型")==asset]
            if extra:
                data["records"].extend(extra);data["fetched_at"]=imported["fetched_at"]
                data["acquisition_note"]="包含历史样本和补充导入；最近获取是最新导入时间，不代表历史样本全部重新核验。"
        return data
    payload=store.load("state:"+period)
    if payload:
        with store.connect() as con:
            newer=con.execute("SELECT 1 FROM snapshots WHERE name LIKE 'state:%' AND name>? LIMIT 1",("state:"+period,)).fetchone()
        return {**payload,"historical":bool(newer)}
    imported=store.load("state-import")
    if imported:
        return {**imported,"records":[r for r in imported["records"] if r.get("报告期")==period and r.get("资产类型")==asset]}
    return None


def inventory():
    with store.connect() as con:
        names=[r[0] for r in con.execute("SELECT DISTINCT name FROM snapshots WHERE name NOT LIKE 'etf-day:%'")]
    items=[]
    for name in sorted(names):
        payload=store.load(name)
        if name.startswith(("state:","research:insurance:","research:private:")):
            prefix=name.rsplit(":",1)[0]+":"
            payload={**payload,"historical":any(n.startswith(prefix) and n>name for n in names)}
        items.append(describe(name,payload))
    required=["trade","pmi","house:北京:上海","estate","etf","cme","industry"]
    items.extend(describe(name) for name in required if name not in names)
    for name,file in [("state-etf-history","verified_etf_holdings.json"),("state-events","state_events.json")]:
        path=store.ROOT/"resources"/file
        if path.exists():items.append(describe(name,json.loads(path.read_text(encoding="utf-8"))))
    return pd.DataFrame(items)
