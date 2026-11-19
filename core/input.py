"""Manual zone input handling for the Sustainable Energy Digital Twin.

This module converts operator-entered zone readings into the same snapshot
DataFrame shape the analytics pipeline already consumes, so the sample dataset
and manual input modes share identical downstream behaviour.
"""

import math
from datetime import datetime
from typing import Any, Dict, List, Tuple, TypedDict

import pandas as pd
from pydantic import BaseModel, Field, ValidationError, field_validator

from core.energy import calculate_baselines, load_and_clean_data

DEFAULT_INTERVAL_MINUTES = 5.0


class ZoneSpec(TypedDict):
    """Static metadata describing one of the four fixed zones."""

    display_name: str
    zone_type: str
    default_area_m2: float
    default_power_w: float
    default_baseline_power_w: float
    default_occupancy: int


# The four fixed assets of the digital twin. Defaults are deliberately set to a
# mid-baseline operating point so a fresh manual session starts without alerts.
ZONE_SPECS: Dict[str, ZoneSpec] = {
    "hvac-unit-01": {
        "display_name": "HVAC Unit 01",
        "zone_type": "hvac",
        "default_area_m2": 320.0,
        "default_power_w": 2000.0,
        "default_baseline_power_w": 3500.0,
        "default_occupancy": 12,
    },
    "lighting-zone-3b": {
        "display_name": "Lighting Zone 3B",
        "zone_type": "lighting",
        "default_area_m2": 240.0,
        "default_power_w": 400.0,
        "default_baseline_power_w": 800.0,
        "default_occupancy": 8,
    },
    "computer-cluster-1": {
        "display_name": "Computer Cluster 1",
        "zone_type": "computers",
        "default_area_m2": 180.0,
        "default_power_w": 600.0,
        "default_baseline_power_w": 1200.0,
        "default_occupancy": 6,
    },
    "printer-zone-1": {
        "display_name": "Printer Zone 1",
        "zone_type": "printers",
        "default_area_m2": 60.0,
        "default_power_w": 40.0,
        "default_baseline_power_w": 85.0,
        "default_occupancy": 2,
    },
}


class ZoneInput(BaseModel):
    """Validated operator-entered reading for a single zone."""

    asset_id: str
    zone_name: str
    power_w: float = Field(ge=0.0)
    baseline_power_w: float = Field(gt=0.0)
    occupancy_count: int = Field(ge=0)
    area_m2: float = Field(gt=0.0)

    @field_validator("power_w", "baseline_power_w", "area_m2")
    @classmethod
    def _require_finite(cls, value: float) -> float:
        """Reject NaN and infinite values before they reach the twin engine."""
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return float(value)

    @field_validator("occupancy_count")
    @classmethod
    def _require_whole_occupancy(cls, value: int) -> int:
        """Keep occupancy a non-negative whole number of people."""
        return int(value)


def default_zone_inputs() -> List[ZoneInput]:
    """Return one valid default reading per fixed zone."""
    return [
        ZoneInput(
            asset_id=asset_id,
            zone_name=spec["display_name"],
            power_w=spec["default_power_w"],
            baseline_power_w=spec["default_baseline_power_w"],
            occupancy_count=spec["default_occupancy"],
            area_m2=spec["default_area_m2"],
        )
        for asset_id, spec in ZONE_SPECS.items()
    ]


def build_snapshot_df(
    inputs: List[ZoneInput],
    interval_minutes: float = DEFAULT_INTERVAL_MINUTES,
) -> pd.DataFrame:
    """Build a single-snapshot telemetry DataFrame from manual zone inputs.

    Args:
        inputs: Validated zone readings, one per asset.
        interval_minutes: Energy integration window in minutes. Defaults to 5.0.

    Returns:
        DataFrame with columns ``timestamp, asset_id, power_w,
        occupancy_count, zone_name, energy_kwh`` where ``timestamp`` is "now".
    """
    if not inputs:
        raise ValueError("At least one zone input is required.")

    now = datetime.now()
    rows: List[Dict[str, Any]] = []
    for zone in inputs:
        rows.append(
            {
                "timestamp": now,
                "asset_id": zone.asset_id,
                "power_w": float(zone.power_w),
                "occupancy_count": int(zone.occupancy_count),
                "zone_name": zone.zone_name,
                "energy_kwh": (float(zone.power_w) / 1000.0)
                * (float(interval_minutes) / 60.0),
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "asset_id",
            "power_w",
            "occupancy_count",
            "zone_name",
            "energy_kwh",
        ],
    )


def baselines_from_inputs(inputs: List[ZoneInput]) -> Dict[str, float]:
    """Map each asset ID to the operator-supplied baseline power in Watts."""
    return {zone.asset_id: float(zone.baseline_power_w) for zone in inputs}


def prefill_from_sample(
    csv_path: str = "data/sample_sensors.csv",
) -> List[ZoneInput]:
    """Load the latest sample readings and 08:00-18:00 baselines per zone.

    Args:
        csv_path: Path to the sample telemetry CSV.

    Returns:
        One ZoneInput per known asset, ordered as in ``ZONE_SPECS``.
    """
    df = load_and_clean_data(csv_path)
    baselines = calculate_baselines(df)
    latest_by_asset = (
        df.sort_values("timestamp").groupby("asset_id").tail(1).set_index("asset_id")
    )

    inputs: List[ZoneInput] = []
    for asset_id, spec in ZONE_SPECS.items():
        if asset_id not in latest_by_asset.index:
            inputs.append(
                ZoneInput(
                    asset_id=asset_id,
                    zone_name=spec["display_name"],
                    power_w=spec["default_power_w"],
                    baseline_power_w=spec["default_baseline_power_w"],
                    occupancy_count=spec["default_occupancy"],
                    area_m2=spec["default_area_m2"],
                )
            )
            continue

        row = latest_by_asset.loc[asset_id]
        baseline_w = float(baselines.get(asset_id, 0.0))
        if baseline_w <= 0.0:
            baseline_w = spec["default_baseline_power_w"]

        inputs.append(
            ZoneInput(
                asset_id=asset_id,
                zone_name=spec["display_name"],
                power_w=max(0.0, float(row["power_w"])),
                baseline_power_w=baseline_w,
                occupancy_count=max(0, int(row["occupancy_count"])),
                area_m2=spec["default_area_m2"],
            )
        )

    return inputs


FIELD_LABELS = {
    "power_w": "Current Power (W)",
    "baseline_power_w": "Baseline Power (W)",
    "occupancy_count": "Occupancy",
    "area_m2": "Area (m²)",
    "asset_id": "Zone",
    "zone_name": "Zone",
}


def inputs_from_rows(rows: pd.DataFrame) -> Tuple[List[ZoneInput], List[str]]:
    """Validate editor rows into ZoneInput models.

    Args:
        rows: DataFrame with the editor columns ``Zone``, ``Current Power (W)``,
            ``Baseline Power (W)``, ``Occupancy`` and ``Area (m²)``.

    Returns:
        A tuple of ``(valid_inputs, errors)``. ``errors`` is empty on success and
        contains human-readable, field-named messages otherwise.
    """
    display_to_asset = {
        spec["display_name"]: asset_id for asset_id, spec in ZONE_SPECS.items()
    }
    inputs: List[ZoneInput] = []
    errors: List[str] = []

    for position, (_, row) in enumerate(rows.iterrows(), start=1):
        zone_label = str(row.get("Zone", "")).strip()
        asset_id = display_to_asset.get(zone_label)
        if asset_id is None:
            errors.append(f"row {position}: unknown zone '{zone_label}'")
            continue

        try:
            inputs.append(
                ZoneInput(
                    asset_id=asset_id,
                    zone_name=zone_label,
                    power_w=row.get("Current Power (W)"),
                    baseline_power_w=row.get("Baseline Power (W)"),
                    occupancy_count=row.get("Occupancy"),
                    area_m2=row.get("Area (m²)"),
                )
            )
        except ValidationError as exc:
            for error in exc.errors():
                field = str(error["loc"][0]) if error["loc"] else "input"
                label = FIELD_LABELS.get(field, field)
                errors.append(
                    f"row {position} ({zone_label}) — {label}: {error['msg']}"
                )

    return inputs, errors
