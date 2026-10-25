import streamlit as st

st.set_page_config(
    page_title="Sustainable Energy Digital Twin",
    page_icon="⚡",
    layout="wide",
)

# Clean, minimal custom CSS targeting block-container and metrics
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
        padding-left: 2.5rem;
        padding-right: 2.5rem;
        max-width: 1200px;
    }
    div[data-testid="stMetric"] {
        background-color: rgba(128, 128, 128, 0.05);
        border: 1px solid rgba(128, 128, 128, 0.15);
        padding: 1rem 1.25rem;
        border-radius: 0.5rem;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⚡ Sustainable Energy Digital Twin")

st.metric(
    label="Total Consumption",
    value="0.00 kWh",
)
