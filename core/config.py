"""Configuration settings for Sustainable Energy Digital Twin.

Thresholds are developer-owned backend configuration. They are derived from the
sample telemetry (08:00-18:00 baselines) and zone operating characteristics, and
can be tuned here without any frontend changes.
"""

from dataclasses import dataclass
from typing import Dict


DEFAULT_CARBON_FACTOR = 0.42  # kg CO2e per kWh
DEFAULT_TARIFF_INR_PER_KWH = 8.0  # ₹ per kWh
DEFAULT_INTERVAL_MINUTES = 5.0
MAX_INTERVAL_MINUTES = 1440.0  # guard against timestamp gaps larger than a day
SMOOTHING_WINDOW = 3  # rolling readings used to suppress isolated spikes

# --- Baseline calculation tuning -------------------------------------------
# Percentile of operating-hours readings to use as the baseline.
# Mean is skewed by usage spikes (e.g. printer) and outliers (HVAC 9250 W).
# The median (50th) or a lower percentile (e.g. 25th) better represents
# "normal idle" power. Defaults to 50th percentile (median).
BASELINE_PERCENTILE = 50.0

# Exclude readings above this multiple of the median when computing baselines.
# Set to None to disable outlier exclusion.
BASELINE_OUTLIER_MULTIPLIER = 3.0

# --- Twin state determination thresholds -----------------------------------
# Fraction of baseline below which an asset is considered OFF.
STATE_OFF_THRESHOLD = 0.10

# Fraction of baseline above which an asset is considered ACTIVE.
# Between OFF and ACTIVE thresholds is IDLE.
STATE_ACTIVE_THRESHOLD = 0.40

# --- Waste detection tuning ------------------------------------------------
# Excess power is computed as max(0, power - EXCESS_BASELINE_FACTOR * baseline)
# when a waste alert fires. Lower values flag more excess; higher values are
# more conservative.
EXCESS_BASELINE_FACTOR = 0.10

# Minimum excess kWh required to raise a waste alert. Prevents noise from
# immaterial readings.
MIN_EXCESS_KWH_FOR_ALERT = 0.01


@dataclass(frozen=True)
class ZoneThreshold:
    """Fixed operating thresholds for one zone.

    Attributes:
        baseline_power_w: Normal operating baseline power in Watts.
        normal_max_w: Upper bound of the normal operating range in Watts.
        warning_w: Power above this value raises a WARNING.
        critical_w: Power above this value raises a CRITICAL alert.
        energy_limit_kwh_per_hour: Expected maximum energy use per hour (kWh).
    """

    baseline_power_w: float
    normal_max_w: float
    warning_w: float
    critical_w: float
    energy_limit_kwh_per_hour: float

    def __post_init__(self) -> None:
        if not (
            0.0
            < self.baseline_power_w
            <= self.normal_max_w
            <= self.warning_w
            < self.critical_w
        ):
            raise ValueError(
                "Zone thresholds must satisfy 0 < baseline <= normal_max "
                "<= warning < critical."
            )
        if self.energy_limit_kwh_per_hour <= 0.0:
            raise ValueError("energy_limit_kwh_per_hour must be positive.")


# Four fixed zones. Values are derived from data/sample_sensors.csv using the
# percentile-based baseline (see BASELINE_PERCENTILE) so they reflect typical
# idle power rather than mean operating power which is skewed by usage spikes.
#
# Derived baselines (50th percentile, 08:00-18:00):
#   HVAC     ~2700 W   (mean was 3488 W, skewed by peaks)
#   Lighting ~798 W    (stable, mean ≈ median)
#   Computers ~300 W   (mean was 1202 W, overnight idle is ~300 W)
#   Printers ~30 W     (mean was 85 W, skewed by printing spikes)
ZONE_THRESHOLDS: Dict[str, ZoneThreshold] = {
    "hvac-unit-01": ZoneThreshold(
        baseline_power_w=2700.0,
        normal_max_w=3900.0,
        warning_w=4300.0,
        critical_w=5000.0,
        energy_limit_kwh_per_hour=3.84,
    ),
    "lighting-zone-3b": ZoneThreshold(
        baseline_power_w=797.9,
        normal_max_w=810.0,
        warning_w=820.0,
        critical_w=835.0,
        energy_limit_kwh_per_hour=0.88,
    ),
    "computer-cluster-1": ZoneThreshold(
        baseline_power_w=300.0,
        normal_max_w=1230.0,
        warning_w=1245.0,
        critical_w=1260.0,
        energy_limit_kwh_per_hour=1.32,
    ),
    "printer-zone-1": ZoneThreshold(
        baseline_power_w=30.0,
        normal_max_w=120.0,
        warning_w=150.0,
        critical_w=400.0,
        energy_limit_kwh_per_hour=0.094,
    ),
}


def get_threshold(asset_id: str) -> ZoneThreshold:
    """Return the configured threshold, raising a clear error for unknown zones."""
    try:
        return ZONE_THRESHOLDS[asset_id]
    except KeyError as exc:
        raise KeyError(f"No threshold configured for zone '{asset_id}'.") from exc


def derive_zone_thresholds_from_data(
    df, baselines: Dict[str, float]
) -> Dict[str, ZoneThreshold]:
    """Derive per-zone thresholds from an actual dataset.

    This makes the twin accurate on *any* uploaded file, not just the sample.
    Baselines come from the caller (already computed), and warning/critical
    thresholds are set as percentiles of observed power per zone.

    Args:
        df: Cleaned DataFrame with 'asset_id' and 'power_w'.
        baselines: Mapping of asset_id -> baseline power in Watts.

    Returns:
        Dict of asset_id -> ZoneThreshold derived from the data.
    """
    import numpy as np

    thresholds: Dict[str, ZoneThreshold] = {}
    for asset_id, baseline in baselines.items():
        group = df[df["asset_id"] == asset_id]["power_w"]
        if group.empty:
            continue
        p95 = float(group.quantile(0.95))
        p99 = float(group.quantile(0.99))
        maximum = float(group.max())
        # normal_max: typical upper bound of normal operation
        normal_max = max(p95, baseline * 1.1)
        # warning: above normal but not yet critical
        warning = max(normal_max * 1.05, p99)
        # critical: absolute ceiling
        critical = max(warning * 1.1, maximum * 1.0)
        energy_limit = max(baseline / 1000.0 * 1.5, 0.1)

        thresholds[asset_id] = ZoneThreshold(
            baseline_power_w=baseline,
            normal_max_w=round(normal_max, 1),
            warning_w=round(warning, 1),
            critical_w=round(critical, 1),
            energy_limit_kwh_per_hour=round(energy_limit, 3),
        )
    return thresholds
