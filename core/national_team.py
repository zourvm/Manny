"""Adapted from Xiaoyuan-Liu/national-team-position (MIT).

Changes: keep individual ETFs, don't fill unknowns with zero, preserve date and
coverage, never call shares positions or equate cross-fund shares with money.
"""
from __future__ import annotations

import re
import pandas as pd

CODE_GROUPS = {
    "上证50": ["510050", "510710", "510850", "510800"],
    "沪深300": ["510300", "510310", "510330", "510350", "510360", "510380", "515330", "510390"],
    "中证500": ["510500", "510510", "512500", "515550"],
    "中证1000": ["512100", "560010"],
    "科创50": ["588000", "588080", "588090", "588050"],
}
GROUPS = ["沪深300", "上证50", "中证500", "中证1000", "中证A500", "科创50"]


def group_shares(df: pd.DataFrame, actual_date: str) -> pd.DataFrame:
    required = {"基金代码", "基金简称", "基金份额"}
    if not required.issubset(df.columns):
        raise ValueError("ETF 源数据字段发生变化")
    rows = []
    for _, row in df.iterrows():
        code = str(row["基金代码"]).split(".")[0].zfill(6)
        name = str(row["基金简称"])
        group = next((g for g, codes in CODE_GROUPS.items() if code in codes), None)
        # Exclude enhanced/leveraged products from broad-index name matching.
        if not group and "A500" in name.upper() and not re.search("增强|杠杆|反向", name):
            group = "中证A500"
        if not group:
            continue
        value = pd.to_numeric(row["基金份额"], errors="coerce")
        if pd.isna(value) or value < 0:
            continue
        rows.append({"日期": actual_date, "宽基": group, "基金代码": code,
                     "基金简称": name, "基金份额（份）": float(value)})
    result = pd.DataFrame(rows)
    if result.empty or result["基金代码"].duplicated().any():
        raise ValueError("ETF 数据为空或存在重复基金代码")
    return result


def matched_changes(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Only compare the same ETFs at both endpoints; no silent universe changes."""
    left = df[df["日期"] == start]
    right = df[df["日期"] == end]
    joined = left.merge(right, on=["基金代码", "宽基"], suffixes=("_期初", "_期末"), validate="one_to_one")
    if joined.empty:
        return joined
    joined["份额变化（亿份）"] = (joined["基金份额（份）_期末"] - joined["基金份额（份）_期初"]) / 1e8
    joined["份额变化率（%）"] = (joined["基金份额（份）_期末"] / joined["基金份额（份）_期初"].replace(0, float("nan")) - 1) * 100
    return joined


def broad_comparison(df: pd.DataFrame) -> pd.DataFrame:
    """Fixed fund basket per index over the same observed dates; no investor attribution."""
    columns = ["日期", "上期日期", "宽基", "可比基金数", "归一份额", "区间份额变化率（%）", "间隔自然日"]
    if df.empty:
        return pd.DataFrame(columns=columns)
    if df.duplicated(["日期", "基金代码"]).any():
        raise ValueError("同一日期、基金存在重复记录，无法比较")
    dates = sorted(df["日期"].unique())
    rows = []
    for group in GROUPS:
        part = df[df["宽基"] == group]
        universes = [set(part.loc[part["日期"] == day, "基金代码"]) for day in dates]
        stable = set.intersection(*universes)
        baseline = previous = None
        for i, day in enumerate(dates):
            sample = part[(part["日期"] == day) & part["基金代码"].isin(stable)]
            value = sample["基金份额（份）"].sum(min_count=len(stable)) if stable else float("nan")
            normalized = value / baseline * 100 if baseline is not None and baseline > 0 else float("nan")
            if baseline is None and pd.notna(value):
                baseline = value
                normalized = 100.0 if value > 0 else float("nan")
            change = (value / previous - 1) * 100 if previous is not None and previous > 0 else float("nan")
            rows.append({"日期": day, "上期日期": dates[i-1] if i else None, "宽基": group, "可比基金数": len(stable),
                "归一份额": normalized, "区间份额变化率（%）": change,
                "间隔自然日": (pd.Timestamp(day)-pd.Timestamp(dates[i-1])).days if i else None})
            previous = value
    return pd.DataFrame(rows, columns=columns)
