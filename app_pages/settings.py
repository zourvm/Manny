import pandas as pd
import streamlit as st
from core import store
from core.freshness import inventory,CAL
from core.data import CATALOG
from core.ui import cached

st.title("数据与设置")
st.caption("核对覆盖、统计口径和刷新状态。数据保存在本机，可离线查看成功快照。")
st.subheader("全部数据 · 获取日期与预计更新")
st.caption("最近成功获取不是源站发布日期。预计日期按数据截至期推算；超期不会悄悄顺延到未来。历史对比期单列，不因此认定旧期数据失效。")
freshness_table=inventory()
selected_dataset=st.selectbox("查看单项数据时效",freshness_table["数据集"].tolist(),key="freshness-dataset")
selected=freshness_table.loc[freshness_table["数据集"]==selected_dataset].iloc[0]
with st.container(border=True):
    st.markdown(f"**数据截至 / 报告期：** {selected['数据截至 / 报告期']}")
    st.markdown(f"**最近成功获取（北京时间）：** {selected['最近成功获取（北京时间）']}")
    st.markdown(f"**预计下次更新：** {selected['预计下次更新']}")
    st.markdown(f"**时效判断：** {selected['时效状态']}")
    st.caption(selected["预计依据"]+"。"+selected["本地获取方式"])
    if selected["依据链接"]:st.markdown(f"[查看预计更新依据]({selected['依据链接']})")
st.caption("完整对照表可横向滚动，或下载到Excel查看全部列。")
st.dataframe(freshness_table,hide_index=True,column_config={"依据链接":st.column_config.LinkColumn("预计更新依据")},alt="所有数据集的截至日期、最近成功获取、预计更新及判断依据")
st.download_button("下载全部数据时效表 CSV",freshness_table.to_csv(index=False).encode("utf-8-sig"),"全部数据时效与预计更新.csv","text/csv")
with st.expander("预计日期如何确定"):
    st.markdown("官方日程标为计划，报告期限标为披露窗口，行情与交易日推算标为估计。进出口导入表、历史ETF具名样本及不定期公告没有固定更新日期，明确保留未知。")
    st.markdown(f"[国家统计局发布日程]({CAL['nbs_source']}) · [公募定报规则]({CAL['fund_rule']}) · [上市公司定报规则]({CAL['company_rule']}) · [已核验交易所休市日]({CAL['exchange_source']})")
    st.caption("本机日程核验："+CAL["checked_on"]+"。超出2026年范围仅给估计窗口；聚合接口可能晚于官方发布。时效判断不能保证样本完整或原文已核验。")
st.subheader("当前覆盖")
rows = []
for key, spec in CATALOG.items():
    name = "house:北京:上海" if key == "house" else key
    payload = cached(name)
    rows.append({"数据集": spec["title"], "频率": spec["frequency"], "单位": spec["unit"],
                 "接口": spec["function"], "最新统计期": max(r["日期"] for r in payload["records"]) if payload else "尚未采集", "来源": spec["source"]})
st.dataframe(rows, hide_index=True, alt="已接入数据集的来源、频率、单位和统计期")
st.caption("进出口行业细分：CSV 导入；地产销售/投资/开工/竣工：待接入；宽基 ETF：上交所代表样本；CME：自动读取公开网页并填入，手工/文件导入为备用，无需API。")
st.caption("国家队公开持仓：已接入具名股票记录，社保与养老金分别识别；ETF 为原文核验的历史样本，详见专页。")
st.page_link("app_pages/state.py",label="国家队公开持仓 · 图表与来源",icon=":material/account_balance:")
st.caption("机构与杠杆观察：按基金代码读取公募重大买卖、持仓；保险与私募具名账户可采集多期对比；沪深两融同日汇总；ETF 延迟行情单独核验。均保留数据范围和来源审计。")
st.page_link("app_pages/funds.py",label="机构与杠杆观察 · 适配器与图表",icon=":material/finance_mode:")

st.subheader("采集日志")
if st.button("重新读取本地状态", icon=":material/refresh:"):
    cached.clear()
logs = store.status()
if logs:
    st.dataframe(logs, hide_index=True, alt="各数据源最近一次采集结果与失败原因")
else:
    st.info("尚无采集记录")

st.subheader("数据口径")
st.markdown("""
- **统计期与采集时间**分开显示；成功访问接口不代表数据已更新至本月。
- **ETF**保留逐只份额与实际日期；不把未采集基金补成零，不把份额当资金金额。
- **行业进出口**按分类、币种、单位、当月/累计和来源分别筛选，避免重叠商品分类相加。
- **房价指数**同比/环比减去 100 后展示为涨跌幅；不计算定基指数的所谓当期涨幅。
- **CME**概率校验为 0–1，逐会议逐报告日合计约为 100%；空值不视为零。
""")
st.caption(f"缓存目录：{store.DATA_DIR}")
st.caption("美联储页打开或操作时自动检查，15分钟限频；其他模块手动获取。预计源站更新日期不等于已设置本地定时获取，没有后台定时任务或自动交易。")
