# Task: Add user-entered 4-zone data input to the Sustainable Energy Digital Twin

## Role

You are a senior Python + Streamlit engineer working inside this repository.
Deliver production-quality, well-documented code consistent with the existing style.

## Goal (read this first)

Today the app is hard-wired to `data/sample_sensors.csv`. Add a **Manual zone input**
data source so the operator types current readings for the 4 zones directly in the UI,
while keeping the existing sample-dataset demo fully functional.

## Verified context (do not re-architect)

- `app.py` (~440 lines) is the Streamlit entry point: CSS block, session state,
  CSV load → `calculate_baselines()` → `TwinStore` → KPI row → 4 tabs
  (Asset Overview, AI Recommendations, Sustainability Report, What-If Simulation).
- `core/energy.py`: `load_and_clean_data()`, `calculate_baselines()` (mean power,
  08:00–18:00), `calculate_carbon()`. Energy per row = `power_w/1000 * 5/60`.
- `core/twin.py`: `AssetTwinState`; `determine_asset_state()` — OFF <10%,
  IDLE 10–40%, ACTIVE >40% of baseline; waste alert = occupancy == 0 AND ACTIVE;
  excess = `max(0, power_w - 0.10*baseline)`.
- `core/actions.py`: candidate actions hardcoded per asset ID
  (`hvac-unit-01`, `lighting-zone-3b`, `computer-cluster-1`, `printer-zone-1`);
  HVAC direct shutdown is forbidden by safety policy.
- `core/recommender.py`: Gemini recommendation + report generation with strict
  pydantic schemas and deterministic fallbacks when `GEMINI_API_KEY` is absent.
- `data/sample_sensors.csv`: 1,152 rows, 5-minute interval, same 4 assets.
  Columns: `timestamp, asset_id, power_w, voltage_v, current_a, power_factor,
  occupancy_count, zone_name`.

## Decisions already made (follow these exactly)

- Input granularity: **single snapshot per zone** (one current reading each).
- Baseline: **operator enters baseline power (W) per zone** (not derived from history).
- Sample dataset stays available as a **toggle**, defaulting to sample mode.
- Extras included: **cost savings with tariff**, **per-zone intensity**
  (**W/m²** plus a clearly-labelled daily `kWh/m²` estimate), and
  **export snapshot to CSV**.
- No ML forecasting in this task.

## Functional requirements

### 1. Sidebar

- Add `st.radio("Data source", ["Sample dataset", "Manual zone input"])`.
- Keep the existing Carbon Factor input.
- Add `Tariff (₹/kWh)` number input: default `8.0`, min `0.0`, step `0.5`.

### 2. Manual zone editor (4 rows, fixed assets)

- Use `st.data_editor` with `num_rows="fixed"` and a stable widget `key` so edits
  persist across tab switches and reruns.
- Columns: Zone (read-only label), Current Power (W), Baseline Power (W),
  Occupancy (int), Area (m²).
- Enforce bounds with `st.column_config.NumberColumn`; additionally validate with
  pydantic: power ≥ 0, baseline > 0, occupancy integer ≥ 0, area > 0.
- Provide a **"Prefill from sample data"** button that loads the latest CSV
  readings plus the 08:00–18:00 baselines into the editor.
- On invalid input: show `st.error` naming the offending field and stop before
  computing analytics.

### 3. Snapshot DataFrame builder — new `core/input.py`

- `ZoneInput` pydantic model: `asset_id: str`, `zone_name: str`,
  `power_w: float ≥ 0`, `baseline_power_w: float > 0`,
  `occupancy_count: int ≥ 0`, `area_m2: float > 0`.
- `ZONE_SPECS`: the 4 fixed asset IDs mapped to display name, zone type
  (`hvac` | `lighting` | `computers` | `printers`) and a sensible default area.
- `build_snapshot_df(inputs, interval_minutes=5.0)` → DataFrame with columns
  `timestamp, asset_id, power_w, occupancy_count, zone_name, energy_kwh`
  where `timestamp = now` and `energy_kwh = power_w/1000 * interval/60`.
- `baselines_from_inputs(inputs) -> dict[str, float]`.
- `prefill_from_sample(csv_path) -> list[ZoneInput]`.

### 4. Data loading branch in `app.py`

- Sample mode: unchanged (`get_sensor_data()` + `calculate_baselines()`).
- Manual mode: `build_snapshot_df()` + `baselines_from_inputs()`.
- Everything downstream (`TwinStore`, KPIs, tabs) must consume the same variables
  so behaviour is identical in both modes.
- The asset filter must still work in manual mode.

### 5. KPIs

- Keep the existing 5 metrics unchanged.
- Add a second row: `Electricity Cost (₹) = total_consumption * tariff` and
  `Excess Energy Cost (₹) = total_excess * tariff`.
- Per-zone intensity: **W/m²** in the asset table; optional daily estimate
  `kWh/m²/day = power_w/1000*24/area`, labelled "at current load".

### 6. Asset Overview tab

- Table gains Occupancy, Cost (₹) for excess, and W/m² columns.
- Chart: sample mode keeps the existing line chart; manual mode shows a grouped
  bar chart of Current vs Baseline power per zone (a snapshot has no history).
- Add `st.download_button` exporting inputs + computed state/KPIs as CSV.

### 7. State hygiene

- Fingerprint the active inputs. When they change, clear
  `st.session_state.ai_recommendations` and `approved_actions` so stale
  recommendations and approvals are never displayed.

### 8. `core/actions.py` robustness

- Keep exact-ID branches first, then add a zone-type fallback based on the asset
  ID prefix (`hvac*`, `light*`, `computer*`, `printer*`) so candidate actions are
  never empty if a zone is renamed.

### 9. `core/recommender.py`

- Include tariff and a per-zone breakdown in the report summary payload, and
  mention projected cost savings in the deterministic fallback report.

## Constraints

- No new third-party dependencies; only `streamlit`, `pandas`, `python-dotenv`,
  `pydantic`, `google-genai` are installed (Python 3.12).
- Do not change twin thresholds or existing safety rules.
- Preserve sample-mode behaviour; do not modify `data/sample_sensors.csv`.
- Never print or commit secrets; `.env` must remain gitignored and untouched.
- Match existing style: module docstrings, type hints, plain functions.

## Acceptance criteria

- [ ] Sample mode behaves exactly as before (KPIs, tabs, AI fallbacks).
- [ ] Manual mode accepts 4 zone readings and produces the same KPI/tab set.
- [ ] Changing power/occupancy/baseline instantly updates state, alerts, KPIs.
- [ ] Occupancy 0 + power > 40% baseline raises a waste alert in manual mode.
- [ ] Tariff updates cost KPIs without a restart.
- [ ] Invalid values are blocked with a clear error message.
- [ ] CSV export downloads the current snapshot with computed columns.
- [ ] AI recommendations/approvals reset when inputs change.
- [ ] No browser console errors; app still starts with `streamlit run app.py`.

## Verification

1. `python -m compileall app.py core`
2. `streamlit run app.py --server.port=8501` and check `/_stcore/health`.
3. Exercise both modes, both charts, the CSV export, and one AI tab.
4. Report a short diff summary and any deviations from this spec.

## Out of scope

ML forecasting, multi-timestamp input, persistence/database, authentication,
changes to the recommendation safety policy.
