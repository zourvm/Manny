"""Named, disclosed state-investor positions. No attribution from ETF creations."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import json
import math
import re
import time
from urllib.parse import urlparse

import pandas as pd
import requests
from core import store

GROUPS = ["中央汇金", "中国证金", "中国国新", "中国诚通", "社保基金", "基本养老基金"]
ALIASES = {
    "中央汇金": ["中央汇金投资有限责任公司", "中央汇金资产管理有限责任公司"],
    "中国证金": ["中国证券金融股份有限公司"],
    "中国国新": ["国新投资有限公司"],
    "中国诚通": ["中国诚通控股集团有限公司", "北京诚通金控投资有限公司", "北京诚旸投资有限公司"],
}
FIELDS = ["主体", "账户名称", "资产类型", "标的代码", "标的名称", "报告期", "公告日期", "持有数量", "持仓市值（元）", "持有比例（%）", "披露口径", "核验状态", "原文链接", "核验入口"]
KEY = ["账户名称", "资产类型", "标的代码", "报告期", "披露口径"]
SOURCES = Path(__file__).resolve().parents[1] / "resources"
EASTMONEY = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def clean_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name)).replace("－", "-").replace("—", "-")


def classify(name: str) -> str | None:
    name = clean_name(name)
    for group, aliases in ALIASES.items():
        if name in aliases:
            return group
    if "汇金资管单一资产管理计划" in name:
        return "中央汇金"
    if "中证金融资产管理计划" in name:
        return "中国证金"
    if name.startswith("北京诚旸投资有限公司-"):
        return "中国诚通"
    if "基本养老保险基金" in name and name.endswith("组合"):
        return "基本养老基金"
    if name == "全国社会保障基金理事会" or ("社保基金" in name and name.endswith("组合")):
        return "社保基金"
    return None


def finite_or_none(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def normalize_stock(rows: list[dict], period: str) -> pd.DataFrame:
    result = []
    for row in rows:
        name = str(row.get("HOLDER_NAME", ""))
        group = classify(name)
        if not group:
            continue
        qty = finite_or_none(row.get("HOLD_NUM"))
        if qty is None or qty < 0:
            raise ValueError("具名持仓存在非法数量")
        actual_period = str(row["END_DATE"])[:10]
        if actual_period != period:
            raise ValueError("接口报告期与所选报告期不一致")
        notice = str(row.get("NOTICE_DATE") or row.get("UPDATE_DATE") or "")[:10]
        date.fromisoformat(notice)
        code = str(row["SECURITY_CODE"]).zfill(6)
        # The vendor repeats issuer shareholder lists on B-share security pages.
        # Keep this dashboard on A shares so those records are not summed twice.
        if code.startswith(("200", "900")):
            continue
        link = f"https://www.sse.com.cn/disclosure/listedinfo/regular/index.shtml?productId={code}" if code.startswith(("6", "9")) else "https://www.cninfo.com.cn/new/index"
        result.append({"主体": group, "账户名称": clean_name(name), "资产类型": "股票", "标的代码": code,
            "标的名称": str(row["SECURITY_NAME_ABBR"]), "报告期": actual_period, "公告日期": notice,
            "持有数量": qty, "持仓市值（元）": finite_or_none(row.get("HOLDER_MARKET_CAP")),
            "持有比例（%）": finite_or_none(row.get("FREE_HOLDNUM_RATIO")), "披露口径": "十大流通股东",
            "核验状态": "聚合数据·待原文复核", "原文链接": "", "核验入口": link})
    df = pd.DataFrame(result, columns=FIELDS)
    if not df.empty:
        df = df.sort_values("公告日期").drop_duplicates(KEY, keep="last")
    return df


def fetch_stock_period(period: str) -> dict:
    day = date.fromisoformat(period)
    if day > date.today() or (day.month, day.day) not in [(3,31),(6,30),(9,30),(12,31)]:
        raise ValueError("请选择已经结束的季度末")
    names = [n for values in ALIASES.values() for n in values]
    named = '(HOLDER_NAME in (' + ','.join('"' + n + '"' for n in names) + '))'
    # Pension accounts are under HOLDER_TYPE, not HOLDER_NEWTYPE. Verify every
    # returned name locally; vendor labels do not establish an ownership chain.
    filters = [named, '(HOLDER_NEWTYPE="国家队")', '(HOLDER_NEWTYPE="社保")', '(HOLDER_TYPE="基本养老基金")']
    raw, audits = [], []
    for condition in filters:
        params = {"reportName": "RPT_F10_EH_FREEHOLDERS", "columns": "ALL", "pageSize": 2000, "pageNumber": 1,
            "sortColumns": "UPDATE_DATE,SECURITY_CODE,HOLDER_RANK", "sortTypes": "-1,1,1", "source": "WEB", "client": "WEB",
            "filter": f"(END_DATE='{period}')" + condition}
        count, pages = 0, 1
        for page in range(1, 31):
            params["pageNumber"] = page
            response = requests.get(EASTMONEY, params=params, timeout=(6, 18))
            response.raise_for_status()
            body = response.json()
            result = body.get("result")
            if not result:
                if page == 1 and body.get("message") == "返回数据为空":
                    break
                raise ValueError("股东源未完成查询，请稍后重试")
            pages = int(result["pages"])
            if pages > 30:
                raise ValueError("查询页数过多，未保存不完整数据")
            batch = result.get("data") or []
            raw.extend(batch)
            count += len(batch)
            if page >= pages:
                if count != int(result["count"]):
                    raise ValueError("股东明细分页数量校验失败")
                break
            time.sleep(.15)
        audits.append({"query": condition, "rows": count, "pages": pages})
    frame = normalize_stock(raw, period)
    if frame.empty:
        raise ValueError("本报告期未取得可识别主体记录；保留旧数据")
    return {"records": json.loads(frame.to_json(orient="records", force_ascii=False)), "source": "东方财富十大流通股东（公开定期报告聚合）", "report_period": period,
            "queries": audits, "coverage": "已识别核心账户及数据源国家队、社保、基本养老基金分类中经名称校验的具名账户；不是全市场全部持仓，未识别关联方及间接持有不在范围。"}


def refresh_period(period: str) -> bool:
    from core.data import run_worker
    try:
        payload = run_worker("state-holdings", {"period": period}, timeout=180)
        store.save("state:" + period, payload)
        return True
    except Exception as ex:
        store.failure("state:" + period, type(ex).__name__ + ": " + str(ex)[:180])
        return False


def load_all() -> pd.DataFrame:
    frames = []
    seed = SOURCES / "verified_etf_holdings.json"
    if seed.exists():
        frames.append(pd.DataFrame(json.loads(seed.read_text(encoding="utf-8"))["records"]))
    with store.connect() as con:
        names = [row[0] for row in con.execute("SELECT DISTINCT name FROM snapshots WHERE name LIKE 'state:%' OR name='state-import'")]
    for name in names:
        payload = store.load(name)
        if payload:
            frames.append(pd.DataFrame(payload["records"]))
    if not frames:
        return pd.DataFrame(columns=FIELDS)
    df = pd.concat(frames, ignore_index=True)
    df["标的代码"] = df["标的代码"].astype(str).str.zfill(6)
    df = df[~((df["资产类型"] == "股票") & df["标的代码"].str.startswith(("200", "900")))].copy()
    df["账户名称"] = df["账户名称"].map(clean_name)
    df["主体"] = df["账户名称"].map(classify)
    for col in ["持有数量", "持仓市值（元）", "持有比例（%）"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # The same position may be present in several data sources, never sum it twice.
    return df.sort_values("公告日期").drop_duplicates(KEY, keep="last").reset_index(drop=True)


def compare(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    keys = ["账户名称", "资产类型", "标的代码", "披露口径"]
    left = df[df["报告期"] == start]
    right = df[df["报告期"] == end]
    result = left.merge(right, on=keys, how="outer", suffixes=("_期初", "_期末"), indicator=True, validate="one_to_one")
    result["主体"] = result["主体_期末"].combine_first(result["主体_期初"])
    result["标的名称"] = result["标的名称_期末"].combine_first(result["标的名称_期初"])
    result["披露状态"] = result["_merge"].map({"both": "两期均披露", "left_only": "本期未披露", "right_only": "新披露（非确认新买入）"}).astype(str)
    common = result["_merge"] == "both"
    result["数量变化"] = (result["持有数量_期末"] - result["持有数量_期初"]).where(common)
    result["数量变化率（%）"] = (result["数量变化"] / result["持有数量_期初"].replace(0, float("nan")) * 100).where(common)
    result["样本市值变化（元）"] = (result["持仓市值（元）_期末"] - result["持仓市值（元）_期初"]).where(common)
    return result


def validate_import(df: pd.DataFrame) -> pd.DataFrame:
    required = ["账户名称", "资产类型", "标的代码", "标的名称", "报告期", "公告日期", "持有数量", "披露口径", "原文链接"]
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError("缺少字段：" + "、".join(sorted(missing)))
    df = df.copy()
    for col in required:
        if df[col].isna().any():
            raise ValueError(f"{col} 不能缺失")
    df["账户名称"] = df["账户名称"].map(clean_name)
    df["主体"] = df["账户名称"].map(classify)
    if df["主体"].isna().any():
        raise ValueError("包含未识别账户，请使用披露文件中的完整名称")
    if not df["资产类型"].isin(["股票", "ETF"]).all():
        raise ValueError("资产类型必须是股票或 ETF")
    for col in ["报告期", "公告日期"]:
        df[col] = df[col].map(lambda x: date.fromisoformat(str(x)[:10]).isoformat())
    if (df["公告日期"] < df["报告期"]).any() or (df["公告日期"] > date.today().isoformat()).any():
        raise ValueError("公告日期不能早于报告期或晚于今天")
    for col in ["持有数量", "持仓市值（元）", "持有比例（%）"]:
        df[col] = pd.to_numeric(df[col], errors="raise") if col in df else None
        valid = df[col].dropna()
        if not valid.map(math.isfinite).all() or (valid < 0).any():
            raise ValueError(f"{col} 必须为有限非负数")
    if df["持有数量"].isna().any():
        raise ValueError("持有数量不能缺失或留空")
    for value in df["原文链接"]:
        parsed = urlparse(str(value))
        if parsed.scheme not in ["https", "http"] or not parsed.hostname:
            raise ValueError("原文链接必须是 http(s) 来源地址")
    df["标的代码"] = df["标的代码"].astype(str).str.zfill(6)
    if not df["标的代码"].str.fullmatch(r"\d{6}").all():
        raise ValueError("标的代码必须为 6 位数字")
    if df.duplicated(KEY).any():
        raise ValueError("存在相同报告期、账户、标的和披露口径的重复记录")
    df["核验状态"] = "用户导入·待核验"
    df["核验入口"] = df["原文链接"]
    return df[FIELDS]


def save_import(df: pd.DataFrame) -> None:
    old = store.load("state-import")
    if old:
        df = pd.concat([pd.DataFrame(old["records"]), df]).drop_duplicates(KEY, keep="last")
    store.save("state-import", {"records": json.loads(df.to_json(orient="records", force_ascii=False)), "source": "用户具名持仓导入"})


def load_events() -> pd.DataFrame:
    path = SOURCES / "state_events.json"
    return pd.DataFrame(json.loads(path.read_text(encoding="utf-8"))["records"]) if path.exists() else pd.DataFrame()
