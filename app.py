import pandas as pd
import streamlit as st

from core.actions import generate_candidate_actions
from core.energy import calculate_baselines, calculate_carbon, load_and_clean_data
from core.recommender import get_ai_recommendation
from core.twin import TwinStore

st.set_page_config(
    page_title="Sustainable Energy Digital Twin",
    page_icon="⚡",
    layout="wide",
)

# Clean minimal CSS block targeting .block-container, cards, and metrics
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
    .priority-badge {
        display: inline-block;
        padding: 0.2rem 0.6rem;
        border-radius: 0.375rem;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }
    .priority-high { background-color: rgba(239, 68, 68, 0.15); color: #dc2626; border: 1px solid rgba(239, 68, 68, 0.3); }
    .priority-medium { background-color: rgba(245, 158, 11, 0.15); color: #d97706; border: 1px solid rgba(245, 158, 11, 0.3); }
    .priority-low { background-color: rgba(16, 185, 129, 0.15); color: #059669; border: 1px solid rgba(16, 185, 129, 0.3); }
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize Session State
if "ai_recommendations" not in st.session_state:
    st.session_state.ai_recommendations = {}

if "approved_actions" not in st.session_state:
    st.session_state.approved_actions = {}


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
tabs = st.tabs(["📊 Asset Overview", "🤖 AI Recommendations"])

# TAB 1: Asset Overview
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


# TAB 2: AI Recommendations
with tabs[1]:
    st.subheader("AI Safety & Optimization Recommendations")

    # Filter assets triggering waste alert
    alert_assets = [s for s in display_states.values() if s.waste_alert]

    if not alert_assets:
        st.info("No active waste alerts detected for the current selection. All assets are operating within expected efficiency thresholds.")
    else:
        for asset in alert_assets:
            with st.expander(f"⚠️ Waste Alert: {asset.asset_id}", expanded=True):
                # Asset metadata row
                m_col1, m_col2, m_col3 = st.columns(3)
                with m_col1:
                    st.metric("Asset ID", asset.asset_id)
                with m_col2:
                    st.metric("Power vs Baseline", f"{asset.current_power_w:.1f} W", delta=f"{asset.current_power_w - asset.baseline_power_w:+.1f} W vs Baseline")
                with m_col3:
                    st.metric("Current Occupancy", f"{asset.occupancy_count} occupants")

                st.write("")

                # Action button to generate AI recommendation
                btn_col, _ = st.columns([2, 5])
                with btn_col:
                    get_rec_clicked = st.button(
                        "Get AI Recommendation",
                        key=f"btn_get_rec_{asset.asset_id}",
                        type="primary",
                    )

                if get_rec_clicked:
                    candidates = [
                        a.model_dump()
                        for a in generate_candidate_actions(
                            asset_id=asset.asset_id,
                            excess_kwh=asset.excess_kwh,
                            carbon_factor=carbon_factor,
                        )
                    ]
                    twin_dict = {
                        "asset_id": asset.asset_id,
                        "state": asset.state,
                        "current_power_w": asset.current_power_w,
                        "baseline_power_w": asset.baseline_power_w,
                        "occupancy_count": asset.occupancy_count,
                        "excess_kwh": asset.excess_kwh,
                        "co2e_kg": asset.co2e_kg,
                        "waste_alert": asset.waste_alert,
                    }
                    recommendation = get_ai_recommendation(
                        twin_state=twin_dict,
                        candidate_actions=candidates,
                        carbon_factor=carbon_factor,
                    )
                    st.session_state.ai_recommendations[asset.asset_id] = recommendation

                # Display Recommendation Details if available
                if asset.asset_id in st.session_state.ai_recommendations:
                    rec = st.session_state.ai_recommendations[asset.asset_id]
                    st.divider()

                    # Result Header & Priority Badge
                    p_class = f"priority-{rec.priority.lower()}"
                    r_head_col1, r_head_col2 = st.columns([4, 1])
                    with r_head_col1:
                        st.markdown(f"#### Recommended Action: **{rec.decision}**")
                    with r_head_col2:
                        st.markdown(
                            f'<div style="text-align: right;"><span class="priority-badge {p_class}">{rec.priority} PRIORITY</span></div>',
                            unsafe_allow_html=True,
                        )

                    # Savings Metrics
                    sav_col1, sav_col2 = st.columns(2)
                    with sav_col1:
                        st.metric("Projected Energy Saving", f"{rec.projected_saving_kwh:.2f} kWh")
                    with sav_col2:
                        st.metric("Projected CO2e Reduction", f"{rec.projected_co2e_kg:.2f} kg")

                    # Rationale & Operator Instructions
                    st.info(f"**Rationale:** {rec.rationale}")
                    st.markdown(f"**Operator Instruction:** {rec.operator_instruction}")

                    # Approval Workflow
                    is_approved = asset.asset_id in st.session_state.approved_actions
                    if is_approved:
                        st.success(f"✅ Recommendation Status: **Approved** ({st.session_state.approved_actions[asset.asset_id]['decision']})")
                    else:
                        appr_col, _ = st.columns([2, 5])
                        with appr_col:
                            if st.button("Approve Recommendation", key=f"btn_approve_{asset.asset_id}"):
                                st.session_state.approved_actions[asset.asset_id] = {
                                    "action_id": rec.selected_action_id,
                                    "decision": rec.decision,
                                    "projected_saving_kwh": rec.projected_saving_kwh,
                                    "projected_co2e_kg": rec.projected_co2e_kg,
                                    "status": "Approved",
                                }
                                st.success("✅ Recommendation marked as Approved!")
                                st.rerun()
