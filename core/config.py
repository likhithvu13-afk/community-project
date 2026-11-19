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


# Four fixed zones. Values are based on data/sample_sensors.csv:
#   HVAC     baseline ~3488 W, p95 ~4240 W, observed spike ~9250 W
#   Lighting baseline ~798 W, p95 ~816 W, observed max ~839 W
#   Computers baseline ~1202 W, p95 ~1242 W, observed max ~1266 W
#   Printers baseline ~85 W, idle ~30 W, usage spikes ~520 W
ZONE_THRESHOLDS: Dict[str, ZoneThreshold] = {
    "hvac-unit-01": ZoneThreshold(
        baseline_power_w=3488.1,
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
        baseline_power_w=1201.8,
        normal_max_w=1230.0,
        warning_w=1245.0,
        critical_w=1260.0,
        energy_limit_kwh_per_hour=1.32,
    ),
    "printer-zone-1": ZoneThreshold(
        baseline_power_w=85.2,
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
