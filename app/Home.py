"""Medicaid Drug Savings Explorer: Streamlit entry point.

Run from the project root:  .venv/bin/streamlit run app/Home.py
Defines the side-menu navigation; each page lives in app/views/.
"""
from pathlib import Path

import streamlit as st

from lib import data, ui

data.clear_if_data_changed()
st.set_page_config(
    page_title="Medicaid Drug Savings Explorer",
    page_icon=":material/medication:",
    layout="wide",
    initial_sidebar_state="auto",   # open on desktop, collapsed on phones
)
ui.apply_style()
st.logo(str(Path(__file__).parent / "static" / ("logo_light.svg" if ui.mode() == "light" else "logo.svg")),
        size="large")

nav = st.navigation(
    {
        "Main menu": [
            st.Page("views/home.py", title="Overview", icon=":material/space_dashboard:", default=True),
            st.Page("views/rankings.py", title="Rankings", icon=":material/bar_chart:", url_path="rankings"),
            st.Page("views/savings.py", title="Savings Opportunity", icon=":material/savings:", url_path="savings"),
            st.Page("views/explore.py", title="Explore Alternatives", icon=":material/search:", url_path="explore"),
            st.Page("views/rebates.py", title="Rebates", icon=":material/receipt_long:", url_path="rebates"),
            st.Page("views/categories.py", title="Drug Categories", icon=":material/category:",
                    url_path="categories"),
        ],
    },
    position="sidebar",
)

with st.sidebar:
    ui.home_link("https://nolansherwood.com", "nolansherwood.com")
    st.toggle("Dark mode", key="theme_dark")
    ui.sync_theme()
    st.caption(
        "Built from CMS State Drug Utilization Data (2025) and FDA records. "
        "Spending is before manufacturer rebates."
    )

nav.run()
