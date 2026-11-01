from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import altair as alt
import pandas as pd
import streamlit as st

from core import store
from core.data import CATALOG, refresh


@st.cache_data(ttl=60, max_entries=32, show_spinner=False)
def _cached(name: str, scope: str) -> dict | None:
    return store.load(name)


def cached(name: str) -> dict | None:
    return _cached(name,str(store.data_dir()))


cached.clear = _cached.clear


def frame(name: str) -> pd.DataFrame:
    p = cached(name)
    return pd.DataFrame(p["records"]) if p else pd.DataFrame()


def filtered(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "日期" not in df:
        return df
    years = st.session_state.get("years", "近 3 年")
    if years == "全部":
        return df
    count = {"近 1 年": 1, "近 3 年": 3, "近 5 年": 5}[years]
    threshold = pd.Timestamp(datetime.now(ZoneInfo("Asia/Shanghai")).date()) - pd.DateOffset(years=count)
    return df[pd.to_datetime(df["日期"]) >= threshold]


def stamp(name: str) -> None:
    from core.freshness import show
    p = cached(name)
    show(name,p)
    if not p:
        st.info("暂无成功采集的数据。点击刷新后开始取数；接口不可用时可在“数据与设置”查看原因。", icon=":material/cloud_off:")
        return
    dates = [str(r.get("日期", r.get("报告日期", ""))) for r in p["records"]]
    latest = max(dates)
    st.caption(f"统计期至 {latest}　｜　本地采集于 {p['fetched_at'][:19].replace('T', ' ')}（北京时间）")
    if p.get("attempt") and p["attempt"][1]:
        st.warning("最近刷新未完整成功，当前保留已有数据。" + p["attempt"][1], icon=":material/history:")


def dataset_header(key: str, title: str, args: dict | None = None) -> tuple[pd.DataFrame, str]:
    name = key if key != "house" else f"house:{(args or {}).get('city1', '北京')}:{(args or {}).get('city2', '上海')}"
    a, b = st.columns([4, 2], vertical_alignment="bottom")
    a.subheader(title)
    if b.button("刷新数据", icon=":material/refresh:", key=f"refresh-{name}"):
        with st.spinner("正在读取公开数据，单项最多等待 40 秒…"):
            good = refresh(key, args)
        cached.clear()
        if not good:
            st.warning("本次刷新失败。请在“数据与设置”查看详情。")
    stamp(name)
    return frame(name), name


def metric(df: pd.DataFrame, col: str, label: str, suffix: str = "", help_text: str = "", dataset_name: str | None = None) -> None:
    valid = df.dropna(subset=[col]).sort_values("日期") if not df.empty and col in df else pd.DataFrame()
    if valid.empty:
        st.metric(label, "—", border=True, help=help_text or "尚无可用数据")
        st.caption("等待有效数据")
        return
    last = valid.iloc[-1]
    delta = float(last[col] - valid.iloc[-2][col]) if len(valid) > 1 else None
    st.metric(label, f"{last[col]:,.2f}{suffix}", delta=round(delta, 2) if delta is not None else None,
              delta_color="off", delta_description="较上一条有效观测", border=True, help=help_text or None)
    st.caption(f"统计期 {last['日期']}")
    if dataset_name:
        from core.freshness import describe
        status=describe(dataset_name,cached(dataset_name))
        st.caption("最近获取："+status["最近成功获取（北京时间）"])
        st.caption("预计下次："+status["预计下次更新"])
        st.caption(status["时效状态"])


def line(df: pd.DataFrame, columns: list[str], unit: str, title: str, baseline: float | None = None, height: int = 280) -> None:
    df = filtered(df)
    cols = [c for c in columns if c in df.columns]
    if df.empty or not cols:
        st.info("所选区间暂无数据，请扩大时间范围或刷新。")
        return
    long = df[["日期", *cols]].melt("日期", var_name="指标", value_name="数值")
    long["日期"] = pd.to_datetime(long["日期"])
    chart = alt.Chart(long).mark_line(point=True, strokeWidth=2).encode(
        x=alt.X("日期:T", title=None), y=alt.Y("数值:Q", title=unit, scale=alt.Scale(zero=False)),
        color=alt.Color("指标:N", legend=alt.Legend(orient="bottom", title=None)),
        tooltip=[alt.Tooltip("日期:T", format="%Y-%m-%d"), "指标:N", alt.Tooltip("数值:Q", format=",.2f")],
    )
    if baseline is not None:
        rule = alt.Chart(pd.DataFrame({"基准": [baseline]})).mark_rule(strokeDash=[5, 5], color="#8F9EAA").encode(y="基准:Q")
        chart = chart + rule
    st.altair_chart(chart.properties(height=height).interactive(), alt=title)


def raw_data(df: pd.DataFrame, name: str) -> None:
    with st.expander("查看与导出原始数据", expanded=False):
        if df.empty:
            st.caption("暂无可导出数据")
        else:
            st.dataframe(df.sort_values("日期", ascending=False) if "日期" in df else df,
                         hide_index=True, alt=name + "原始数据")
            st.download_button("下载 CSV", df.to_csv(index=False).encode("utf-8-sig"), name + ".csv", "text/csv", key="download-" + name)


def source_note(key: str) -> None:
    spec = CATALOG[key]
    st.caption(f"来源：AKShare → [原始页面]({spec['source']})　｜　{spec['frequency']}　｜　{spec['unit']}")
    st.caption(spec["note"])
