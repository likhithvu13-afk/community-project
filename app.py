import pandas as pd
import streamlit as st

from core.energy import calculate_baselines, calculate_carbon, load_and_clean_data
from core.twin import TwinStore

st.set_page_config(
    page_title="Sustainable Energy Digital Twin",
    page_icon="⚡",
    layout="wide",
)

# Clean minimal CSS block targeting .block-container and metrics
st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.8rem;
        padding-bottom: 2rem;
        padding-left: 2.5rem;
        padding-right: 2.5rem;
        max-width: 1300px;
    }
    div[data-testid="stMetric"] {
        background-color: rgba(128, 128, 128, 0.05);
        border: 1px solid rgba(128, 128, 128, 0.15);
        padding: 0.9rem 1.1rem;
        border-radius: 0.5rem;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
    }
    div[data-testid="stMetric"] label {
        font-size: 0.82rem;
        color: #64748b;
        font-weight: 500;
    }
    div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
        font-size: 1.55rem;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data
def get_sensor_data() -> pd.DataFrame:
    """Load and clean telemetry data with caching."""
    return load_and_clean_data("data/sample_sensors.csv")


# Data loading & Digital Twin evaluation
df = get_sensor_data()
baselines = calculate_baselines(df)

# Sidebar
st.sidebar.title("⚙️ Settings")
carbon_factor = st.sidebar.number_input(
    "Carbon Factor (kg CO2e/kWh)",
    min_value=0.00,
    max_value=2.00,
    value=0.42,
    step=0.01,
    format="%.2f",
)

available_assets = sorted(df["asset_id"].unique().tolist())
asset_filter = st.sidebar.selectbox(
    "Asset Filter",
    options=["All Assets"] + available_assets,
    index=0,
)

# Initialize and evaluate TwinStore
store = TwinStore(baselines=baselines, carbon_factor=carbon_factor)
twin_states = store.evaluate_dataframe(df)

# Determine active slice based on filter
if asset_filter == "All Assets":
    active_df = df
    display_states = twin_states
else:
    active_df = df[df["asset_id"] == asset_filter]
    display_states = {asset_filter: twin_states[asset_filter]}

# Top Row KPI Calculations
total_consumption = float(active_df["energy_kwh"].sum())

# Compute excess consumption across active dataset
excess_kwh_list = []
for _, row in active_df.iterrows():
    base = baselines.get(row["asset_id"], 0.0)
    if row["occupancy_count"] == 0 and row["power_w"] > 0.40 * base:
        excess_w = max(0.0, row["power_w"] - 0.10 * base)
        excess_kwh_list.append((excess_w / 1000.0) * (5.0 / 60.0))
    else:
        excess_kwh_list.append(0.0)

total_excess = float(sum(excess_kwh_list))
projected_co2 = calculate_carbon(total_consumption, carbon_factor)
active_assets_count = sum(1 for s in display_states.values() if s.state == "ACTIVE")
waste_alerts_count = sum(1 for s in display_states.values() if s.waste_alert)

# Header Section
st.title("⚡ Sustainable Energy Digital Twin")
st.caption("Real-time telemetry, asset simulation states, and energy efficiency analytics")

# Header / KPI Section (Top row using st.columns(5))
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Total Consumption (kWh)", f"{total_consumption:.2f}")
with col2:
    st.metric("Total Excess Consumption (kWh)", f"{total_excess:.2f}")
with col3:
    st.metric("Projected CO2e Emission (kg)", f"{projected_co2:.2f}")
with col4:
    st.metric("Active Assets Count", f"{active_assets_count}")
with col5:
    st.metric("Energy Waste Alerts Count", f"{waste_alerts_count}")

st.write("")

# Main View (Structured Tabs)
tabs = st.tabs(["📊 Asset Overview"])

with tabs[0]:
    st.subheader("Asset Digital Twin Status")

    # Construct clean overview table
    table_data = []
    for state in display_states.values():
        table_data.append({
            "Asset ID": state.asset_id,
            "State": state.state,
            "Power (W)": round(state.current_power_w, 2),
            "Baseline (W)": round(state.baseline_power_w, 2),
            "Excess kWh": round(state.excess_kwh, 2),
            "CO2e (kg)": round(state.co2e_kg, 2),
            "Waste Alert Status": "⚠️ Waste Alert" if state.waste_alert else "Normal",
        })

    overview_df = pd.DataFrame(table_data)

    st.dataframe(
        overview_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Asset ID": st.column_config.TextColumn("Asset ID"),
            "State": st.column_config.TextColumn("State"),
            "Power (W)": st.column_config.NumberColumn("Power (W)", format="%.2f"),
            "Baseline (W)": st.column_config.NumberColumn("Baseline (W)", format="%.2f"),
            "Excess kWh": st.column_config.NumberColumn("Excess kWh", format="%.2f"),
            "CO2e (kg)": st.column_config.NumberColumn("CO2e (kg)", format="%.2f"),
            "Waste Alert Status": st.column_config.TextColumn("Waste Alert Status"),
        },
    )

    st.write("")
    st.subheader(f"Telemetry Analysis: {asset_filter}")

    # Telemetry Line Chart for selected asset's power consumption over time
    if asset_filter == "All Assets":
        chart_df = df.pivot(index="timestamp", columns="asset_id", values="power_w")
    else:
        chart_df = df[df["asset_id"] == asset_filter].set_index("timestamp")[["power_w"]]
        chart_df.columns = [f"{asset_filter} Power (W)"]

    st.line_chart(chart_df, height=360)
