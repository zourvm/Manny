"""CME FedWatch EOD adapter, based on CME's published message specification."""
from __future__ import annotations

from datetime import date, datetime
import math
import uuid
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
import pandas as pd
import requests

BASE = "https://markets.api.cmegroup.com/fedwatch/v1"
DOC = "https://www.cmegroup.com/market-data/market-data-api/fedwatch-api.html"
WEB = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool"
GUIDE = "https://www.cmegroup.com/tools-information/quikstrike/cme-fedwatch-tool-user-guide.html"
INPUT_COLUMNS = ["会议日期", "报告日期", "利率下限（%）", "利率上限（%）", "概率（%）"]


def today_cn():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def validate_distribution(frame):
    """Rates are percentages; normalized probabilities are 0–1."""
    columns = ["会议日期", "报告日期", "利率下限（%）", "利率上限（%）", "概率"]
    if frame.empty or not set(columns).issubset(frame.columns):
        raise ValueError("请填入完整的会议、日期、利率区间和概率")
    result = frame.copy()
    for col in columns[:2]:
        result[col] = result[col].map(lambda value: date.fromisoformat(str(value)[:10]).isoformat())
    if (result["报告日期"] > result["会议日期"]).any() or (result["报告日期"] > today_cn().isoformat()).any():
        raise ValueError("报告日期不能晚于会议日期或今天；请按网页原始日期填写")
    for col in columns[2:]:
        result[col] = pd.to_numeric(result[col], errors="raise")
        if not result[col].map(math.isfinite).all():
            raise ValueError("利率和概率不能留空，不能使用无限值或未知值")
    if ((result["概率"] < 0) | (result["概率"] > 1)).any():
        raise ValueError("概率必须在0%至100%之间")
    lo, hi = result["利率下限（%）"], result["利率上限（%）"]
    if ((lo < -10) | (hi > 30) | (hi <= lo)).any():
        raise ValueError("利率区间不合法。这里填百分比，例如3.25，不是325基点")
    if result.duplicated(["会议日期", "报告日期", "利率下限（%）"]).any():
        raise ValueError("同一会议、报告日存在重复利率区间")
    for _, group in result.groupby(["会议日期", "报告日期"]):
        ordered = group.sort_values("利率下限（%）")
        lows = ordered["利率下限（%）"].to_numpy()
        highs = ordered["利率上限（%）"].to_numpy()
        if len(group) > 1 and (highs[:-1] > lows[1:] + 1e-9).any():
            raise ValueError("同一份快照的利率区间不能重叠")
        if abs(group["概率"].sum() - 1) > .002 + 1e-12:
            raise ValueError("每场会议、每个报告日的概率合计应约为100%（舍入误差最多0.2个百分点）；请录全所有区间")
    return result.sort_values(["会议日期", "报告日期", "利率下限（%）"]).reset_index(drop=True)


def normalize_manual(frame, meeting=None, reporting=None):
    """Blank editor rows never become zero-probability predictions."""
    result = frame.dropna(how="all").copy()
    if result.empty:
        raise ValueError("请先填入网页中的完整概率分布，空白表不会保存")
    if meeting is not None: result["会议日期"] = str(meeting)
    if reporting is not None: result["报告日期"] = str(reporting)
    missing = set(INPUT_COLUMNS) - set(result.columns)
    if missing: raise ValueError("缺少字段：" + "、".join(sorted(missing)))
    if result[INPUT_COLUMNS].isna().any().any():
        raise ValueError("会议日期、报告日期、利率区间和概率均须填写；未知概率不能填0")
    result["概率"] = pd.to_numeric(result["概率（%）"], errors="raise") / 100
    return validate_distribution(result.drop(columns="概率（%）"))


def save_snapshot(frame, source=WEB, source_time="", method="网页对照录入", audit=None):
    """Replace a whole meeting/day distribution, preserving other dates and origins."""
    from core import store
    from core.data import records
    parsed = urlparse(source)
    if parsed.scheme not in ["http", "https"] or not parsed.hostname:
        raise ValueError("请填写可核对的http(s)来源网页地址")
    result = validate_distribution(frame)
    result["来源"] = source
    if "来源时间（原文）" not in result:
        result["来源时间（原文）"] = source_time.strip() or None
    result["录入方式"] = method
    result["录入时间（北京时间）"] = store.now()
    previous = store.load("cme")
    if previous:
        old = pd.DataFrame(previous["records"])
        if "来源" not in old: old["来源"] = previous.get("source", "历史导入·来源待核对")
        replacement = set(zip(result["会议日期"], result["报告日期"]))
        old = old[[pair not in replacement for pair in zip(old["会议日期"], old["报告日期"])]]
        result = pd.concat([old, result], ignore_index=True)
    result = result.sort_values(["会议日期", "报告日期", "利率下限（%）"])
    store.save("cme", {"records": records(result), "source": "CME FedWatch 公开网页 / 本地快照档案", "public_audit":audit,
        "note": "按会议和报告日保存每日快照；同日更新整份分布，其他日期保留。网页时间未知时留空；概率不强制归一化。"})


def normalize(payload: dict) -> pd.DataFrame:
    rows = []
    for forecast in payload.get("payload", []):
        meeting = date.fromisoformat(str(forecast["meetingDt"]))
        reporting = date.fromisoformat(str(forecast["reportingDt"]))
        if reporting > meeting:
            raise ValueError("报告日期不能晚于会议日期")
        seen = set()
        total = 0.0
        for band in forecast.get("rateRange", []):
            if band.get("probability") is None:
                raise ValueError("存在未知概率，不能计算完整分布")
            p = float(band["probability"])
            lo, hi = float(band["lowerRt"]), float(band["upperRt"])
            if not all(math.isfinite(v) for v in (p, lo, hi)) or not 0 <= p <= 1 or hi <= lo or (lo, hi) in seen:
                raise ValueError("利率区间或概率不合法；概率应在 0–1 之间")
            seen.add((lo, hi))
            total += p
            rows.append({"会议日期": str(meeting), "报告日期": str(reporting),
                         "利率下限（%）": lo / 100, "利率上限（%）": hi / 100, "概率": p})
        if not seen or abs(total - 1) > 0.002:
            raise ValueError("每场会议每个报告日的概率之和应为 100%")
    if not rows:
        raise ValueError("未找到 CME payload/rateRange 数据")
    result = pd.DataFrame(rows)
    if result.duplicated(["会议日期", "报告日期", "利率下限（%）"]).any():
        raise ValueError("重复的会议、报告日期和利率区间")
    return validate_distribution(result)


def get_forecasts(api_id: str = "", password: str = "", token: str = "", meetings: str = "", reporting: str = "") -> dict:
    if not token:
        if not api_id or not password:
            raise ValueError("请在本地配置 CME OAuth API ID 和密码，或访问令牌")
        auth = requests.post("https://auth.cmegroup.com/as/token.oauth2", auth=(api_id, password),
                             data={"grant_type": "client_credentials"}, timeout=20)
        auth.raise_for_status()
        token = auth.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "CME-Application-Name": "MacroDesk",
               "CME-Application-Vendor": "Personal", "CME-Application-Version": "0.1.0",
               "CME-Request-ID": str(uuid.uuid4()), "User-Agent": "MacroDesk/0.1.0"}
    params = {}
    if meetings:
        params["meetingDt"] = meetings
    if reporting:
        params["reportingDt"] = reporting
    response = requests.get(BASE + "/forecasts", params=params, headers=headers, timeout=25)
    response.raise_for_status()
    result = response.json()
    if result.get("metadata", {}).get("totalPages", 1) > 1:
        raise ValueError("响应被分页，请缩小会议/报告日期范围，避免使用不完整分布")
    normalize(result)
    return result
