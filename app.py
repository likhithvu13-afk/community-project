import hashlib
import json
from datetime import datetime
from typing import Dict, List

import pandas as pd
import streamlit as st

from core.actions import generate_candidate_actions
from core.config import (
    DEFAULT_CARBON_FACTOR,
    DEFAULT_TARIFF_INR_PER_KWH,
    EXCESS_BASELINE_FACTOR,
    STATE_ACTIVE_THRESHOLD,
    ZONE_THRESHOLDS,
    derive_zone_thresholds_from_data,
)
from core.energy import calculate_baselines, calculate_carbon, load_and_clean_data
from core.ingest import (
    PROCESSED_COLUMNS,
    build_zone_recommendations,
    classify_records,
    overall_status,
    read_dataset,
    standardize_dataset,
    summarize_zones,
)
from core.input import (
    ZONE_SPECS,
    ZoneInput,
    baselines_from_inputs,
    build_snapshot_df,
    default_zone_inputs,
    inputs_from_rows,
    prefill_from_sample,
)
from core.recommender import generate_sustainability_report, get_ai_recommendation
from core.twin import TwinStore

INTERVAL_MINUTES = 5.0

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
    .report-card {
        background-color: rgba(128, 128, 128, 0.04);
        border: 1px solid rgba(128, 128, 128, 0.18);
        border-radius: 0.65rem;
        padding: 1.5rem 1.8rem;
        margin-top: 1rem;
        line-height: 1.6;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize Session State
if "ai_recommendations" not in st.session_state:
    st.session_state.ai_recommendations = {}

if "approved_actions" not in st.session_state:
    st.session_state.approved_actions = {}

if "sustainability_report" not in st.session_state:
    st.session_state.sustainability_report = None


@st.cache_data
def get_sensor_data() -> pd.DataFrame:
    """Load and clean telemetry data with caching."""
    return load_and_clean_data("data/sample_sensors.csv")


@st.cache_data(show_spinner=False)
def process_uploaded_dataset(filename: str, payload: bytes):
    """Parse and standardise an uploaded dataset (cached by file content)."""
    raw, file_format = read_dataset(filename, payload)
    standardized, report = standardize_dataset(raw, file_format)
    return standardized, report


def zone_display_rows(inputs: List[ZoneInput]) -> pd.DataFrame:
    """Convert validated zone inputs into the editor's fixed 4-row table."""
    return pd.DataFrame(
        [
            {
                "Zone": zone.zone_name,
                "Current Power (W)": float(zone.power_w),
                "Baseline Power (W)": float(zone.baseline_power_w),
                "Occupancy": int(zone.occupancy_count),
                "Area (m²)": float(zone.area_m2),
            }
            for zone in inputs
        ]
    )


def zone_name_for(asset_id: str) -> str:
    """Return the display name for an asset, falling back to its asset ID."""
    spec = ZONE_SPECS.get(asset_id)
    return spec["display_name"] if spec else asset_id


# Sidebar
st.sidebar.title("⚙️ Settings")
data_source = st.sidebar.radio(
    "Data source",
    options=["Sample dataset", "Manual zone input", "Upload dataset"],
    index=0,
)
carbon_factor = st.sidebar.number_input(
    "Carbon Factor (kg CO2e/kWh)",
    min_value=0.00,
    max_value=2.00,
    value=DEFAULT_CARBON_FACTOR,
    step=0.01,
    format="%.2f",
)
tariff = st.sidebar.number_input(
    "Tariff (₹/kWh)",
    min_value=0.0,
    max_value=100.0,
    value=DEFAULT_TARIFF_INR_PER_KWH,
    step=0.5,
    format="%.2f",
)

# Header Section
st.title("⚡ Sustainable Energy Digital Twin")
st.caption("Real-time telemetry, asset simulation states, and energy efficiency analytics")

# Upload dataset: the single user input; everything else is automatic
upload_standardized: pd.DataFrame | None = None
upload_report = None
upload_filename = ""
upload_digest = ""
if data_source == "Upload dataset":
    st.subheader("📤 Upload Dataset")
    st.caption(
        "Upload one CSV, Excel or JSON file. Formats, columns and zones are "
        "detected automatically — no manual configuration is required."
    )
    # Sample file download so users can test with a known-good format
    sample_csv = (
        "timestamp,asset_id,power_w,zone_name,occupancy_count\n"
        "2026-10-25 08:00:00,hvac-unit-01,4500,Main Floor,0\n"
        "2026-10-25 08:05:00,hvac-unit-01,3200,Main Floor,12\n"
        "2026-10-25 08:10:00,lighting-zone-3b,850,Zone 3B,0\n"
        "2026-10-25 08:15:00,lighting-zone-3b,800,Zone 3B,10\n"
        "2026-10-25 08:20:00,computer-cluster-1,1250,IT Room,0\n"
        "2026-10-25 08:25:00,computer-cluster-1,1180,IT Room,8\n"
        "2026-10-25 08:30:00,printer-zone-1,300,Office,0\n"
        "2026-10-25 08:35:00,printer-zone-1,40,Office,2\n"
    )
    st.download_button(
        "⬇️ Download sample CSV (try this first)",
        data=sample_csv.encode("utf-8"),
        file_name="ecotwin_sample_upload.csv",
        mime="text/csv",
        key="btn_download_sample_csv",
        help="Download a sample file with the expected column format, then upload it back to test.",
    )
    st.divider()

    uploaded_file = st.file_uploader(
        "Upload energy data",
        type=["csv", "xlsx", "xls", "xlsm", "json"],
        accept_multiple_files=False,
        key="dataset_uploader",
    )

    if uploaded_file is None:
        st.info(
            "Waiting for a dataset. The file may contain readings for all four "
            "zones or for a subset of them."
        )
        st.stop()

    payload = uploaded_file.getvalue()
    upload_filename = uploaded_file.name
    upload_digest = hashlib.sha256(payload).hexdigest()

    try:
        with st.spinner("Detecting format, mapping columns and classifying zones..."):
            upload_standardized, upload_report = process_uploaded_dataset(
                upload_filename, payload
            )
    except Exception as exc:
        st.error(f"Could not process the uploaded file: {exc}")
        st.stop()

    with st.expander("Processing report", expanded=False):
        st.write(
            f"**File:** `{upload_filename}` · **Format:** {upload_report.file_format} "
            f"· **Rows read:** {upload_report.total_rows} "
            f"· **Valid rows:** {upload_report.valid_rows}"
        )
        st.write(
            "**Detected columns:** "
            + (
                ", ".join(
                    f"{canonical} ← `{source}`"
                    for canonical, source in upload_report.detected_columns.items()
                )
                or "none"
            )
        )
        zone_counts_text = " · ".join(
            f"{zone_name_for(asset_id)}: {count}"
            for asset_id, count in upload_report.zone_counts.items()
        )
        st.write(f"**Zone classification:** {zone_counts_text}")
        st.write(
            f"**Cleaning:** {upload_report.dropped_rows} invalid rows dropped · "
            f"{upload_report.duplicate_rows} duplicate rows removed · "
            f"{upload_report.ambiguous_rows} ambiguous rows flagged"
        )
        for note in upload_report.notes:
            st.caption(f"ℹ️ {note}")
        if upload_report.ambiguous_rows:
            st.warning(
                "Some records could not be mapped safely to a fixed zone and were "
                "excluded from analysis rather than silently assigned: "
                + " | ".join(upload_report.ambiguous_examples)
            )

# Manual zone input editor (4 fixed zones, one current snapshot)
manual_inputs: List[ZoneInput] = []
if data_source == "Manual zone input":
    st.subheader("📝 Manual Zone Input")
    st.caption(
        "Enter one current reading per zone. Totals below reflect a 5-minute snapshot "
        "and update immediately after every edit."
    )

    if "manual_rows" not in st.session_state:
        st.session_state.manual_rows = zone_display_rows(default_zone_inputs())
    if "manual_editor_version" not in st.session_state:
        st.session_state.manual_editor_version = 0

    if st.button("Prefill from sample data", key="btn_prefill_manual"):
        st.session_state.manual_rows = zone_display_rows(
            prefill_from_sample("data/sample_sensors.csv")
        )
        st.session_state.manual_editor_version += 1
        st.rerun()

    edited_rows = st.data_editor(
        st.session_state.manual_rows,
        key=f"manual_zone_editor_{st.session_state.manual_editor_version}",
        num_rows="fixed",
        hide_index=True,
        use_container_width=True,
        disabled=["Zone"],
        column_config={
            "Zone": st.column_config.TextColumn(
                "Zone", help="Fixed asset zone (read-only)"
            ),
            "Current Power (W)": st.column_config.NumberColumn(
                "Current Power (W)", min_value=0.0, step=10.0, format="%.1f"
            ),
            "Baseline Power (W)": st.column_config.NumberColumn(
                "Baseline Power (W)", min_value=0.1, step=10.0, format="%.1f"
            ),
            "Occupancy": st.column_config.NumberColumn(
                "Occupancy", min_value=0, step=1, format="%d"
            ),
            "Area (m²)": st.column_config.NumberColumn(
                "Area (m²)", min_value=0.1, step=1.0, format="%.1f"
            ),
        },
    )
    st.session_state.manual_rows = edited_rows

    manual_inputs, input_errors = inputs_from_rows(edited_rows)
    if input_errors:
        st.error(
            "Invalid zone input — please correct the following: "
            + " | ".join(input_errors)
        )
        st.stop()

# Data loading & Digital Twin evaluation
if data_source == "Sample dataset":
    df = get_sensor_data()
    baselines = calculate_baselines(df)
elif data_source == "Upload dataset":
    df = upload_standardized[~upload_standardized["ambiguous"]].copy()
    df = df.drop(columns=["ambiguous", "ambiguous_reason"], errors="ignore")
    if df.empty:
        st.error(
            "None of the uploaded rows could be matched to the four fixed zones. "
            "Check that the file has an asset/zone column using recognisable names."
        )
        st.stop()
    # Derive baselines from the uploaded data (percentile-based, robust to spikes)
    baselines = calculate_baselines(df)
    # Derive zone thresholds from the actual data so uploads are accurate
    derived_thresholds = derive_zone_thresholds_from_data(df, baselines)
    ZONE_THRESHOLDS.update(derived_thresholds)
else:
    df = build_snapshot_df(manual_inputs, interval_minutes=INTERVAL_MINUTES)
    baselines = baselines_from_inputs(manual_inputs)

# State hygiene: clear stale AI output whenever the active inputs change
if data_source == "Sample dataset":
    input_fingerprint = "sample"
elif data_source == "Upload dataset":
    input_fingerprint = f"upload:{upload_digest}:{carbon_factor:.4f}"
else:
    input_fingerprint = hashlib.sha256(
        json.dumps(
            [
                [
                    zone.asset_id,
                    zone.power_w,
                    zone.baseline_power_w,
                    zone.occupancy_count,
                    zone.area_m2,
                ]
                for zone in manual_inputs
            ]
        ).encode("utf-8")
    ).hexdigest()

if st.session_state.get("input_fingerprint") != input_fingerprint:
    st.session_state.input_fingerprint = input_fingerprint
    st.session_state.ai_recommendations = {}
    st.session_state.approved_actions = {}

# Threshold-based analysis of the uploaded dataset
upload_enriched: pd.DataFrame | None = None
upload_zone_summary: pd.DataFrame | None = None
if data_source == "Upload dataset":
    try:
        upload_enriched = classify_records(upload_standardized, carbon_factor)
        upload_zone_summary = summarize_zones(upload_enriched, tariff)
    except Exception as exc:
        st.error(
            "Could not analyze the uploaded dataset: "
            f"{exc}. Please check that the file contains readable power readings "
            "and try again."
        )
        st.stop()

available_assets = sorted(df["asset_id"].unique().tolist())
asset_filter = st.sidebar.selectbox(
    "Asset Filter",
    options=["All Assets"] + available_assets,
    index=0,
)

# Initialize and evaluate TwinStore
store = TwinStore(baselines=baselines, carbon_factor=carbon_factor)
twin_states = store.evaluate_dataframe(df, interval_minutes=INTERVAL_MINUTES)

# Determine active slice based on filter
if asset_filter == "All Assets":
    active_df = df
    display_states = twin_states
else:
    active_df = df[df["asset_id"] == asset_filter]
    display_states = {asset_filter: twin_states[asset_filter]}

# Per-zone area and display names (manual values win; sample uses zone defaults)
if data_source == "Manual zone input":
    area_by_asset: Dict[str, float] = {
        zone.asset_id: float(zone.area_m2) for zone in manual_inputs
    }
    name_by_asset: Dict[str, str] = {
        zone.asset_id: zone.zone_name for zone in manual_inputs
    }
else:
    area_by_asset = {
        asset_id: float(spec["default_area_m2"])
        for asset_id, spec in ZONE_SPECS.items()
    }
    name_by_asset = {asset_id: zone_name_for(asset_id) for asset_id in available_assets}

# Top Row KPI Calculations
total_consumption = float(active_df["energy_kwh"].sum())

# Compute excess consumption across active dataset
excess_kwh_list = []
for _, row in active_df.iterrows():
    base = baselines.get(row["asset_id"], 0.0)
    if row["occupancy_count"] == 0 and row["power_w"] > STATE_ACTIVE_THRESHOLD * base:
        excess_w = max(0.0, row["power_w"] - (EXCESS_BASELINE_FACTOR * base))
        excess_kwh_list.append(
            (excess_w / 1000.0) * (INTERVAL_MINUTES / 60.0)
        )
    else:
        excess_kwh_list.append(0.0)

total_excess = float(sum(excess_kwh_list))
projected_co2 = calculate_carbon(total_consumption, carbon_factor)
active_assets_count = sum(1 for s in display_states.values() if s.state == "ACTIVE")
waste_alerts_count = sum(1 for s in display_states.values() if s.waste_alert)
electricity_cost = total_consumption * tariff
excess_energy_cost = total_excess * tariff

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

# Second KPI row: tariff-based cost metrics
cost_col1, cost_col2 = st.columns(2)
with cost_col1:
    st.metric("Electricity Cost (₹)", f"₹{electricity_cost:,.2f}")
with cost_col2:
    st.metric("Excess Energy Cost (₹)", f"₹{excess_energy_cost:,.2f}")

st.caption(
    f"Tariff: ₹{tariff:.2f}/kWh · Carbon factor: {carbon_factor:.2f} kg CO2e/kWh · "
    + (
        "Sample dataset (5-minute interval)"
        if data_source == "Sample dataset"
        else "Manual snapshot (5-minute interval)"
    )
)

st.write("")

# Main View (Structured Tabs)
tab_labels = [
    "📊 Asset Overview",
    "🤖 AI Recommendations",
    "📄 Sustainability Report",
    "🔮 What-If Simulation",
]
if data_source == "Upload dataset":
    tab_labels.insert(0, "📤 Dataset Analysis")
tabs = st.tabs(tab_labels)
tab_offset = 1 if data_source == "Upload dataset" else 0
overview_tab = tabs[tab_offset]
ai_tab = tabs[tab_offset + 1]
report_tab = tabs[tab_offset + 2]
simulation_tab = tabs[tab_offset + 3]

# TAB 0 (upload only): automatic dataset analysis
if data_source == "Upload dataset":
    with tabs[0]:
        st.subheader(f"Automatic Zone Analysis: {upload_filename}")

        total_energy_kwh = float(
            upload_enriched.loc[
                upload_enriched["asset_id"].isin(ZONE_SPECS), "energy_kwh"
            ].sum()
        )
        total_waste_kwh = float(
            upload_enriched.loc[
                upload_enriched["asset_id"].isin(ZONE_SPECS), "excess_energy_kwh"
            ].sum()
        )
        total_carbon_kg = float(
            upload_enriched.loc[
                upload_enriched["asset_id"].isin(ZONE_SPECS), "carbon_kg"
            ].sum()
        )
        classified_rows = int(
            upload_enriched["asset_id"].isin(ZONE_SPECS).sum()
        )
        system_status = overall_status(upload_zone_summary)
        status_icon = {"NORMAL": "🟢", "WARNING": "🟡", "CRITICAL": "🔴"}.get(
            system_status, "⚪"
        )

        sum_col1, sum_col2, sum_col3, sum_col4 = st.columns(4)
        with sum_col1:
            st.metric("Total Energy Consumed", f"{total_energy_kwh:,.2f} kWh")
        with sum_col2:
            st.metric("Total Estimated Waste", f"{total_waste_kwh:,.2f} kWh")
        with sum_col3:
            st.metric("Total Carbon Emissions", f"{total_carbon_kg:,.2f} kg CO2e")
        with sum_col4:
            st.metric("Overall System Status", f"{status_icon} {system_status}")

        st.caption(
            f"Tariff ₹{tariff:.2f}/kWh · Estimated energy cost "
            f"₹{total_energy_kwh * tariff:,.2f} · Waste cost "
            f"₹{total_waste_kwh * tariff:,.2f} · "
            f"{classified_rows} classified readings"
        )

        st.write("")
        st.subheader("Four-Zone Analysis")
        st.dataframe(
            upload_zone_summary,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Zone": st.column_config.TextColumn("Zone"),
                "Asset ID": st.column_config.TextColumn("Asset ID"),
                "Avg Power (W)": st.column_config.NumberColumn("Avg Power (W)", format="%.1f"),
                "Peak Power (W)": st.column_config.NumberColumn("Peak Power (W)", format="%.1f"),
                "Threshold (W)": st.column_config.NumberColumn("Threshold (W)", format="%.1f"),
                "Energy (kWh)": st.column_config.NumberColumn("Energy (kWh)", format="%.3f"),
                "Baseline Energy (kWh)": st.column_config.NumberColumn("Baseline Energy (kWh)", format="%.3f"),
                "Excess Energy (kWh)": st.column_config.NumberColumn("Excess Energy (kWh)", format="%.3f"),
                "Carbon (kg)": st.column_config.NumberColumn("Carbon (kg)", format="%.3f"),
                "Cost (₹)": st.column_config.NumberColumn("Cost (₹)", format="%.2f"),
                "Warning Readings": st.column_config.NumberColumn("Warning Readings", format="%d"),
                "Critical Readings": st.column_config.NumberColumn("Critical Readings", format="%d"),
                "Status": st.column_config.TextColumn("Status"),
            },
        )

        st.write("")
        st.subheader("Visualizations")
        chart_left, chart_right = st.columns(2)
        with chart_left:
            st.caption("Energy consumed by zone (kWh)")
            st.bar_chart(
                upload_zone_summary.set_index("Zone")[["Energy (kWh)"]], height=280
            )
        with chart_right:
            st.caption("Average power vs warning threshold (W)")
            st.bar_chart(
                upload_zone_summary.set_index("Zone")[
                    ["Avg Power (W)", "Threshold (W)"]
                ],
                height=280,
            )

        chart_left2, chart_right2 = st.columns(2)
        with chart_left2:
            st.caption("Excess energy wastage by zone (kWh)")
            st.bar_chart(
                upload_zone_summary.set_index("Zone")[["Excess Energy (kWh)"]],
                height=280,
            )
        with chart_right2:
            st.caption("Carbon emissions by zone (kg CO2e)")
            st.bar_chart(
                upload_zone_summary.set_index("Zone")[["Carbon (kg)"]], height=280
            )

        st.write("")
        with st.expander(
            f"Processed records ({len(upload_enriched)} rows)", expanded=False
        ):
            st.caption(
                "Columns: " + " · ".join(PROCESSED_COLUMNS)
            )
            st.dataframe(
                upload_enriched[PROCESSED_COLUMNS],
                use_container_width=True,
                hide_index=True,
                height=360,
                column_config={
                    "timestamp": st.column_config.DatetimeColumn(
                        "timestamp", format="YYYY-MM-DD HH:mm"
                    ),
                    "power_w": st.column_config.NumberColumn("power_w", format="%.1f"),
                    "threshold_w": st.column_config.NumberColumn("threshold_w", format="%.1f"),
                    "excess_power_w": st.column_config.NumberColumn("excess_power_w", format="%.1f"),
                    "energy_kwh": st.column_config.NumberColumn("energy_kwh", format="%.4f"),
                    "carbon_kg": st.column_config.NumberColumn("carbon_kg", format="%.4f"),
                },
            )

        st.write("")
        st.subheader("AI Recommendations")
        if st.session_state.get("upload_ai_fingerprint") != input_fingerprint:
            with st.spinner("Generating threshold-based recommendations..."):
                st.session_state.upload_ai_fingerprint = input_fingerprint
                st.session_state.upload_ai_recommendations = build_zone_recommendations(
                    upload_zone_summary, carbon_factor
                )

        upload_recommendations = st.session_state.get("upload_ai_recommendations", [])
        if not upload_recommendations:
            st.success(
                "All four zones are within their configured thresholds. No "
                "corrective recommendations are required."
            )
        else:
            for item in upload_recommendations:
                rec = item["recommendation"]
                with st.expander(
                    f"{'🔴' if item['status'] == 'CRITICAL' else '🟡'} "
                    f"{item['zone_name']} — {item['status']}",
                    expanded=True,
                ):
                    st.info(f"**Why:** {item['explanation']}")
                    st.markdown(f"#### Recommended Action: **{rec['decision']}**")
                    rec_col1, rec_col2, rec_col3 = st.columns(3)
                    with rec_col1:
                        st.metric("Priority", rec["priority"])
                    with rec_col2:
                        st.metric(
                            "Projected Energy Saving",
                            f"{rec['projected_saving_kwh']:.3f} kWh",
                        )
                    with rec_col3:
                        st.metric(
                            "Projected CO2e Reduction",
                            f"{rec['projected_co2e_kg']:.3f} kg",
                        )
                    st.markdown(f"**Rationale:** {rec['rationale']}")
                    st.markdown(f"**Operator Instruction:** {rec['operator_instruction']}")
                    st.caption(
                        f"Action `{rec['selected_action_id']}` · "
                        f"confidence {rec['confidence']:.2f} · "
                        "human approval required"
                    )

# TAB 1: Asset Overview
with overview_tab:
    st.subheader("Asset Digital Twin Status")

    # Construct clean overview table
    table_data = []
    for state in display_states.values():
        area_m2 = area_by_asset.get(state.asset_id)
        w_per_m2 = (
            round(state.current_power_w / area_m2, 2) if area_m2 else None
        )
        kwh_per_m2_day = (
            round(state.current_power_w / 1000.0 * 24.0 / area_m2, 3)
            if area_m2
            else None
        )
        table_data.append({
            "Asset ID": state.asset_id,
            "Zone": name_by_asset.get(state.asset_id, state.asset_id),
            "State": state.state,
            "Power (W)": round(state.current_power_w, 2),
            "Baseline (W)": round(state.baseline_power_w, 2),
            "Occupancy": state.occupancy_count,
            "Excess kWh": round(state.excess_kwh, 2),
            "Excess Cost (₹)": round(state.excess_kwh * tariff, 2),
            "W/m²": w_per_m2,
            "kWh/m²/day (at current load)": kwh_per_m2_day,
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
            "Zone": st.column_config.TextColumn("Zone"),
            "State": st.column_config.TextColumn("State"),
            "Power (W)": st.column_config.NumberColumn("Power (W)", format="%.2f"),
            "Baseline (W)": st.column_config.NumberColumn("Baseline (W)", format="%.2f"),
            "Occupancy": st.column_config.NumberColumn("Occupancy", format="%d"),
            "Excess kWh": st.column_config.NumberColumn("Excess kWh", format="%.2f"),
            "Excess Cost (₹)": st.column_config.NumberColumn("Excess Cost (₹)", format="%.2f"),
            "W/m²": st.column_config.NumberColumn("W/m²", format="%.2f"),
            "kWh/m²/day (at current load)": st.column_config.NumberColumn(
                "kWh/m²/day (at current load)", format="%.3f"
            ),
            "CO2e (kg)": st.column_config.NumberColumn("CO2e (kg)", format="%.2f"),
            "Waste Alert Status": st.column_config.TextColumn("Waste Alert Status"),
        },
    )
    st.caption(
        "W/m² uses the current reading. The daily kWh/m² figure extrapolates the "
        "current load over 24 hours and is an estimate only."
    )

    st.write("")
    st.subheader(f"Telemetry Analysis: {asset_filter}")

    if data_source != "Manual zone input":
        # Telemetry Line Chart for selected asset's power consumption over time
        if asset_filter == "All Assets":
            chart_df = df.pivot(index="timestamp", columns="asset_id", values="power_w")
        else:
            chart_df = df[df["asset_id"] == asset_filter].set_index("timestamp")[["power_w"]]
            chart_df.columns = [f"{asset_filter} Power (W)"]

        st.line_chart(chart_df, height=360)
    else:
        # A manual snapshot has no history: compare current vs baseline per zone
        chart_df = pd.DataFrame(
            {
                "Zone": [name_by_asset.get(s.asset_id, s.asset_id) for s in display_states.values()],
                "Current Power (W)": [s.current_power_w for s in display_states.values()],
                "Baseline Power (W)": [s.baseline_power_w for s in display_states.values()],
            }
        ).set_index("Zone")
        st.bar_chart(chart_df, height=360)

    # Export the active snapshot with computed state and KPI columns
    export_rows = []
    for state in display_states.values():
        area_m2 = area_by_asset.get(state.asset_id)
        export_rows.append({
            "timestamp": state.last_updated,
            "asset_id": state.asset_id,
            "zone_name": name_by_asset.get(state.asset_id, state.asset_id),
            "data_source": data_source,
            "power_w": round(state.current_power_w, 2),
            "baseline_power_w": round(state.baseline_power_w, 2),
            "occupancy_count": state.occupancy_count,
            "area_m2": area_m2,
            "state": state.state,
            "excess_kwh": round(state.excess_kwh, 4),
            "excess_cost_inr": round(state.excess_kwh * tariff, 2),
            "co2e_kg": round(state.co2e_kg, 4),
            "w_per_m2": (
                round(state.current_power_w / area_m2, 2) if area_m2 else None
            ),
            "kwh_per_m2_per_day": (
                round(state.current_power_w / 1000.0 * 24.0 / area_m2, 3)
                if area_m2
                else None
            ),
            "waste_alert": state.waste_alert,
            "tariff_inr_per_kwh": tariff,
            "carbon_factor": carbon_factor,
        })

    st.download_button(
        "⬇️ Download snapshot CSV",
        data=pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8"),
        file_name=f"ecotwin_snapshot_{datetime.now():%Y%m%d_%H%M}.csv",
        mime="text/csv",
        key="btn_download_snapshot",
    )


# TAB 2: AI Recommendations
with ai_tab:
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


# TAB 3: Sustainability Report
with report_tab:
    st.subheader("Executive Sustainability & Carbon Audit")

    # Aggregate total baseline kWh across dataset
    total_baseline_kwh = float(sum((baselines.get(r["asset_id"], 0.0) / 1000.0) * (INTERVAL_MINUTES / 60.0) for _, r in df.iterrows()))
    approved_savings_kwh = float(sum(a.get("projected_saving_kwh", 0.0) for a in st.session_state.approved_actions.values()))
    approved_co2e_kg = float(sum(a.get("projected_co2e_kg", 0.0) for a in st.session_state.approved_actions.values()))
    projected_cost_savings = approved_savings_kwh * tariff
    top_wasting_assets = [s.asset_id for s in twin_states.values() if s.waste_alert]

    # Per-zone breakdown for the report payload
    consumption_by_asset = df.groupby("asset_id")["energy_kwh"].sum().to_dict()
    zone_breakdown = [
        {
            "asset_id": state.asset_id,
            "zone_name": name_by_asset.get(state.asset_id, state.asset_id),
            "state": state.state,
            "power_w": round(state.current_power_w, 2),
            "consumption_kwh": round(float(consumption_by_asset.get(state.asset_id, 0.0)), 4),
            "excess_kwh": round(state.excess_kwh, 4),
            "waste_alert": state.waste_alert,
        }
        for state in twin_states.values()
    ]

    # Metrics summary preview
    s_col1, s_col2, s_col3 = st.columns(3)
    with s_col1:
        st.metric("Total Consumption", f"{total_consumption:.2f} kWh")
    with s_col2:
        st.metric("Total Baseline", f"{total_baseline_kwh:.2f} kWh")
    with s_col3:
        st.metric("Total Excess Waste", f"{total_excess:.2f} kWh")

    s_col4, s_col5, s_col6 = st.columns(3)
    with s_col4:
        st.metric("Approved Actions", f"{len(st.session_state.approved_actions)}")
    with s_col5:
        st.metric("Projected Savings", f"{approved_savings_kwh:.2f} kWh")
    with s_col6:
        st.metric("Projected CO2e Avoidance", f"{approved_co2e_kg:.2f} kg")

    st.write("")

    summary_payload = {
        "total_consumption_kwh": round(total_consumption, 2),
        "total_baseline_kwh": round(total_baseline_kwh, 2),
        "total_excess_kwh": round(total_excess, 2),
        "approved_savings_kwh": round(approved_savings_kwh, 2),
        "approved_co2e_kg": round(approved_co2e_kg, 2),
        "top_wasting_assets": top_wasting_assets,
        "carbon_factor": carbon_factor,
        "tariff_per_kwh": tariff,
        "projected_cost_savings_inr": round(projected_cost_savings, 2),
        "approved_actions_count": len(st.session_state.approved_actions),
        "zone_breakdown": zone_breakdown,
    }

    gen_btn_col, _ = st.columns([2, 5])
    with gen_btn_col:
        if st.button("Generate Sustainability Report", key="btn_gen_report", type="primary"):
            with st.spinner("Generating executive sustainability audit..."):
                st.session_state.sustainability_report = generate_sustainability_report(summary_payload)

    # Render report callout container
    if st.session_state.sustainability_report:
        st.markdown(
            f'<div class="report-card">{st.session_state.sustainability_report}</div>',
            unsafe_allow_html=True,
        )


# TAB 4: What-If Simulation
SIMULATION_ACTIONS = {
    "Do Nothing": 0.0,
    "Switch to Low-Power Mode": 0.30,
    "Enable Sleep Mode": 0.70,
    "Schedule Shutdown": 1.00,
}

with simulation_tab:
    st.subheader("1-Hour Energy What-If Simulation")
    st.caption("Deterministic projection based on current power readings. No AI calls — pure physics.")

    st.write("")

    # User Controls
    sim_col1, sim_col2 = st.columns([2, 3])
    with sim_col1:
        sim_asset = st.selectbox(
            "Select Asset",
            options=available_assets,
            key="sim_asset_select",
        )
    with sim_col2:
        sim_action = st.selectbox(
            "Simulated Action",
            options=list(SIMULATION_ACTIONS.keys()),
            key="sim_action_select",
        )

    st.write("")

    # Simulation Math (deterministic — no LLM)
    sim_state = twin_states.get(sim_asset)
    if sim_state:
        current_power_w = sim_state.current_power_w
        reduction_factor = SIMULATION_ACTIONS[sim_action]

        current_1hr_kwh = current_power_w / 1000.0
        optimized_1hr_kwh = current_1hr_kwh * (1.0 - reduction_factor)
        savings_kwh = current_1hr_kwh - optimized_1hr_kwh
        savings_co2e = savings_kwh * carbon_factor
        delta_kwh = optimized_1hr_kwh - current_1hr_kwh  # negative = improvement

        # Side-by-side metric cards
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Current 1-hr Projection", f"{current_1hr_kwh:.3f} kWh")
        with m2:
            st.metric(
                "Optimized 1-hr Projection",
                f"{optimized_1hr_kwh:.3f} kWh",
                delta=f"{delta_kwh:+.3f} kWh",
                delta_color="inverse",
            )
        with m3:
            st.metric("Projected Savings", f"{savings_kwh:.3f} kWh")
        with m4:
            st.metric("Projected CO2e Saved", f"{savings_co2e:.3f} kg")

        st.write("")

        # Side-by-side bar chart comparison
        chart_data = pd.DataFrame(
            {
                "Scenario": ["Current", sim_action],
                "1-Hour Energy (kWh)": [round(current_1hr_kwh, 4), round(optimized_1hr_kwh, 4)],
            }
        ).set_index("Scenario")

        st.bar_chart(chart_data, height=300, use_container_width=True)

        st.caption(
            f"Asset: `{sim_asset}` | Action: `{sim_action}` | "
            f"Reduction Factor: {reduction_factor*100:.0f}% | "
            f"Carbon Factor: {carbon_factor:.2f} kg CO2e/kWh"
        )
    else:
        st.warning(f"No twin state available for asset: {sim_asset}")
