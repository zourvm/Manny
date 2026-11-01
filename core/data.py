from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import json
import math
import os
import re
import subprocess
import sys

import pandas as pd
from core import store
from core.national_team import group_shares

CATALOG = {
    "trade": {"title": "进出口总量", "function": "macro_china_hgjck", "source": "https://data.eastmoney.com/cjsj/hgjck.html", "unit": "金额：亿美元；增速：%", "frequency": "月度", "note": "人民币与美元口径不可混用；当月与年初累计分别展示。"},
    "pmi": {"title": "采购经理指数", "function": "macro_china_pmi", "source": "https://data.eastmoney.com/cjsj/pmi.html", "unit": "指数点", "frequency": "月度", "note": "制造业 PMI 以 50 为荣枯分界；不等于投资收益信号。"},
    "house": {"title": "城市住宅价格", "function": "macro_china_new_house_price", "source": "https://data.eastmoney.com/cjsj/newhouse.html", "unit": "指数，上期/上年同期=100", "frequency": "月度", "note": "同比/环比为指数，减 100 后才是对应涨跌幅（%）。"},
    "estate": {"title": "国房景气指数", "function": "macro_china_real_estate", "source": "https://data.eastmoney.com/cjsj/hyzs_list_EMM00121987.html", "unit": "指数点", "frequency": "月度", "note": "全国行业景气，不能替代销售金额、成交面积或城市房价。"},
}


def records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))


def canonical_date(value) -> str:
    s = str(value).strip()
    match = re.match(r"^(\d{4})年(\d{1,2})月", s)
    if match:
        return date(int(match[1]), int(match[2]), 1).isoformat()
    timestamp = pd.Timestamp(s)
    if pd.isna(timestamp):
        raise ValueError("统计期不能缺失")
    return timestamp.date().isoformat()


def normalize_macro(df: pd.DataFrame, key: str) -> pd.DataFrame:
    if df.empty:
        raise ValueError("数据源返回空表")
    df = df.copy()
    date_col = "月份" if "月份" in df else "日期"
    if date_col not in df:
        raise ValueError("找不到统计期字段，数据源可能已改版")
    if date_col != "日期":
        df["统计期"] = df[date_col].astype(str)
    df["日期"] = df[date_col].map(canonical_date)
    if date_col != "日期":
        df = df.drop(columns=[date_col])
    for col in df.columns:
        if col not in ("日期", "城市", "统计期"):
            df[col] = pd.to_numeric(df[col], errors="coerce").replace([float("inf"), -float("inf")], float("nan"))
    numeric = df.select_dtypes(include="number")
    if numeric.empty or not numeric.notna().any().any():
        raise ValueError("没有可用数值，保留旧快照")
    if key == "trade":
        for c in ["当月出口额-金额", "当月进口额-金额"]:
            if c not in df:
                raise ValueError("进出口字段不完整")
        # Eastmoney's own hgjck.js uses zoom:-5: source is USD thousands,
        # display is USD 100 million. AKShare returns the raw source number.
        for c in ["当月出口额-金额", "当月进口额-金额", "累计出口额-金额", "累计进口额-金额"]:
            if c in df:
                df[c] = df[c] / 100_000
        df["当月贸易差额"] = df["当月出口额-金额"] - df["当月进口额-金额"]
    return df.sort_values("日期").drop_duplicates(["日期", "城市"] if "城市" in df else ["日期"])


def worker_fetch(key: str, args: dict) -> dict:
    if key == "cme-web":
        from core.fed_web import fetch_public
        return fetch_public()
    if key == "state-industries":
        from core.state_industry import fetch_industries
        return fetch_industries(args["period"])
    if key == "state-holdings":
        from core.state_holdings import fetch_stock_period
        return fetch_stock_period(args["period"])
    import akshare as ak
    import requests
    request = requests.sessions.Session.request

    def bounded(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (6, 12))
        return request(self, method, url, **kwargs)

    requests.sessions.Session.request = bounded
    if key in ["market-index","market-margin"]:
        from core.market_compare import fetch_index,fetch_margin_history
        return fetch_index(args,ak) if key=="market-index" else fetch_margin_history(args,ak)
    if key == "research":
        from core.fund_research import fetch
        return fetch(args["kind"], args["params"], ak)
    if key == "etf-day":
        requested = date.fromisoformat(args["date"])
        errors = []
        for offset in range(5):
            day = requested - timedelta(days=offset)
            if day.weekday() > 4:
                continue
            try:
                raw = ak.fund_etf_scale_sse(date=day.strftime("%Y%m%d"))
                actual = {canonical_date(v) for v in raw["统计日期"].dropna()}
                if len(actual) != 1 or not (requested - timedelta(days=4)).isoformat() <= next(iter(actual)) <= requested.isoformat():
                    raise ValueError("交易所返回的实际日期不合法")
                frame = group_shares(raw, next(iter(actual)))
                return {"records": records(frame), "source": "https://www.sse.com.cn/assortment/fund/etf/list/scale/", "requested_date": str(requested)}
            except Exception as ex:
                errors.append(type(ex).__name__)
                if isinstance(ex, requests.RequestException):
                    break
        raise ValueError("ETF 日快照不可用：" + "/".join(errors))
    spec = CATALOG[key]
    params = {"city_first": args.get("city1", "北京"), "city_second": args.get("city2", "上海")} if key == "house" else {}
    raw = getattr(ak, spec["function"])(**params)
    frame = normalize_macro(raw, key)
    return {"records": records(frame), "raw_records": records(raw), "source": spec["source"], "unit": spec["unit"], "function": spec["function"], "schema_version": 1,
            "conversion": "原始金额为千美元，除以 100000 后为亿美元" if key == "trade" else "保留源数值"}


def run_worker(key: str, args: dict | None = None, timeout: int = 40) -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "MACRO_DESK_DATA": str(store.data_dir())}
    result = subprocess.run([sys.executable, str(store.ROOT / "fetch_worker.py"), key, json.dumps(args or {})],
                            cwd=store.ROOT, env=env, text=True, encoding="utf-8", capture_output=True,
                            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("取数进程未完成")
    payload = json.loads(result.stdout)
    if "error" in payload:
        raise ValueError(payload["error"])
    return payload


def refresh(key: str, args: dict | None = None) -> bool:
    name = key if key != "house" else f"house:{(args or {}).get('city1', '北京')}:{(args or {}).get('city2', '上海')}"
    try:
        store.save(name, run_worker(key, args))
        return True
    except Exception as ex:
        message = "请求超过 40 秒，请稍后重试" if isinstance(ex, subprocess.TimeoutExpired) else str(ex)[:250]
        store.failure(name, message)
        return False


def refresh_many(keys: list[str]) -> dict[str, bool]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(refresh, k): k for k in keys}
        return {jobs[f]: f.result() for f in as_completed(jobs)}


def refresh_etf(start: date, end: date, freq: str = "W-FRI", progress=None) -> tuple[int, int]:
    dates = {d.date() for d in pd.date_range(start, end, freq=freq)}
    dates.add(end)
    dates = sorted(d for d in dates if d <= end)
    if len(dates) > 60:
        raise ValueError("单次最多 60 个采样点，请缩小区间或改用月度")
    ok = failed = 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {}
        for d in dates:
            name = f"etf-day:{d}"
            if store.load(name):
                ok += 1
            else:
                jobs[pool.submit(run_worker, "etf-day", {"date": str(d)}, 30)] = name
        for f in as_completed(jobs):
            name = jobs[f]
            try:
                store.save(name, f.result())
                ok += 1
            except Exception as ex:
                store.failure(name, type(ex).__name__ + ": " + str(ex)[:100])
                failed += 1
            if progress:
                progress(ok + failed, len(dates))
    payloads = [store.load(f"etf-day:{d}") for d in dates]
    good = [pd.DataFrame(p["records"]) for p in payloads if p]
    if good:
        merged = pd.concat(good).drop_duplicates(["日期", "基金代码"]).sort_values(["日期", "基金代码"])
        old = store.load("etf")
        if old:
            merged = pd.concat([pd.DataFrame(old["records"]), merged]).drop_duplicates(["日期", "基金代码"], keep="last").sort_values(["日期", "基金代码"])
        store.save("etf", {"records": records(merged), "source": "https://www.sse.com.cn/assortment/fund/etf/list/scale/", "failures": failed})
    if failed:
        store.failure("etf", f"本次 {failed} 个采样点失败；图表只含成功的实际日期")
    return ok, failed


def validate_industry(df: pd.DataFrame) -> pd.DataFrame:
    required = ["日期", "分类口径", "类别", "方向", "金额", "币种", "单位", "统计口径", "来源"]
    if not set(required).issubset(df.columns):
        raise ValueError("缺少必需字段：" + "、".join(c for c in required if c not in df))
    df = df[required].copy()
    if df.isna().any().any():
        raise ValueError("必需字段不能留空")
    df["日期"] = df["日期"].map(canonical_date)
    for col in [c for c in required if c not in ("日期", "金额")]:
        df[col] = df[col].astype(str).str.strip()
        if df[col].eq("").any():
            raise ValueError(f"{col} 不能留空")
    df["金额"] = pd.to_numeric(df["金额"], errors="raise")
    if ((df["金额"] < 0) | ~df["金额"].map(math.isfinite)).any():
        raise ValueError("金额须为有限非负数，缺失不能补零")
    if not df["方向"].isin(["进口", "出口"]).all() or not df["统计口径"].isin(["当月", "累计"]).all():
        raise ValueError("方向应为进口/出口；统计口径应为当月/累计")
    if df.duplicated([c for c in required if c not in ("金额", "来源")]).any():
        raise ValueError("存在同日期同类别的重复记录，请先明确口径")
    return df.sort_values("日期")


if __name__ == "__main__":
    print(json.dumps(refresh_many(["trade", "pmi", "estate", "house"]), ensure_ascii=False))
