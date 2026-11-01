"""Industry aggregation of disclosed positions, with constant-price quantity changes."""
from datetime import date
import json
import pandas as pd
import requests
from core import store
from core.state_holdings import EASTMONEY, compare

MAP_KEY = "state-industries"
MAP_COLUMNS = ["标的代码", "标的名称", "行业"]


def normalize_industries(rows, period):
    records = []
    for row in rows:
        if str(row["REPORTDATE"])[:10] != period:
            raise ValueError("行业源报告期不一致")
        code = str(row["SECURITY_CODE"]).zfill(6)
        sector = row.get("PUBLISHNAME")
        if sector and str(sector).strip() not in ["-", "--"]:
            records.append({"标的代码": code, "标的名称": row["SECURITY_NAME_ABBR"], "行业": str(sector).strip()})
    frame = pd.DataFrame(records, columns=MAP_COLUMNS).drop_duplicates()
    if frame.empty or frame["标的代码"].duplicated().any():
        raise ValueError("行业分类为空或同一股票存在冲突分类")
    return frame


def fetch_industries(period):
    if date.fromisoformat(period) > date.today():
        raise ValueError("行业来源报告期不能晚于今天")
    params = {"reportName":"RPT_LICO_FN_CPD", "columns":"SECURITY_CODE,SECURITY_NAME_ABBR,PUBLISHNAME,REPORTDATE",
        "pageSize":500, "pageNumber":1, "sortColumns":"SECURITY_CODE", "sortTypes":"1",
        "filter":f"(REPORTDATE='{period}')(SECURITY_TYPE=\"A股\")"}
    rows = []
    for page in range(1, 31):
        params["pageNumber"] = page
        response = requests.get(EASTMONEY,params=params,timeout=(6,18))
        response.raise_for_status()
        body = response.json()
        result = body.get("result")
        if not result or int(result["pages"]) > 30:
            raise ValueError("行业接口未返回完整可用分页：" + str(body.get("message",""))[:100])
        rows.extend(result.get("data") or [])
        if page >= int(result["pages"]):
            if len(rows) != int(result["count"]):
                raise ValueError("行业分页数量校验失败")
            break
    frame = normalize_industries(rows,period)
    return {"records": json.loads(frame.to_json(orient="records",force_ascii=False)),
        "source": f"https://data.eastmoney.com/bbsj/{period[:4]}{period[5:7]}/yjbb.html",
        "source_period": period, "classification":"东方财富财报所处行业（PUBLISHNAME）",
        "note":"行业标签按采集时源数据保存；同一映射用于回看各期，不代表历史时点行业归属。"}


def refresh_industries(period):
    from core.data import run_worker
    try:
        store.save(MAP_KEY,run_worker("state-industries",{"period":period},timeout=120))
        return True
    except Exception as ex:
        store.failure(MAP_KEY,type(ex).__name__+": "+str(ex)[:180])
        return False


def industry_map():
    payload = store.load(MAP_KEY)
    return pd.DataFrame(payload["records"],columns=MAP_COLUMNS) if payload else pd.DataFrame(columns=MAP_COLUMNS)


def attach_industries(frame, mapping):
    mapped = mapping[["标的代码","行业"]].copy()
    if mapped["标的代码"].duplicated().any():
        raise ValueError("行业映射存在重复代码")
    result = frame.drop(columns="行业",errors="ignore").merge(mapped,on="标的代码",how="left",validate="many_to_one")
    result["行业"] = result["行业"].fillna("未分类")
    return result


def industry_snapshot(frame, mapping):
    data = attach_industries(frame,mapping)
    grouped = data.groupby("行业",sort=False)
    result = grouped.agg(持仓记录=("标的代码","size"),股票数量=("标的代码","nunique"),有市值记录=("持仓市值（元）","count")).reset_index()
    values = grouped["持仓市值（元）"].sum(min_count=1).rename("可见市值（元）").reset_index()
    result = result.merge(values,on="行业")
    total = result["可见市值（元）"].sum(min_count=1)
    result["可见市值占比（%）"] = result["可见市值（元）"] / total * 100 if pd.notna(total) and total > 0 else float("nan")
    result["可见市值（亿元）"] = result["可见市值（元）"] / 1e8
    return result.sort_values("可见市值（元）",ascending=False,na_position="last")


def industry_changes(frame, mapping, start, end):
    changes = attach_industries(compare(frame,start,end),mapping)
    common = changes["披露状态"] == "两期均披露"
    can_value = common & (changes["持有数量_期初"] > 0) & (changes["持仓市值（元）_期初"] > 0)
    changes["期初参考价（元）"] = (changes["持仓市值（元）_期初"] / changes["持有数量_期初"].replace(0,float("nan"))).where(can_value)
    changes["数量变动参考额（元）"] = changes["数量变化"] * changes["期初参考价（元）"]
    rows = []
    for sector, group in changes.groupby("行业",sort=False):
        both = group[group["披露状态"] == "两期均披露"]
        priced = both[both["数量变动参考额（元）"].notna()]
        net = priced["数量变动参考额（元）"].sum(min_count=1)
        base = priced["持仓市值（元）_期初"].sum(min_count=1)
        rows.append({"行业":sector,"两期可比记录":len(both),"估值覆盖记录":len(priced),
            "增持记录":int((both["数量变化"]>0).sum()),"减持记录":int((both["数量变化"]<0).sum()),
            "不变记录":int((both["数量变化"]==0).sum()),
            "新披露记录":int((group["披露状态"]=="新披露（非确认新买入）").sum()),
            "本期未披露":int((group["披露状态"]=="本期未披露").sum()),
            "增持参考额（亿元）":priced["数量变动参考额（元）"].clip(lower=0).sum()/1e8 if len(priced) else float("nan"),
            "减持参考额（亿元）":priced["数量变动参考额（元）"].clip(upper=0).sum()/1e8 if len(priced) else float("nan"),
            "净数量变动参考额（亿元）":net/1e8,
            "期初可估算市值（亿元）":base/1e8,
            "可比数量变动率（%）":net/base*100 if pd.notna(base) and base>0 else float("nan")})
    return pd.DataFrame(rows), changes
