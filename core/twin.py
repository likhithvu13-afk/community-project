"""Digital twin simulation and state management module."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd


@dataclass
class AssetTwinState:
    """Represents the real-time operational state of an asset digital twin."""

    asset_id: str
    state: str  # "OFF", "IDLE", or "ACTIVE"
    current_power_w: float
    baseline_power_w: float
    occupancy_count: int
    excess_kwh: float
    co2e_kg: float
    waste_alert: bool
    last_updated: datetime


def determine_asset_state(current_power_w: float, baseline_power_w: float) -> str:
    """Classify asset state based on power relative to baseline.

    Rules:
    - current_power_w < 0.10 * baseline_power_w -> "OFF"
    - 0.10 * baseline_power_w <= current_power_w <= 0.40 * baseline_power_w -> "IDLE"
    - current_power_w > 0.40 * baseline_power_w -> "ACTIVE"
    """
    if baseline_power_w <= 0.0:
        return "OFF" if current_power_w <= 0.0 else "ACTIVE"

    if current_power_w < 0.10 * baseline_power_w:
        return "OFF"
    elif current_power_w <= 0.40 * baseline_power_w:
        return "IDLE"
    else:
        return "ACTIVE"


class TwinStore:
    """In-memory store managing and evaluating digital twin asset states."""

    def __init__(
        self,
        baselines: Optional[Dict[str, float]] = None,
        carbon_factor: float = 0.42,
    ):
        self.baselines: Dict[str, float] = baselines or {}
        self.carbon_factor: float = carbon_factor
        self.states: Dict[str, AssetTwinState] = {}

    def set_baseline(self, asset_id: str, baseline_power_w: float) -> None:
        """Set or update the baseline power for an asset."""
        self.baselines[asset_id] = float(baseline_power_w)

    def evaluate_asset(
        self,
        asset_id: str,
        current_power_w: float,
        occupancy_count: int,
        timestamp: Optional[datetime] = None,
        interval_minutes: float = 5.0,
    ) -> AssetTwinState:
        """Evaluate and update the digital twin state for a single asset reading.

        Rules applied:
        - state: OFF (<0.10*baseline), IDLE (0.10-0.40*baseline), ACTIVE (>0.40*baseline)
        - waste_alert: True if occupancy_count == 0 and state == "ACTIVE"
        - excess_power_w: max(0.0, current_power_w - (0.10 * baseline_power_w)) if waste_alert
        - excess_kwh: (excess_power_w / 1000.0) * (interval_minutes / 60.0)
        - co2e_kg: excess_kwh * carbon_factor
        """
        baseline = self.baselines.get(asset_id, 0.0)
        state = determine_asset_state(current_power_w, baseline)

        waste_alert = (occupancy_count == 0) and (state == "ACTIVE")

        if waste_alert:
            excess_power_w = max(0.0, current_power_w - (0.10 * baseline))
            excess_kwh = (excess_power_w / 1000.0) * (interval_minutes / 60.0)
            co2e_kg = excess_kwh * self.carbon_factor
        else:
            excess_kwh = 0.0
            co2e_kg = 0.0

        last_updated = timestamp if timestamp is not None else datetime.now()

        twin_state = AssetTwinState(
            asset_id=asset_id,
            state=state,
            current_power_w=float(current_power_w),
            baseline_power_w=float(baseline),
            occupancy_count=int(occupancy_count),
            excess_kwh=float(excess_kwh),
            co2e_kg=float(co2e_kg),
            waste_alert=waste_alert,
            last_updated=last_updated,
        )

        self.states[asset_id] = twin_state
        return twin_state

    def evaluate_dataframe(
        self,
        df: pd.DataFrame,
        interval_minutes: float = 5.0,
    ) -> Dict[str, AssetTwinState]:
        """Process a DataFrame of sensor readings chronologically to update current states."""
        sorted_df = df.sort_values("timestamp")
        for _, row in sorted_df.iterrows():
            ts = row["timestamp"]
            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()
            self.evaluate_asset(
                asset_id=str(row["asset_id"]),
                current_power_w=float(row["power_w"]),
                occupancy_count=int(row["occupancy_count"]),
                timestamp=ts,
                interval_minutes=interval_minutes,
            )
        return self.states

    def get_state(self, asset_id: str) -> Optional[AssetTwinState]:
        """Retrieve the current state of a specific asset."""
        return self.states.get(asset_id)

    def get_all_states(self) -> Dict[str, AssetTwinState]:
        """Retrieve all current asset twin states."""
        return self.states

    def get_waste_alerts(self) -> List[AssetTwinState]:
        """Retrieve all assets currently flagging an active waste alert."""
        return [state for state in self.states.values() if state.waste_alert]
