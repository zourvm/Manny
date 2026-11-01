"""Selected helpers from thincat75/fund-rotation-analyst (MIT).
Pinned ae560876ffce4db926b508a3fe5330b1a3531929. No proxy or scoring enabled.
Only the client dispatch in safe_call is removed; normal public AKShare path retained.
"""
from __future__ import annotations
import datetime as dt
import math
import re
from typing import Any, Callable


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return None if math.isnan(number) else number
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "--", "nan", "None", "<NA>"}:
        return None
    multiplier = 1.0
    if "亿" in text:
        multiplier = 100_000_000.0
    elif "万" in text:
        multiplier = 10_000.0
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group(0)) * multiplier if match else None

def clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

def df_to_records(df: Any, limit: int | None = None) -> list[dict[str, Any]]:
    if df is None or getattr(df, "empty", False):
        return []
    if limit is not None:
        df = df.head(limit)
    return [{str(key): clean_value(value) for key, value in row.items()} for row in df.to_dict(orient="records")]

def safe_call(
    warnings: list[str],
    label: str,
    func: Callable[..., Any] | None,
    variants: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    if func is None:
        warnings.append(f"{label}: AkShare function not found")
        return []
    errors = []
    for kwargs in variants:
        try:
            df = func(**kwargs)
            records = df_to_records(df, limit=limit)
            if records:
                return records
            errors.append(f"{kwargs}: empty")
        except Exception as exc:  # pragma: no cover - endpoint dependent
            errors.append(f"{kwargs}: {exc}")
    warnings.append(f"{label}: all variants failed or empty ({'; '.join(errors[:3])})")
    return []

def date_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)[:10].replace("/", "-")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError:
        return None

def _amount(value: Any, multiplier: float) -> float | None:
    number = parse_number(value)
    return number * multiplier if number is not None else None

def normalize_margin_rows(
    rows: list[dict[str, Any]],
    exchange: str,
    provider: str,
    *,
    unit: str = "元",
    cutoff: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize Tushare or AkShare aggregate margin rows to yuan."""
    multiplier = 100_000_000 if unit == "亿元" else 1
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = date_text(row.get("trade_date") or row.get("日期"))
        if not day or (cutoff and day > cutoff):
            continue
        financing = _amount(row.get("rzye", row.get("融资余额")), multiplier)
        financing_buy = _amount(row.get("rzmre", row.get("融资买入额")), multiplier)
        financing_repay = _amount(row.get("rzche", row.get("融资偿还额")), multiplier)
        lending = _amount(row.get("rqye", row.get("融券余额")), multiplier)
        total = _amount(row.get("rzrqye", row.get("融资融券余额")), multiplier)
        if total is None and financing is not None and lending is not None:
            total = financing + lending
        if financing is None and total is None:
            continue
        output[day] = {
            "trade_date": day,
            "exchange": exchange,
            "financing_balance": financing,
            "financing_buy": financing_buy,
            "financing_repay": financing_repay,
            "lending_balance": lending,
            "margin_balance": total,
            "unit": "元",
            "provider": provider,
        }
    return [output[day] for day in sorted(output)]

def eastmoney_etf_spot_candidates(codes: list[str]) -> list[dict[str, Any]]:
    """Fetch the ETF quote universe once and retain only configured candidates."""
    import requests

    response = requests.get(
        "https://push2delay.eastmoney.com/api/qt/clist/get",
        params={
            "pn": "1", "pz": "5000", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281", "fltt": "2", "invt": "2",
            "fid": "f12", "fs": "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827",
            "fields": "f2,f3,f6,f12,f14,f124,f297,f402,f441",
        },
        timeout=12,
    )
    response.raise_for_status()
    rows = ((response.json().get("data") or {}).get("diff") or [])
    wanted = {str(code).zfill(6) for code in codes}
    output = []
    for row in rows:
        code = str(row.get("f12") or "").zfill(6)
        if code not in wanted:
            continue
        timestamp = parse_number(row.get("f124"))
        updated = dt.datetime.fromtimestamp(timestamp).isoformat(timespec="seconds") if timestamp else None
        output.append({
            "代码": code,
            "名称": row.get("f14"),
            "最新价": parse_number(row.get("f2")),
            "IOPV实时估值": parse_number(row.get("f441")),
            "基金折价率": parse_number(row.get("f402")),
            "涨跌幅": parse_number(row.get("f3")),
            "成交额": parse_number(row.get("f6")),
            "数据日期": row.get("f297"),
            "更新时间": updated,
        })
    return output
