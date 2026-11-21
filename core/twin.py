"""Digital twin simulation and state management module."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
import pandas as pd

from core.config import (
    STATE_OFF_THRESHOLD,
    STATE_ACTIVE_THRESHOLD,
    EXCESS_BASELINE_FACTOR,
    MIN_EXCESS_KWH_FOR_ALERT,
)


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


def determine_asset_state(
    current_power_w: float,
    baseline_power_w: float,
    off_threshold: float = STATE_OFF_THRESHOLD,
    active_threshold: float = STATE_ACTIVE_THRESHOLD,
) -> str:
    """Classify asset state based on power relative to baseline.

    Rules (all configurable via :mod:`core.config`):
    - current_power_w < off_threshold * baseline_power_w -> "OFF"
    - off_threshold * baseline <= current_power_w <= active_threshold * baseline -> "IDLE"
    - current_power_w > active_threshold * baseline_power_w -> "ACTIVE"

    Args:
        current_power_w: Current measured power in Watts.
        baseline_power_w: Baseline (typical idle) power in Watts.
        off_threshold: Fraction of baseline below which the asset is OFF.
        active_threshold: Fraction of baseline above which the asset is ACTIVE.

    Returns:
        One of "OFF", "IDLE", or "ACTIVE".
    """
    if baseline_power_w <= 0.0:
        return "OFF" if current_power_w <= 0.0 else "ACTIVE"

    off_limit = off_threshold * baseline_power_w
    active_limit = active_threshold * baseline_power_w

    if current_power_w < off_limit:
        return "OFF"
    elif current_power_w <= active_limit:
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
        - state: OFF / IDLE / ACTIVE based on configurable thresholds
        - waste_alert: True if occupancy_count == 0 and state == "ACTIVE"
        - excess_power_w: max(0.0, current_power_w - excess_factor * baseline)
          only when a waste alert fires
        - excess_kwh: (excess_power_w / 1000.0) * (interval_minutes / 60.0)
        - co2e_kg: excess_kwh * carbon_factor

        Args:
            asset_id: Identifier of the asset.
            current_power_w: Current measured power in Watts.
            occupancy_count: Number of occupants detected.
            timestamp: Reading timestamp. Defaults to now.
            interval_minutes: Integration window in minutes. Defaults to 5.0.

        Returns:
            The updated AssetTwinState.
        """
        baseline = self.baselines.get(asset_id, 0.0)
        state = determine_asset_state(current_power_w, baseline)

        waste_alert = (occupancy_count == 0) and (state == "ACTIVE")

        if waste_alert:
            excess_power_w = max(
                0.0, current_power_w - (EXCESS_BASELINE_FACTOR * baseline)
            )
            excess_kwh = (excess_power_w / 1000.0) * (interval_minutes / 60.0)
            # Suppress immaterial excess to avoid noise.
            if excess_kwh < MIN_EXCESS_KWH_FOR_ALERT:
                excess_kwh = 0.0
                waste_alert = False
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
            row_interval = interval_minutes
            if "interval_minutes" in row.index and pd.notna(row["interval_minutes"]):
                row_interval = float(row["interval_minutes"])
            self.evaluate_asset(
                asset_id=str(row["asset_id"]),
                current_power_w=float(row["power_w"]),
                occupancy_count=int(row["occupancy_count"]),
                timestamp=ts,
                interval_minutes=row_interval,
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
