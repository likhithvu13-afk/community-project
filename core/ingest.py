"""Automatic dataset ingestion for the Sustainable Energy Digital Twin.

A single uploaded CSV / Excel / JSON file is parsed, its columns are mapped by
name, records are classified into the four fixed zones, and the result is
standardised and enriched with per-zone thresholds, energy and carbon figures.

No operator configuration is required: every heuristic lives here in the backend.
"""

import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.actions import generate_candidate_actions
from core.config import (
    DEFAULT_INTERVAL_MINUTES,
    MAX_INTERVAL_MINUTES,
    SMOOTHING_WINDOW,
    ZONE_THRESHOLDS,
    get_threshold,
)
from core.input import ZONE_SPECS
from core.recommender import AIRecommendation, get_ai_recommendation


SUPPORTED_FORMATS = {"csv", "xlsx", "xls", "xlsm", "json"}

STANDARD_COLUMNS = [
    "timestamp",
    "asset_id",
    "zone_name",
    "power_w",
    "occupancy_count",
    "interval_minutes",
    "energy_kwh",
    "ambiguous",
]

PROCESSED_COLUMNS = [
    "timestamp",
    "asset_id",
    "zone_name",
    "power_w",
    "threshold_w",
    "excess_power_w",
    "energy_kwh",
    "carbon_kg",
    "status",
]


# ---------------------------------------------------------------------------
# Column name detection
# ---------------------------------------------------------------------------

COLUMN_ALIASES: Dict[str, List[str]] = {
    "timestamp": [
        "timestamp", "time", "datetime", "date_time", "recorded_at",
        "reading_time", "reading_timestamp", "ts", "date",
    ],
    "power_w": [
        "power_w", "power", "power_watts", "watts", "watt", "active_power",
        "load_w", "load", "demand_w", "demand", "consumption_w",
    ],
    "power_kw": [
        "power_kw", "kw", "active_power_kw", "load_kw", "demand_kw",
    ],
    "asset_id": [
        "asset_id", "assetid", "asset", "device_id", "deviceid", "device",
        "meter_id", "meter", "equipment_id", "equipment", "unit_id",
    ],
    "zone_name": [
        "zone_name", "zonename", "zone", "zone_id", "zoneid", "location",
        "location_name", "area_name", "space",
    ],
    "occupancy_count": [
        "occupancy_count", "occupancy", "occupants", "people", "people_count",
        "headcount", "persons",
    ],
    "energy_kwh": [
        "energy_kwh", "energy", "consumption_kwh", "kwh", "energy_consumption",
    ],
}


def _normalise_name(name: Any) -> str:
    """Lowercase a column name and collapse non-alphanumerics into underscores."""
    text = re.sub(r"[^0-9a-zA-Z]+", "_", str(name).strip().lower())
    return text.strip("_")


def _normalise_columns(columns: List[Any]) -> Dict[str, Any]:
    """Map canonical field names to the original column labels found in a file."""
    normalised = {_normalise_name(column): column for column in columns}
    detected: Dict[str, Any] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalised:
                detected[canonical] = normalised[alias]
                break
    return detected


# ---------------------------------------------------------------------------
# Zone identification
# ---------------------------------------------------------------------------

ZONE_KEYWORDS: Dict[str, set] = {
    "hvac-unit-01": {
        "hvac", "ac", "ahu", "chiller", "ventilation", "heating", "cooling",
        "conditioning", "aircon",
    },
    "lighting-zone-3b": {
        "lighting", "light", "lights", "lamp", "lamps", "illumination", "led",
    },
    "computer-cluster-1": {
        "computer", "computers", "pc", "pcs", "workstation", "workstations",
        "server", "servers", "cluster", "it",
    },
    "printer-zone-1": {
        "printer", "printers", "print", "printing", "copier", "mfp", "scanner",
    },
}


def _tokens(value: Any) -> set:
    """Tokenise a label such as ``Lighting-Zone 3B`` into matching keywords."""
    return set(_normalise_name(value).split("_"))


def _match_types(value: Any) -> set:
    """Return the zone types whose keywords appear in a label."""
    found = _tokens(value)
    if not found:
        return set()
    matches = set()
    for asset_id, keywords in ZONE_KEYWORDS.items():
        if found & keywords:
            matches.add(asset_id)
    return matches


def resolve_zone(
    asset_value: Any,
    zone_value: Any,
) -> Tuple[Optional[str], str, bool, str]:
    """Resolve one row's asset/zone labels to a fixed backend zone.

    Returns:
        ``(asset_id, reason, ambiguous, confidence)``. ``asset_id`` is ``None``
        when the record cannot be assigned safely.
    """
    asset_text = "" if asset_value is None else str(asset_value).strip()
    zone_text = "" if zone_value is None else str(zone_value).strip()

    # 1. Exact backend asset ID (or a normalised spelling of it).
    normalised_asset = _normalise_name(asset_text)
    for asset_id in ZONE_SPECS:
        if normalised_asset == _normalise_name(asset_id):
            return asset_id, "exact asset ID match", False, "high"

    # 2. Keyword matching on the asset label, then the zone label.
    asset_matches = _match_types(asset_text)
    zone_matches = _match_types(zone_text)

    if asset_matches and zone_matches:
        if asset_matches == zone_matches:
            matched = next(iter(asset_matches))
            return matched, "asset and zone labels agree", False, "high"
        return (
            None,
            f"asset label suggests {sorted(asset_matches)} but zone label "
            f"suggests {sorted(zone_matches)}",
            True,
            "ambiguous",
        )

    matches = asset_matches or zone_matches
    if len(matches) == 1:
        matched = next(iter(matches))
        source = "asset label" if asset_matches else "zone label"
        return matched, f"matched by {source} keyword", False, "medium"
    if len(matches) > 1:
        return (
            None,
            f"label matches multiple zones: {sorted(matches)}",
            True,
            "ambiguous",
        )

    return (
        None,
        f"could not map asset '{asset_text}' / zone '{zone_text}' to a fixed zone",
        True,
        "unresolved",
    )


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------


def detect_format(filename: str, payload: bytes) -> str:
    """Detect the dataset format from the extension, then from content."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in SUPPORTED_FORMATS:
        return "excel" if extension in {"xlsx", "xls", "xlsm"} else extension

    stripped = payload.lstrip()[:1]
    if stripped in {b"{", b"["}:
        return "json"
    return "csv"


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def read_dataset(filename: str, payload: bytes) -> Tuple[pd.DataFrame, str]:
    """Read a CSV, Excel or JSON dataset into a raw DataFrame.

    Args:
        filename: Original upload name (used for format detection).
        payload: Raw file bytes.

    Returns:
        A tuple of ``(raw_dataframe, file_format)``.
    """
    file_format = detect_format(filename, payload)

    if file_format == "excel":
        return pd.read_excel(io.BytesIO(payload), engine="openpyxl"), file_format

    if file_format == "json":
        data = json.loads(_decode_text(payload))
        if isinstance(data, dict):
            for key in ("data", "records", "rows", "readings"):
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
            else:
                if all(isinstance(value, list) for value in data.values()):
                    data = pd.DataFrame(data)
                else:
                    data = [data]
        return pd.json_normalize(data), file_format

    text = _decode_text(payload)
    try:
        raw = pd.read_csv(io.StringIO(text), sep=None, engine="python")
    except Exception:
        raw = pd.read_csv(io.StringIO(text))
    return raw, file_format


# ---------------------------------------------------------------------------
# Standardisation
# ---------------------------------------------------------------------------


@dataclass
class IngestReport:
    """Diagnostics describing what the automatic pipeline did."""

    file_format: str
    detected_columns: Dict[str, str] = field(default_factory=dict)
    total_rows: int = 0
    valid_rows: int = 0
    dropped_rows: int = 0
    duplicate_rows: int = 0
    ambiguous_rows: int = 0
    zone_counts: Dict[str, int] = field(default_factory=dict)
    ambiguous_examples: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse a timestamp column, including Unix seconds/milliseconds."""
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all() and numeric.max() > 10_000_000:
        unit = "ms" if numeric.max() > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, unit=unit, errors="coerce")
    return pd.to_datetime(series, errors="coerce")


def _compute_intervals(df: pd.DataFrame) -> pd.Series:
    """Derive each record's interval from real timestamps, with safe fallbacks."""
    working = df[["asset_id", "timestamp"]].copy()
    working["next_timestamp"] = working.groupby("asset_id")["timestamp"].shift(-1)
    delta_minutes = (
        working["next_timestamp"] - working["timestamp"]
    ).dt.total_seconds() / 60.0

    median_delta = delta_minutes.groupby(working["asset_id"]).transform("median")
    intervals = delta_minutes.where(delta_minutes > 0, median_delta)
    intervals = intervals.fillna(DEFAULT_INTERVAL_MINUTES)
    return intervals.clip(lower=0.0, upper=MAX_INTERVAL_MINUTES)


def standardize_dataset(
    raw: pd.DataFrame,
    file_format: str = "csv",
) -> Tuple[pd.DataFrame, IngestReport]:
    """Convert an arbitrary energy dataset into the internal snapshot schema.

    Steps: detect columns, parse timestamps, resolve zones, clean rows, drop
    duplicates and compute energy using the real timestamp intervals.

    Args:
        raw: The raw uploaded DataFrame.
        file_format: Detected format label, stored in the report.

    Returns:
        A tuple of the standardized DataFrame (including an ``ambiguous`` flag)
        and an :class:`IngestReport`.

    Raises:
        ValueError: If no usable power or zone-identifying columns exist.
    """
    report = IngestReport(file_format=file_format, total_rows=int(len(raw)))
    detected = _normalise_columns(list(raw.columns))
    report.detected_columns = {
        canonical: str(original) for canonical, original in detected.items()
    }

    if "power_w" not in detected and "power_kw" not in detected:
        raise ValueError(
            "No power column found. Expected a column such as 'power_w', "
            "'power', 'active_power' or 'kw'."
        )
    if "asset_id" not in detected and "zone_name" not in detected:
        raise ValueError(
            "No asset or zone column found. Expected a column such as "
            "'asset_id', 'device', 'zone_name' or 'location'."
        )

    working = pd.DataFrame(index=raw.index)
    working["asset_label"] = raw[detected["asset_id"]] if "asset_id" in detected else ""
    working["zone_label"] = raw[detected["zone_name"]] if "zone_name" in detected else ""

    if "power_kw" in detected and "power_w" not in detected:
        working["power_w"] = pd.to_numeric(raw[detected["power_kw"]], errors="coerce") * 1000.0
    else:
        working["power_w"] = pd.to_numeric(raw[detected["power_w"]], errors="coerce")

    if "timestamp" in detected:
        working["timestamp"] = _parse_timestamps(raw[detected["timestamp"]])
    else:
        working["timestamp"] = pd.Timestamp.now() + pd.to_timedelta(
            np.arange(len(raw)) * DEFAULT_INTERVAL_MINUTES, unit="m"
        )
        report.notes.append(
            "No timestamp column found; sequential 5-minute timestamps were assumed."
        )

    if "occupancy_count" in detected:
        working["occupancy_count"] = (
            pd.to_numeric(raw[detected["occupancy_count"]], errors="coerce")
            .fillna(1)
            .astype(int)
            .clip(lower=0)
        )
    else:
        working["occupancy_count"] = 1
        report.notes.append(
            "No occupancy column found; occupancy assumed to be 1 (unknown)."
        )

    # Resolve each row into one of the four fixed zones.
    resolved_assets: List[Optional[str]] = []
    resolved_reasons: List[str] = []
    ambiguous_flags: List[bool] = []
    for asset_value, zone_value in zip(
        working["asset_label"], working["zone_label"]
    ):
        asset_id, reason, ambiguous, _ = resolve_zone(asset_value, zone_value)
        resolved_assets.append(asset_id)
        resolved_reasons.append(reason)
        ambiguous_flags.append(ambiguous)

    working["resolved_asset"] = resolved_assets
    working["ambiguous"] = ambiguous_flags
    working["ambiguous_reason"] = resolved_reasons

    # Clean unusable rows: bad timestamps, missing/negative power, NaNs.
    working["power_w"] = pd.to_numeric(working["power_w"], errors="coerce")
    valid_mask = (
        working["timestamp"].notna()
        & working["power_w"].notna()
        & np.isfinite(working["power_w"])
        & (working["power_w"] >= 0.0)
    )
    dropped = int((~valid_mask).sum())
    working = working[valid_mask].copy()

    before_dedupe = len(working)
    working = working.drop_duplicates(
        subset=["timestamp", "asset_label", "zone_label"], keep="last"
    )
    report.duplicate_rows = int(before_dedupe - len(working))

    working["asset_id"] = working["resolved_asset"].map(
        lambda value: value if value else "unassigned"
    )
    working["zone_name"] = working["asset_id"].map(
        lambda value: ZONE_SPECS[value]["display_name"] if value in ZONE_SPECS else "Unknown"
    )

    working = working.sort_values(["asset_id", "timestamp"]).reset_index(drop=True)
    working["interval_minutes"] = _compute_intervals(working)
    working["energy_kwh"] = (
        working["power_w"] / 1000.0 * working["interval_minutes"] / 60.0
    )

    report.dropped_rows = dropped
    report.valid_rows = int(len(working))
    report.ambiguous_rows = int(working["ambiguous"].sum())
    report.zone_counts = {
        asset_id: int((working["asset_id"] == asset_id).sum())
        for asset_id in ZONE_SPECS
    }
    report.ambiguous_examples = (
        working.loc[working["ambiguous"], "ambiguous_reason"]
        .drop_duplicates()
        .head(5)
        .tolist()
    )

    standardized = working[
        STANDARD_COLUMNS + ["ambiguous_reason"]
    ].copy()
    return standardized, report


# ---------------------------------------------------------------------------
# Threshold classification
# ---------------------------------------------------------------------------


def classify_records(
    df: pd.DataFrame,
    carbon_factor: float,
) -> pd.DataFrame:
    """Apply per-zone thresholds and compute status, excess energy and carbon.

    A short rolling median dampens isolated spikes so a single bad reading does
    not raise an alert. Thresholds come from :mod:`core.config`.
    """
    enriched = df.copy()
    enriched = enriched.sort_values(["asset_id", "timestamp"]).reset_index(drop=True)

    threshold_w = []
    normal_max_w = []
    critical_w = []
    baseline_w = []
    for asset_id in enriched["asset_id"]:
        if asset_id in ZONE_THRESHOLDS:
            zone = get_threshold(asset_id)
            threshold_w.append(zone.warning_w)
            normal_max_w.append(zone.normal_max_w)
            critical_w.append(zone.critical_w)
            baseline_w.append(zone.baseline_power_w)
        else:
            threshold_w.append(np.nan)
            normal_max_w.append(np.nan)
            critical_w.append(np.nan)
            baseline_w.append(np.nan)

    enriched["threshold_w"] = threshold_w
    enriched["normal_max_w"] = normal_max_w
    enriched["critical_w"] = critical_w
    enriched["baseline_power_w"] = baseline_w

    enriched["smoothed_power_w"] = enriched.groupby("asset_id")["power_w"].transform(
        lambda values: values.rolling(SMOOTHING_WINDOW, min_periods=1).median()
    )

    def _status(row: pd.Series) -> str:
        if pd.isna(row["threshold_w"]):
            return "UNCLASSIFIED"
        if row["smoothed_power_w"] > row["critical_w"]:
            return "CRITICAL"
        if row["smoothed_power_w"] > row["threshold_w"]:
            return "WARNING"
        return "NORMAL"

    enriched["status"] = enriched.apply(_status, axis=1)

    enriched["excess_power_w"] = (
        enriched["smoothed_power_w"] - enriched["threshold_w"]
    ).clip(lower=0.0)
    enriched["excess_power_w"] = enriched["excess_power_w"].fillna(0.0)
    enriched["excess_energy_kwh"] = (
        enriched["excess_power_w"] / 1000.0
        * enriched["interval_minutes"] / 60.0
    )
    enriched["carbon_kg"] = enriched["energy_kwh"] * carbon_factor

    return enriched


# ---------------------------------------------------------------------------
# Zone analysis and recommendations
# ---------------------------------------------------------------------------


def summarize_zones(
    enriched: pd.DataFrame,
    tariff: float = 8.0,
) -> pd.DataFrame:
    """Aggregate the processed records into one analysis row per fixed zone."""
    rows: List[Dict[str, Any]] = []

    for asset_id, spec in ZONE_SPECS.items():
        group = enriched[enriched["asset_id"] == asset_id]
        threshold = get_threshold(asset_id)

        if group.empty:
            rows.append(
                {
                    "Zone": spec["display_name"],
                    "Asset ID": asset_id,
                    "Avg Power (W)": np.nan,
                    "Peak Power (W)": np.nan,
                    "Threshold (W)": threshold.warning_w,
                    "Energy (kWh)": 0.0,
                    "Baseline Energy (kWh)": 0.0,
                    "Excess Energy (kWh)": 0.0,
                    "Carbon (kg)": 0.0,
                    "Cost (₹)": 0.0,
                    "Warning Readings": 0,
                    "Critical Readings": 0,
                    "Status": "NO DATA",
                }
            )
            continue

        duration_hours = float(group["interval_minutes"].sum()) / 60.0
        warning_count = int((group["status"] == "WARNING").sum())
        critical_count = int((group["status"] == "CRITICAL").sum())

        if critical_count > 0:
            status = "CRITICAL"
        elif warning_count > 0:
            status = "WARNING"
        else:
            status = "NORMAL"

        energy_kwh = float(group["energy_kwh"].sum())
        rows.append(
            {
                "Zone": spec["display_name"],
                "Asset ID": asset_id,
                "Avg Power (W)": round(float(group["power_w"].mean()), 2),
                "Peak Power (W)": round(float(group["power_w"].max()), 2),
                "Threshold (W)": threshold.warning_w,
                "Energy (kWh)": round(energy_kwh, 4),
                "Baseline Energy (kWh)": round(
                    threshold.baseline_power_w / 1000.0 * duration_hours, 4
                ),
                "Excess Energy (kWh)": round(
                    float(group["excess_energy_kwh"].sum()), 4
                ),
                "Carbon (kg)": round(float(group["carbon_kg"].sum()), 4),
                "Cost (₹)": round(energy_kwh * tariff, 2),
                "Warning Readings": warning_count,
                "Critical Readings": critical_count,
                "Status": status,
            }
        )

    return pd.DataFrame(rows)


def overall_status(zone_summary: pd.DataFrame) -> str:
    """Return the highest severity across all zones."""
    statuses = set(zone_summary["Status"])
    if "CRITICAL" in statuses:
        return "CRITICAL"
    if "WARNING" in statuses:
        return "WARNING"
    if statuses == {"NO DATA"}:
        return "NO DATA"
    return "NORMAL"


def build_zone_recommendations(
    zone_summary: pd.DataFrame,
    carbon_factor: float,
) -> List[Dict[str, Any]]:
    """Generate explainable recommendations for every zone with violations.

    Reuses the existing candidate-action and recommendation engines. Without a
    Gemini key this yields deterministic, schema-validated fallbacks.
    """
    recommendations: List[Dict[str, Any]] = []

    for _, zone in zone_summary.iterrows():
        if zone["Status"] not in {"WARNING", "CRITICAL"}:
            continue

        asset_id = str(zone["Asset ID"])
        excess_kwh = float(zone["Excess Energy (kWh)"])
        candidates = [
            action.model_dump()
            for action in generate_candidate_actions(
                asset_id=asset_id,
                excess_kwh=excess_kwh,
                carbon_factor=carbon_factor,
            )
        ]
        twin_state = {
            "asset_id": asset_id,
            "state": "ACTIVE",
            "current_power_w": float(zone["Avg Power (W)"]),
            "baseline_power_w": get_threshold(asset_id).baseline_power_w,
            "occupancy_count": 0,
            "excess_kwh": excess_kwh,
            "co2e_kg": float(zone["Carbon (kg)"]),
            "waste_alert": True,
        }
        recommendation: AIRecommendation = get_ai_recommendation(
            twin_state=twin_state,
            candidate_actions=candidates,
            carbon_factor=carbon_factor,
        )

        threshold_w = float(zone["Threshold (W)"])
        peak_w = float(zone["Peak Power (W)"])
        breach = (
            "critical threshold"
            if zone["Status"] == "CRITICAL"
            else "warning threshold"
        )
        explanation = (
            f"{zone['Zone']} is {zone['Status']}: peak {peak_w:,.0f} W against a "
            f"{breach} of {threshold_w:,.0f} W, with "
            f"{int(zone['Warning Readings'])} warning and "
            f"{int(zone['Critical Readings'])} critical readings. "
            f"Excess consumption is {excess_kwh:.3f} kWh."
        )

        recommendations.append(
            {
                "asset_id": asset_id,
                "zone_name": str(zone["Zone"]),
                "status": str(zone["Status"]),
                "explanation": explanation,
                "recommendation": recommendation.model_dump(),
            }
        )

    return recommendations
