"""Record publicly viewed FedWatch probabilities without API credentials."""
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import pandas as pd
import streamlit as st
from core import cme
from core.ui import cached


def ready(frame, source, source_time, method):
    totals=frame.groupby(["会议日期","报告日期"])["概率"].sum().mul(100).reset_index(name="概率合计（%）")
    st.dataframe(totals,hide_index=True,alt="按会议和观测日期检查概率合计")
    st.caption("允许最多0.2个百分点的舍入误差，不强制归一化。同一会议、报告日再保存会替换当天完整分布；其他日期保留。")
    if st.button("核对后保存概率快照",type="primary",key="fed-save-"+method):
        try:
            cme.save_snapshot(frame,source,source_time,method)
            cached.clear()
            st.success("已保存，下方图表已更新；其他报告日期仍保留。")
        except (ValueError,TypeError) as ex:
            st.error("未保存："+str(ex))


def render_entry(backup=False):
    with st.expander("备用：手动录入 / 文件导入" if backup else "录入网页概率 / 导入表格",expanded=not backup and not bool(cached("cme"))):
        st.markdown("1. 在FedWatch选择 **会议日期**，查看Current中各目标利率区间的概率。\n2. 填写网页对应的 **报告/观测日期**，记录更新时间及时区（如有）。\n3. 录全区间并核对合计，保存后查看下方图表。")
        st.caption("利率填百分比：325–350基点对应3.25–3.50%；看到68.5%就填68.5。单个总的‘降息概率’不足以还原完整分布。")
        method=st.segmented_control("录入方式",["网页对照录入","导入 CSV / Excel","兼容原始 JSON"],default="网页对照录入",required=True,key="fed-entry-mode")
        if method=="网页对照录入":
            a,b=st.columns(2)
            meeting=a.date_input("录入：FOMC会议日期",value=None,key="fed-entry-meeting")
            reporting=b.date_input("录入：网页报告 / 观测日期",value=None,max_value=datetime.now(ZoneInfo("Asia/Shanghai")).date(),key="fed-entry-reporting")
            source_time=st.text_input("网页更新时间与时区（按原文填写，可留空）",placeholder="未显示时留空，不以本地时间代替",key="fed-entry-time")
            source=st.text_input("来源网页",value=cme.WEB,key="fed-entry-source")
            columns=["利率下限（%）","利率上限（%）","概率（%）"]
            edited=st.data_editor(pd.DataFrame({c:pd.Series([None],dtype="float64") for c in columns}),num_rows="dynamic",hide_index=True,key="fed-manual-editor",
                column_config={columns[0]:st.column_config.NumberColumn(min_value=-10,max_value=30,step=.25,format="%.2f"),
                    columns[1]:st.column_config.NumberColumn(min_value=-10,max_value=30,step=.25,format="%.2f"),
                    columns[2]:st.column_config.NumberColumn(min_value=0,max_value=100,step=.1,format="%.2f")},
                alt="填写各利率区间和对应百分比概率，支持增加行及从表格粘贴")
            if meeting and reporting and edited.notna().any().any():
                try:ready(cme.normalize_manual(edited,meeting,reporting),source,source_time,method)
                except (ValueError,TypeError) as ex:st.warning(str(ex))
            else:st.caption("填好会议日、观测日期和完整概率后即可保存，不使用演示值补图。")
        elif method=="导入 CSV / Excel":
            st.caption("按模板整理公开网页数据，可一次导入多个会议和日期。CME下载文件若表头不同，须先转换为此模板。")
            st.download_button("下载空白概率 CSV 模板",pd.DataFrame(columns=cme.INPUT_COLUMNS+["来源时间（原文）"]).to_csv(index=False).encode("utf-8-sig"),"FedWatch概率模板.csv","text/csv")
            file=st.file_uploader("上传标准概率 CSV / Excel",type=["csv","xlsx"],key="fed-table")
            source=st.text_input("本批数据来源网页",value=cme.WEB,key="fed-table-source")
            if file:
                try:
                    raw=pd.read_csv(file) if file.name.lower().endswith(".csv") else pd.read_excel(file)
                    imported=cme.normalize_manual(raw)
                    st.dataframe(imported,hide_index=True,alt="待保存的标准概率表")
                    ready(imported,source,"",method)
                except Exception as ex:st.error("表格未通过校验："+str(ex))
        else:
            st.caption("保留既有JSON文件导入，不需要调用接口。原始JSON的利率为基点、概率为0–1，与网页模板单位不同。")
            file=st.file_uploader("选择 CME 原始 JSON",type=["json"],key="cme-json")
            if file:
                try:
                    imported=cme.normalize(json.load(file))
                    st.dataframe(imported,hide_index=True,alt="待保存的原始JSON概率分布")
                    ready(imported,cme.WEB,"","原始JSON导入·来源待核对")
                except Exception as ex:st.error("导入失败："+str(ex))
