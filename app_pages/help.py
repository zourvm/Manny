from pathlib import Path
import streamlit as st

st.title("使用说明")
st.caption("操作步骤、图表读法、数据更新和常见问题，按当前看板功能编写。")
guide = Path(__file__).resolve().parents[1] / "docs" / "使用说明.md"
content = guide.read_text(encoding="utf-8")
st.download_button("下载完整使用说明（Markdown）", content.encode("utf-8-sig"), "观数-使用说明.md", "text/markdown")
sections = content.split("\n## ")
st.markdown(sections[0].split("\n", 2)[2])
for section in sections[1:]:
    title, body = section.split("\n", 1)
    with st.expander(title, expanded=title.startswith("1.")):
        st.markdown(body)
