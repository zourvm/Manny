"""Read CME's public FedWatch HTML using its normal session and form navigation.

No paid endpoint, API credential, browser driver or third-party probability feed.
Blank HTML cells remain missing; only explicitly printed numbers are imported.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from hashlib import sha256
import os
import re
from urllib.parse import urljoin,urlparse
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from bs4 import BeautifulSoup
from core import cme,store

PUBLIC="https://www.cmegroup.cn/fed-watch/"
TOOL_HOST="cmegroup-tools.quikstrike.net"
MONTHS={name:i+1 for i,name in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())}


def parse_day(text):
    match=re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{2,4})",str(text),re.I)
    if not match or match[2].title() not in MONTHS:
        raise ValueError("公开网页日期格式已改变")
    year=int(match[3]);year=year+2000 if year<100 else year
    return datetime(year,MONTHS[match[2].title()],int(match[1])).date().isoformat()


def pct(text):
    if not re.fullmatch(r"\d+(?:\.\d+)?\s*%",text.strip()):
        raise ValueError("网页概率不是明确百分比，未将未知值补零")
    value=float(text.strip().rstrip("%").strip())
    if not 0<=value<=100:raise ValueError("网页概率超出范围")
    return value/100


def table_named(soup,needle):
    candidates=[t for t in soup.select("table.grid-thm") if needle in t.get_text(" ",strip=True)]
    if len(candidates)!=1:raise ValueError("未找到唯一的公开概率表："+needle)
    return candidates[0]


def parse_view(html,expected_meeting=None):
    soup=BeautifulSoup(html,"html.parser")
    info=table_named(soup,"Meeting Date")
    cells=info.find_all("td")
    if len(cells)<4:raise ValueError("会议资料未完整加载")
    meeting=parse_day(cells[0].get_text(" ",strip=True))
    if expected_meeting and meeting!=expected_meeting:
        raise ValueError("会议切换未生效，拒绝将上一场数据写给下一场")
    table=table_named(soup,"Target Rate (bps)")
    stamp=re.search(r"Data as of\s+(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+CT",table.get_text(" ",strip=True))
    if not stamp:raise ValueError("网页缺少实际数据更新时间")
    day=parse_day(stamp[1]);clock=datetime.strptime(stamp[2],"%H:%M:%S" if stamp[2].count(":")==2 else "%H:%M").time()
    source_dt=datetime.combine(datetime.fromisoformat(day).date(),clock,ZoneInfo("America/Chicago"))
    if source_dt>datetime.now(timezone.utc)+timedelta(minutes=15):
        raise ValueError("网页时间晚于当前时间")
    headers=table.find_all("tr")[1].find_all("th")
    if len(headers)!=4 or not headers[0].get_text().strip().startswith("Now"):
        raise ValueError("网页概率列已变化，拒绝猜测日期或列顺序")
    dates=[day]
    for header in headers[1:]:
        text=header.get_text(" ",strip=True)
        # Newly listed far meetings can have no historical observations. Do
        # not invent their dates from "1 Day / Week / Month" labels.
        dates.append(parse_day(text) if re.search(r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}",text) else None)
    labels=["Now","1 Day","1 Week","1 Month"]
    rows=[];base=None;blanks=0
    for tr in table.find_all("tr"):
        cells=tr.find_all("td",recursive=False)
        if len(cells)!=5:continue
        band=re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)(?:\s*\(Current\))?",cells[0].get_text(" ",strip=True))
        if not band:continue
        lo,hi=int(band[1])/100,int(band[2])/100
        if "Current" in cells[0].get_text():
            if base is not None:raise ValueError("网页出现多个当前利率区间")
            base=(lo,hi)
        for i,cell in enumerate(cells[1:]):
            text=cell.get_text(" ",strip=True)
            if dates[i] is None:
                if text:raise ValueError("历史概率有数值但缺少原始日期")
                blanks+=1;continue
            if not text:
                if "hide" not in (tr.get("class") or []):raise ValueError("可见区间存在未知概率")
                blanks+=1;continue
            rows.append({"会议日期":meeting,"报告日期":dates[i],"利率下限（%）":lo,"利率上限（%）":hi,"概率":pct(text),
                "来源时间（原文）":stamp[0] if i==0 else None,"网页数据截至（原文）":stamp[0],
                "网页栏目":labels[i],"网页数据截至（北京时间）":source_dt.astimezone(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")})
    if base is None:raise ValueError("网页缺少Current基准区间")
    frame=cme.validate_distribution(pd.DataFrame(rows))
    frame["基准利率下限（%）"]=[base[0] if v=="Now" else None for v in frame["网页栏目"]]
    frame["基准利率上限（%）"]=[base[1] if v=="Now" else None for v in frame["网页栏目"]]
    summary=table_named(soup,"No Change")
    summary_rows=[tr.find_all("td",recursive=False) for tr in summary.find_all("tr")]
    numeric=next((v for v in summary_rows if len(v)==3),None)
    if not numeric:raise ValueError("网页缺少降息/维持/加息校验值")
    stated=[pct(c.get_text(" ",strip=True)) for c in numeric]
    now=frame[frame["网页栏目"]=="Now"]
    calculated=[now.loc[now["利率下限（%）"]<base[0],"概率"].sum(),
        now.loc[now["利率下限（%）"]==base[0],"概率"].sum(),now.loc[now["利率下限（%）"]>base[0],"概率"].sum()]
    if any(abs(a-b)>.002+1e-12 for a,b in zip(stated,calculated)):
        raise ValueError("详细概率与网页降息/维持/加息汇总不一致")
    for name,value in zip(["网页降息概率","网页维持概率","网页加息概率"],stated):
        frame[name]=[value if v=="Now" else None for v in frame["网页栏目"]]
    return frame,{"meeting":meeting,"data_date":day,"source_time":stamp[0],"source_time_iso":source_dt.isoformat(),
        "blank_cells_skipped":blanks,"unavailable_history_columns":[labels[i] for i,d in enumerate(dates) if d is None],
        "current_target":list(base),"source_table_hash":sha256(str(table).encode()).hexdigest()}


def navigation(soup):
    links=soup.select('a[id*="lbMeeting"]')
    if not links or len(links)>16:raise ValueError("公开会议列表为空或超出预期")
    result=[]
    for a in links:
        event=re.search(r"__doPostBack\('([^']+)'\s*,\s*'([^']*)'\)",a.get("href",""))
        if not event:raise ValueError("公开会议标签不再使用预期的表单跳转")
        result.append((parse_day(a.get_text(" ",strip=True)),event[1],event[2]))
    if len({v[0] for v in result})!=len(result):raise ValueError("公开会议日期重复")
    return result


def form_data(soup,target,argument):
    data={i["name"]:i.get("value","") for i in soup.select('form input[type="hidden"][name]')}
    if not data.get("__VIEWSTATE"):raise ValueError("缺少公开表单状态，未继续切换会议")
    for select in soup.select("form select[name]"):
        option=select.find("option",selected=True) or select.find("option")
        data[select["name"]]=option.get("value","") if option else ""
    data["__EVENTTARGET"]=target;data["__EVENTARGUMENT"]=argument
    return data


def check_tool_url(url):
    parsed=urlparse(url)
    if parsed.scheme!="https" or parsed.hostname!=TOOL_HOST or parsed.path not in ["/User/QuikStrikeTools.aspx","/User/QuikStrikeView.aspx"]:
        raise ValueError("网页嵌入或跳转地址改变，未访问未知目标")
    return url


def fetch_public():
    with requests.Session() as session:
        session.headers.update({"User-Agent":"Mozilla/5.0 MacroDesk/0.1 public-data-reader"})
        landing=session.get(PUBLIC,timeout=(6,18));landing.raise_for_status()
        page=BeautifulSoup(landing.text,"html.parser")
        iframe=next((f for f in page.find_all("iframe",src=True) if urlparse(urljoin(PUBLIC,f["src"])).hostname==TOOL_HOST),None)
        if iframe is None:raise ValueError("CME公开页面没有FedWatch嵌入工具")
        entry=check_tool_url(urljoin(PUBLIC,iframe["src"]))
        session.headers["Referer"]=PUBLIC
        first=session.get(entry,timeout=(6,18));first.raise_for_status();check_tool_url(first.url)
        session.headers["Referer"]=first.url
        # This is the normal redirect declared by the public mobile_redirect()
        # bootstrap script. Reuse its anonymous page session, without logging in.
        view_url=check_tool_url(first.url.replace("/User/QuikStrikeTools.aspx","/User/QuikStrikeView.aspx"))
        response=session.get(view_url,timeout=(6,22));response.raise_for_status();check_tool_url(response.url)
        soup=BeautifulSoup(response.text,"html.parser")
        meetings=navigation(soup)
        frames=[];audits=[]
        for index,(meeting,target,argument) in enumerate(meetings):
            if index:
                form=soup.find("form")
                if not form:raise ValueError("公开会议表单未加载")
                action=check_tool_url(urljoin(response.url,form.get("action","")))
                data=form_data(soup,target,argument)
                session.headers["Referer"]=response.url
                response=session.post(action,data=data,timeout=(6,22));response.raise_for_status();check_tool_url(response.url)
                soup=BeautifulSoup(response.text,"html.parser")
            frame,audit=parse_view(response.text,meeting)
            frames.append(frame);audits.append(audit)
        combined=cme.validate_distribution(pd.concat(frames,ignore_index=True))
        dates={a["data_date"] for a in audits}
        if len(dates)!=1:raise ValueError("各会议数据日不一致，未合并为一次完整快照")
        source_date=next(iter(dates))
        if (cme.today_cn()-datetime.fromisoformat(source_date).date()).days>7:
            raise ValueError("网页最新数据超过7天，未覆盖现有快照")
        from core.data import records
        return {"records":records(combined),"source":PUBLIC,"audit":{"method":"CME公开HTML及会议表单正常导航",
            "source":PUBLIC,"meeting_count":len(meetings),"data_date":source_date,"fetched_at":store.now(),
            "meetings":audits,"units":"利率百分比；概率0–1","note":"历史比较列保留原始日期；空单元格不补零。未使用付费API、密钥或第三方概率。"}}


def refresh_due():
    if os.environ.get("MACRO_DESK_OFFLINE")=="1":return False
    with store.connect() as con:
        last=con.execute("SELECT attempted_at FROM attempts WHERE name='cme-web'").fetchone()
    return not last or (datetime.now(timezone.utc)-datetime.fromisoformat(last[0])).total_seconds()>=900


def meeting_outcomes(frame):
    """Only use a baseline explicitly printed for that snapshot, never guess it."""
    output=[]
    for meeting,history in frame.groupby("会议日期",sort=True):
        last=history[history["报告日期"]==history["报告日期"].max()]
        baseline=last["基准利率下限（%）"].dropna().unique() if "基准利率下限（%）" in last else []
        if len(baseline)!=1:continue
        value=float(baseline[0]);lo=last["利率下限（%）"]
        row={"会议日期":meeting,"观测日期":last["报告日期"].iloc[0],"网页基准下限（%）":value,
            "降息（%）":last.loc[lo<value,"概率"].sum()*100,
            "维持（%）":last.loc[lo==value,"概率"].sum()*100,
            "加息（%）":last.loc[lo>value,"概率"].sum()*100,"概率口径":"明细计算"}
        for field,source in [("降息（%）","网页降息概率"),("维持（%）","网页维持概率"),("加息（%）","网页加息概率")]:
            values=last[source].dropna().unique() if source in last else []
            if len(values)==1:row[field]=float(values[0])*100;row["概率口径"]="网页汇总"
        output.append(row)
    return pd.DataFrame(output)


def refresh_public():
    from core.data import run_worker
    try:
        payload=run_worker("cme-web",timeout=150)
        frame=cme.validate_distribution(pd.DataFrame(payload["records"]))
        cme.save_snapshot(frame,source=PUBLIC,method="CME公开网页自动读取",audit=payload["audit"])
        with store.connect() as con:
            con.execute("INSERT OR REPLACE INTO attempts VALUES(?,?,?)",("cme-web",store.now(),""))
        return True
    except Exception as ex:
        message=type(ex).__name__+": "+str(ex)[:180]
        store.failure("cme-web",message);store.failure("cme",message)
        return False
