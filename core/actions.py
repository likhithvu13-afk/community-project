"""Action dispatch and control recommendation execution module."""

from typing import Dict, List, Optional
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


def is_action_allowed(asset_id: str, action_id: str) -> bool:
    """Check whether a requested action is allowed for an asset under safety rules."""
    forbidden = FORBIDDEN_ACTIONS.get(asset_id, [])
    return action_id not in forbidden


def generate_candidate_actions(
    asset_id: str,
    excess_kwh: float,
    carbon_factor: float = 0.42,
) -> List[CandidateAction]:
    """Deterministically generate pre-approved safety actions for energy-wasting assets.

    Rules by asset type:
    - HVAC ('hvac-unit-01'):
        * Allowed: "switch_to_low_mode_30m", "notify_facilities"
        * FORBIDDEN: Direct complete shutdown (minimum ventilation required).
        * Savings: 50% of excess_kwh.
    - Lighting ('lighting-zone-3b'):
        * Allowed: "schedule_shutdown", "notify_facilities"
        * Savings: 100% of excess_kwh.
    - Computers ('computer-cluster-1'):
        * Allowed: "enable_sleep_mode", "notify_facilities"
        * Savings: 80% of excess_kwh.
    - Printers ('printer-zone-1'):
        * Allowed: "schedule_shutdown", "notify_facilities"
        * Savings: 100% of excess_kwh.

    Args:
        asset_id: Identifier of the asset.
        excess_kwh: Calculated excess energy in kWh.
        carbon_factor: Emission intensity factor in kg CO2/kWh. Defaults to 0.42.

    Returns:
        List of pre-approved CandidateAction objects.
    """
    actions: List[CandidateAction] = []
    excess = max(0.0, float(excess_kwh))

    if asset_id == "hvac-unit-01":
        # 50% savings for low-mode, direct shutdown is strictly excluded
        saving_kwh = round(0.50 * excess, 4)
        co2e_kg = round(saving_kwh * carbon_factor, 4)
        actions.append(
            CandidateAction(
                action_id="switch_to_low_mode_30m",
                label="Switch HVAC to Low Mode (30 mins)",
                allowed=True,
                projected_saving_kwh=saving_kwh,
                projected_co2e_kg=co2e_kg,
                risk_score=0.20,
                requires_human_approval=True,
            )
        )
        actions.append(
            CandidateAction(
                action_id="notify_facilities",
                label="Notify Facilities Maintenance Team",
                allowed=True,
                projected_saving_kwh=0.0,
                projected_co2e_kg=0.0,
                risk_score=0.05,
                requires_human_approval=True,
            )
        )

    elif asset_id == "lighting-zone-3b":
        # 100% savings for scheduled lighting shutdown
        saving_kwh = round(1.00 * excess, 4)
        co2e_kg = round(saving_kwh * carbon_factor, 4)
        actions.append(
            CandidateAction(
                action_id="schedule_shutdown",
                label="Schedule Lighting Zone 3B Shutdown",
                allowed=True,
                projected_saving_kwh=saving_kwh,
                projected_co2e_kg=co2e_kg,
                risk_score=0.10,
                requires_human_approval=True,
            )
        )
        actions.append(
            CandidateAction(
                action_id="notify_facilities",
                label="Notify Facilities Maintenance Team",
                allowed=True,
                projected_saving_kwh=0.0,
                projected_co2e_kg=0.0,
                risk_score=0.05,
                requires_human_approval=True,
            )
        )

    elif asset_id == "computer-cluster-1":
        # 80% savings for standby / sleep mode
        saving_kwh = round(0.80 * excess, 4)
        co2e_kg = round(saving_kwh * carbon_factor, 4)
        actions.append(
            CandidateAction(
                action_id="enable_sleep_mode",
                label="Enable Computer Cluster Sleep Mode",
                allowed=True,
                projected_saving_kwh=saving_kwh,
                projected_co2e_kg=co2e_kg,
                risk_score=0.25,
                requires_human_approval=True,
            )
        )
        actions.append(
            CandidateAction(
                action_id="notify_facilities",
                label="Notify Facilities Maintenance Team",
                allowed=True,
                projected_saving_kwh=0.0,
                projected_co2e_kg=0.0,
                risk_score=0.05,
                requires_human_approval=True,
            )
        )

    elif asset_id == "printer-zone-1":
        # 100% savings for printer shutdown
        saving_kwh = round(1.00 * excess, 4)
        co2e_kg = round(saving_kwh * carbon_factor, 4)
        actions.append(
            CandidateAction(
                action_id="schedule_shutdown",
                label="Schedule Printer Deep Sleep / Shutdown",
                allowed=True,
                projected_saving_kwh=saving_kwh,
                projected_co2e_kg=co2e_kg,
                risk_score=0.10,
                requires_human_approval=True,
            )
        )
        actions.append(
            CandidateAction(
                action_id="notify_facilities",
                label="Notify Facilities Maintenance Team",
                allowed=True,
                projected_saving_kwh=0.0,
                projected_co2e_kg=0.0,
                risk_score=0.05,
                requires_human_approval=True,
            )
        )

    return actions
