import streamlit as st
from pathlib import Path
from components.gds_service_toolkit_ui import apply_gds_styles, render_phase_banner, render_caption


st.set_page_config(layout="wide")
apply_gds_styles()
render_phase_banner("Prototype", "This is an AI Triage PoC")
render_caption("Defra, Natural England internal tooling")

app_root = Path(__file__).resolve().parent
defra_logo = app_root / "assets" / "defra_logo.svg"
ne_logo = app_root / "assets" / "natural_england_logo.svg"

# 1 - Logo top left
st.logo(str(defra_logo), size="large")

# 2 - Images (logos) in the main content area at the top
left, right = st.columns([3, 1])
with left:
    if defra_logo.exists():
        st.image(str(defra_logo), width=160)
with right:
    if ne_logo.exists():
        st.image(str(ne_logo), width=100)

# 3 - Images (logos) in the sidebar
with st.sidebar:
    st.image(str(defra_logo), width=110)
    st.image(str(ne_logo), width=110)