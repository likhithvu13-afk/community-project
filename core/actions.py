"""Action dispatch and control recommendation execution module."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CandidateAction(BaseModel):
    """Pydantic model representing a pre-approved, safe candidate action."""

    action_id: str
    label: str
    allowed: bool
    projected_saving_kwh: float
    projected_co2e_kg: float
    risk_score: float = Field(ge=0.0, le=1.0)  # Range 0.0 to 1.0
    requires_human_approval: bool = True


# Explicit safety policy: direct complete shutdown is strictly prohibited for HVAC
# to maintain mandatory building ventilation safety standards.
FORBIDDEN_ACTIONS: Dict[str, List[str]] = {
    "hvac-unit-01": [
        "direct_shutdown",
        "complete_shutdown",
        "emergency_shutdown",
        "shutdown",
    ],
}

# Zone-type mapping used as a fallback when an asset ID is renamed.
ZONE_PREFIX_MAP: Dict[str, str] = {
    "hvac": "hvac",
    "light": "lighting",
    "computer": "computers",
    "printer": "printers",
}

# Primary (savings-bearing) safe action per zone type.
PRIMARY_ACTION_SPECS: Dict[str, Dict[str, Any]] = {
    "hvac": {
        "action_id": "switch_to_low_mode_30m",
        "label": "Switch HVAC to Low Mode (30 mins)",
        "saving_factor": 0.50,
        "risk_score": 0.20,
    },
    "lighting": {
        "action_id": "schedule_shutdown",
        "label": "Schedule Lighting Shutdown",
        "saving_factor": 1.00,
        "risk_score": 0.10,
    },
    "computers": {
        "action_id": "enable_sleep_mode",
        "label": "Enable Computer Cluster Sleep Mode",
        "saving_factor": 0.80,
        "risk_score": 0.25,
    },
    "printers": {
        "action_id": "schedule_shutdown",
        "label": "Schedule Printer Deep Sleep / Shutdown",
        "saving_factor": 1.00,
        "risk_score": 0.10,
    },
}

# Exact-ID branches keep their original, more specific labels.
LABEL_OVERRIDES: Dict[str, str] = {
    "lighting-zone-3b": "Schedule Lighting Zone 3B Shutdown",
}


def zone_type_for_asset(asset_id: str) -> Optional[str]:
    """Resolve a zone type from an asset ID prefix (``hvac*``, ``light*``, ...)."""
    normalized = asset_id.strip().lower()
    for prefix, zone_type in ZONE_PREFIX_MAP.items():
        if normalized.startswith(prefix):
            return zone_type
    return None


def is_action_allowed(asset_id: str, action_id: str) -> bool:
    """Check whether a requested action is allowed for an asset under safety rules."""
    forbidden = list(FORBIDDEN_ACTIONS.get(asset_id, []))
    if zone_type_for_asset(asset_id) == "hvac":
        # Apply the HVAC ventilation safety policy to renamed HVAC assets too.
        forbidden.extend(FORBIDDEN_ACTIONS.get("hvac-unit-01", []))
    return action_id not in forbidden


def generate_candidate_actions(
    asset_id: str,
    excess_kwh: float,
    carbon_factor: float = 0.42,
) -> List[CandidateAction]:
    """Deterministically generate pre-approved safety actions for energy-wasting assets.

    Rules by asset type:
    - HVAC ('hvac*'):
        * Allowed: "switch_to_low_mode_30m", "notify_facilities"
        * FORBIDDEN: Direct complete shutdown (minimum ventilation required).
        * Savings: 50% of excess_kwh.
    - Lighting ('light*'):
        * Allowed: "schedule_shutdown", "notify_facilities"
        * Savings: 100% of excess_kwh.
    - Computers ('computer*'):
        * Allowed: "enable_sleep_mode", "notify_facilities"
        * Savings: 80% of excess_kwh.
    - Printers ('printer*'):
        * Allowed: "schedule_shutdown", "notify_facilities"
        * Savings: 100% of excess_kwh.

    Exact asset-ID branches are matched first; any other asset ID falls back to
    its zone type by prefix so candidate actions are never empty after a rename.

    Args:
        asset_id: Identifier of the asset.
        excess_kwh: Calculated excess energy in kWh.
        carbon_factor: Emission intensity factor in kg CO2/kWh. Defaults to 0.42.

    Returns:
        List of pre-approved CandidateAction objects.
    """
    zone_type = zone_type_for_asset(asset_id)
    if zone_type is None:
        return []

    spec = PRIMARY_ACTION_SPECS[zone_type]
    excess = max(0.0, float(excess_kwh))

    saving_kwh = round(float(spec["saving_factor"]) * excess, 4)
    co2e_kg = round(saving_kwh * carbon_factor, 4)
    label = LABEL_OVERRIDES.get(asset_id, str(spec["label"]))

    actions: List[CandidateAction] = [
        CandidateAction(
            action_id=str(spec["action_id"]),
            label=label,
            allowed=True,
            projected_saving_kwh=saving_kwh,
            projected_co2e_kg=co2e_kg,
            risk_score=float(spec["risk_score"]),
            requires_human_approval=True,
        ),
        CandidateAction(
            action_id="notify_facilities",
            label="Notify Facilities Maintenance Team",
            allowed=True,
            projected_saving_kwh=0.0,
            projected_co2e_kg=0.0,
            risk_score=0.05,
            requires_human_approval=True,
        ),
    ]

    return actions
