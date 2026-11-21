"""Energy metrics and calculations module."""

from typing import Dict, Optional
import pandas as pd
import numpy as np

from core.config import (
    BASELINE_PERCENTILE,
    BASELINE_OUTLIER_MULTIPLIER,
)


def load_and_clean_data(file_path: str = "data/sample_sensors.csv") -> pd.DataFrame:
    """Load sensor telemetry data and perform cleaning and energy integration.

    Processing steps:
    1. Load CSV and convert 'timestamp' to datetime objects.
    2. Drop duplicate rows on ['timestamp', 'asset_id'].
    3. Handle NaNs in 'power_w': compute from `voltage_v * current_a * power_factor`,
       then fallback to forward-fill (`ffill()`).
    4. Clip negative power values to 0.0.
    5. Exact interval integration: compute `energy_kwh` per row as:
       `(power_w / 1000.0) * (5 / 60)`.

    Args:
        file_path: Path to the CSV file. Defaults to "data/sample_sensors.csv".

    Returns:
        Cleaned pd.DataFrame with datetime timestamp and calculated energy_kwh.
    """
    df = pd.read_csv(file_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.drop_duplicates(subset=["timestamp", "asset_id"]).copy()

    # Compute missing power_w from physical parameters: P = V * I * PF
    calculated_power = df["voltage_v"] * df["current_a"] * df["power_factor"]
    df["power_w"] = df["power_w"].fillna(calculated_power)
    df["power_w"] = df["power_w"].ffill()

    # Clip negative power values to 0.0
    df["power_w"] = df["power_w"].clip(lower=0.0)

    # Exact interval integration: 5 minutes = 5 / 60 hours
    df["energy_kwh"] = (df["power_w"] / 1000.0) * (5.0 / 60.0)

    return df


def calculate_baselines(
    df: pd.DataFrame,
    start_hour: int = 8,
    end_hour: int = 18,
    percentile: float = BASELINE_PERCENTILE,
    outlier_multiplier: Optional[float] = BASELINE_OUTLIER_MULTIPLIER,
) -> Dict[str, float]:
    """Calculate baseline power (W) for each asset during normal operating hours.

    Filters active power readings between 08:00 and 18:00 (default operating
    window) and computes a robust baseline per asset using a percentile of the
    operating-hours distribution. The percentile (default: 50th / median) is far
    more accurate than the arithmetic mean because it is not skewed by:

    - Usage spikes (e.g. a printer drawing 520 W while printing)
    - Outlier readings (e.g. an HVAC sensor reporting 9250 W once)

    Optionally excludes readings above ``outlier_multiplier * median`` before
    computing the percentile, which removes extreme sensor glitches entirely.

    Args:
        df: Cleaned sensor DataFrame containing 'timestamp', 'asset_id', and 'power_w'.
        start_hour: Start of operating window (inclusive, 0-23). Defaults to 8 (08:00).
        end_hour: End of operating window (exclusive, 0-23). Defaults to 18 (18:00).
        percentile: Percentile (0-100) of operating-hours power to use as baseline.
                    Defaults to 50 (median).
        outlier_multiplier: Exclude readings above ``multiplier * median``.
                            Set to None to disable. Defaults to 3.0.

    Returns:
        Dictionary mapping asset_id to baseline power in Watts.
    """
    operating_mask = (df["timestamp"].dt.hour >= start_hour) & (
        df["timestamp"].dt.hour < end_hour
    )
    operating_df = df[operating_mask]

    baselines: Dict[str, float] = {}
    for asset_id, group in operating_df.groupby("asset_id"):
        powers = group["power_w"].astype(float)
        if powers.empty:
            baselines[asset_id] = 0.0
            continue

        # Optional outlier exclusion: drop extreme readings before baseline calc.
        if outlier_multiplier is not None:
            median_power = powers.median()
            outlier_cutoff = median_power * outlier_multiplier
            powers = powers[powers <= outlier_cutoff]
            if powers.empty:
                powers = group["power_w"].astype(float)

        baseline_value = float(powers.quantile(percentile / 100.0))
        baselines[asset_id] = round(baseline_value, 2)

    return baselines


def calculate_carbon(energy_kwh: float, carbon_factor: float = 0.42) -> float:
    """Calculate carbon emissions equivalent in kilograms of CO2.

    Args:
        energy_kwh: Total electrical energy in kilowatt-hours.
        carbon_factor: Emission intensity factor in kg CO2 per kWh. Defaults to 0.42.

    Returns:
        Emissions in kg CO2 rounded to 4 decimal places.
    """
    return round(float(energy_kwh * carbon_factor), 4)
