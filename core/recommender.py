"""Recommendation engine for energy efficiency and sustainability."""

import json
import os
import uuid
from typing import Any, Dict, List
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class AIRecommendation(BaseModel):
    """Pydantic schema for strictly validated AI recommendations."""

    recommendation_id: str
    decision: str
    selected_action_id: str
    priority: str  # "LOW", "MEDIUM", "HIGH"
    requires_human_approval: bool = True
    projected_saving_kwh: float
    projected_co2e_kg: float
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    operator_instruction: str


def create_fallback_recommendation(
    twin_state: Dict[str, Any],
    candidate_actions: List[Dict[str, Any]],
    carbon_factor: float = 0.42,
    reason: str = "Deterministic safety fallback engaged.",
) -> AIRecommendation:
    """Create a deterministic, safe fallback recommendation."""
    allowed_actions = [a for a in candidate_actions if a.get("allowed", False)]
    if allowed_actions:
        selected = allowed_actions[0]
    elif candidate_actions:
        selected = candidate_actions[0]
    else:
        selected = {
            "action_id": "notify_facilities",
            "label": "Notify Facilities Maintenance Team",
            "projected_saving_kwh": 0.0,
            "projected_co2e_kg": 0.0,
        }

    action_id = str(selected.get("action_id", "notify_facilities"))
    label = str(selected.get("label", "Notify Facilities Maintenance Team"))
    saving_kwh = float(selected.get("projected_saving_kwh", 0.0))
    co2e_kg = float(selected.get("projected_co2e_kg", saving_kwh * carbon_factor))

    priority = "HIGH" if saving_kwh > 5.0 else ("MEDIUM" if saving_kwh > 0 else "LOW")
    asset_id = str(twin_state.get("asset_id", "unknown"))

    return AIRecommendation(
        recommendation_id=f"rec-fallback-{uuid.uuid4().hex[:8]}",
        decision=f"Execute {label}",
        selected_action_id=action_id,
        priority=priority,
        requires_human_approval=True,
        projected_saving_kwh=round(saving_kwh, 4),
        projected_co2e_kg=round(co2e_kg, 4),
        confidence=1.0,
        rationale=f"{reason} Selected safest pre-approved action '{action_id}'.",
        operator_instruction=f"Review and confirm execution of action '{label}' for asset {asset_id}.",
    )


def get_ai_recommendation(
    twin_state: Dict[str, Any],
    candidate_actions: List[Dict[str, Any]],
    carbon_factor: float = 0.42,
) -> AIRecommendation:
    """Generate a schema-validated AI recommendation for an energy-wasting asset.

    Steps:
    1. Check for valid GEMINI_API_KEY (or fallback to OPENAI_API_KEY).
    2. If missing, return a deterministic fallback recommendation.
    3. Pass digital twin state and pre-approved candidate actions as JSON to Gemini.
    4. Enforce strict schema validation using AIRecommendation.model_validate_json().
    5. Handle any network, API, or validation error by returning the safe fallback.

    Args:
        twin_state: Dictionary representation of AssetTwinState.
        candidate_actions: List of dictionaries of candidate actions.
        carbon_factor: Emission factor in kg CO2/kWh.

    Returns:
        AIRecommendation validated instance.
    """
    if not candidate_actions:
        return create_fallback_recommendation(
            twin_state, [], carbon_factor, "No candidate actions provided."
        )

    load_dotenv()
    gemini_key = os.getenv("GEMINI_API_KEY")

    # If key is absent, placeholder, or empty, return safe deterministic fallback
    if not gemini_key or gemini_key.strip() in ("", "your_key_here"):
        return create_fallback_recommendation(
            twin_state,
            candidate_actions,
            carbon_factor,
            "GEMINI_API_KEY is not configured.",
        )

    try:
        from google import genai

        client = genai.Client(api_key=gemini_key.strip())

        prompt = f"""You are an expert Energy Management and Building Automation AI.
Analyze the following asset digital twin state and select the best candidate action to optimize energy efficiency and eliminate waste while adhering strictly to building safety rules.

Digital Twin State:
{json.dumps(twin_state, indent=2, default=str)}

Available Candidate Actions:
{json.dumps(candidate_actions, indent=2)}

Carbon Intensity Factor: {carbon_factor} kg CO2e/kWh

Strict Safety Instructions:
1. You MUST select EXACTLY ONE 'selected_action_id' from the provided candidate actions list where allowed == True.
2. NEVER invent new actions or modify 'projected_saving_kwh' or 'projected_co2e_kg' from the selected action.
3. Set requires_human_approval to True.
4. Set priority to 'LOW', 'MEDIUM', or 'HIGH'.
5. Provide a clear, technical rationale and operator_instruction.
6. Return ONLY valid JSON matching this schema:
{{
  "recommendation_id": "string",
  "decision": "string",
  "selected_action_id": "string",
  "priority": "LOW" | "MEDIUM" | "HIGH",
  "requires_human_approval": true,
  "projected_saving_kwh": float,
  "projected_co2e_kg": float,
  "confidence": float,
  "rationale": "string",
  "operator_instruction": "string"
}}
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        content = response.text.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Schema validation
        recommendation = AIRecommendation.model_validate_json(content)

        # Safety Guard: Ensure selected_action_id is one of the candidate actions
        matching_actions = {a["action_id"]: a for a in candidate_actions}
        if recommendation.selected_action_id not in matching_actions:
            return create_fallback_recommendation(
                twin_state,
                candidate_actions,
                carbon_factor,
                f"AI selected non-existent action ID '{recommendation.selected_action_id}'.",
            )

        # Enforce exact candidate action savings to prevent hallucinated values
        selected = matching_actions[recommendation.selected_action_id]
        recommendation.projected_saving_kwh = float(selected.get("projected_saving_kwh", 0.0))
        recommendation.projected_co2e_kg = float(selected.get("projected_co2e_kg", recommendation.projected_saving_kwh * carbon_factor))
        recommendation.requires_human_approval = True

        return recommendation

    except Exception as exc:
        return create_fallback_recommendation(
            twin_state,
            candidate_actions,
            carbon_factor,
            f"AI exception: {exc}.",
        )


def generate_sustainability_report(summary_data: Dict[str, Any]) -> str:
    """Generate a concise executive sustainability report (< 200 words).

    Requirements:
    - If API key is missing or call fails, use a deterministic structured fallback.
    - Produces an executive summary under 200 words.
    - Explicitly states that all savings are projected estimates.
    - Highlights key findings and exactly 2 actionable next steps.

    Args:
        summary_data: Dictionary containing energy and emissions aggregates.

    Returns:
        Markdown string formatted report.
    """
    load_dotenv()
    gemini_key = os.getenv("GEMINI_API_KEY")

    total_cons = float(summary_data.get("total_consumption_kwh", 0.0))
    total_base = float(summary_data.get("total_baseline_kwh", 0.0))
    total_excess = float(summary_data.get("total_excess_kwh", 0.0))
    savings = float(summary_data.get("approved_savings_kwh", 0.0))
    co2e = float(summary_data.get("approved_co2e_kg", 0.0))
    top_wasting = summary_data.get("top_wasting_assets", [])
    wasting_str = ", ".join(top_wasting) if top_wasting else "None detected"

    fallback_report = f"""### Executive Sustainability Summary

**Facility Scope:** Commercial Office Zone 3B

**Key Findings:**
Total monitored electrical consumption reached **{total_cons:.2f} kWh** against a baseline reference of **{total_base:.2f} kWh**, identifying **{total_excess:.2f} kWh** of detected excess energy waste. The primary driver of waste is `{wasting_str}`, where equipment remained energized during zero-occupancy overnight intervals. Approved intervention measures represent a projected savings of **{savings:.2f} kWh** and **{co2e:.2f} kg CO2e** emissions reduction. *Note: All stated energy and emissions savings are projected estimates based on digital twin calculations.*

**Actionable Next Steps:**
1. **Automate After-Hours Curtailment:** Implement automated setback schedules for lighting and plug loads during non-operational hours.
2. **Review Setpoint Guidelines:** Coordinate with facility engineering to calibrate HVAC low-power ventilation setpoints for continuous air quality and optimal efficiency."""

    if not gemini_key or gemini_key.strip() in ("", "your_key_here"):
        return fallback_report

    try:
        from google import genai

        client = genai.Client(api_key=gemini_key.strip())

        prompt = f"""You are an executive energy sustainability auditor.
Review the following commercial office telemetry summary:
{json.dumps(summary_data, indent=2)}

Write a concise executive summary report adhering strictly to these rules:
1. MUST be under 200 words total.
2. Explicitly state that all savings are projected estimates.
3. Highlight key findings (consumption vs baseline, excess waste, top wasting assets).
4. Highlight exactly 2 actionable next steps for facility management.
5. Use clean Markdown formatting with clear headings.
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        report_text = response.text.strip()
        return report_text if report_text else fallback_report
    except Exception:
        return fallback_report
