"""Native-channel case scoring and model-eligibility audits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .model import (
    Forcing,
    MarkovParameters,
    PhysicalConstants,
    Regime,
    State,
    state_tendencies,
)


KNOT_TO_MS = 0.5144444444444445


class ObservationContractError(ValueError):
    """Raised when a wind record violates the registered observation contract."""


@dataclass(frozen=True)
class NativeWindScore:
    agency: str
    averaging_period_seconds: int
    native_unit: str
    lead_hours: float
    initial_wind_native: float
    forecast_wind_native: float
    observed_wind_native: float
    forecast_change_native: float
    observed_change_native: float
    forecast_error_native: float
    absolute_error_native: float
    forecast_change_ms: float
    observed_change_ms: float
    forecast_error_ms: float
    forecast_direction: str
    observed_direction: str
    event_direction_captured: bool


@dataclass(frozen=True)
class RegimeWindInfluenceReport:
    influences_wind_tendency: bool
    wind_tendency_ms_per_day: dict[str, float]
    maximum_pairwise_difference_ms_per_day: float
    tolerance_ms_per_day: float


@dataclass(frozen=True)
class ModelEligibilityReport:
    eligible: bool
    verdict: str
    checks: dict[str, bool]
    missing_artifacts: tuple[str, ...]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def wind_to_ms(value: float, unit: str) -> float:
    if unit == "m/s":
        return float(value)
    if unit == "kt":
        return float(value) * KNOT_TO_MS
    raise ObservationContractError(f"unsupported wind unit: {unit}")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ObservationContractError(f"timestamp must include UTC offset: {value}")
    return parsed


def _direction(value: float, tolerance: float = 1.0e-12) -> str:
    if value > tolerance:
        return "intensifying"
    if value < -tolerance:
        return "weakening"
    return "quasi_steady"


def _validate_record(
    record: dict[str, Any],
    required_fields: Iterable[str],
    agency_contract: dict[str, Any],
) -> None:
    missing = [field for field in required_fields if field not in record]
    if missing:
        raise ObservationContractError(
            f"{record.get('stage', 'record')} is missing fields: {', '.join(missing)}"
        )
    expected_window = agency_contract["wind_averaging_period_seconds"]
    expected_height = agency_contract["wind_height_m"]
    if record["wind_averaging_period_seconds"] != expected_window:
        raise ObservationContractError(
            f"{record['agency']} averaging period must be {expected_window} seconds"
        )
    if record["wind_height_m"] != expected_height:
        raise ObservationContractError(
            f"{record['agency']} wind height must be {expected_height} m"
        )
    expected_ms = wind_to_ms(record["wind_value_native"], record["wind_unit_native"])
    if not math.isclose(record["wind_value_ms"], expected_ms, abs_tol=1.0e-9):
        raise ObservationContractError(
            f"{record['agency']} canonical wind conversion is inconsistent"
        )
    _parse_utc(record["issued_at_utc"])
    _parse_utc(record["valid_at_utc"])


def score_native_channel(
    channel: dict[str, Any], contract: dict[str, Any]
) -> NativeWindScore:
    """Score one forecast against a later analysis without changing wind channel."""

    agency = channel["agency"]
    try:
        agency_contract = contract["agencies"][agency]
    except KeyError as error:
        raise ObservationContractError(f"unknown agency contract: {agency}") from error

    records = [channel[name] for name in ("initial_analysis", "forecast", "truth")]
    for record in records:
        _validate_record(record, contract["required_record_fields"], agency_contract)
        if record["agency"] != agency:
            raise ObservationContractError("all native-channel records must share agency")

    signatures = {
        (
            record["wind_unit_native"],
            record["wind_averaging_period_seconds"],
            record["wind_height_m"],
        )
        for record in records
    }
    if len(signatures) != 1:
        raise ObservationContractError(
            "native-channel scoring requires one wind unit, averaging period, and height"
        )

    initial, forecast, truth = records
    if forecast["valid_at_utc"] != truth["valid_at_utc"]:
        raise ObservationContractError("forecast and truth valid times must match")
    initial_time = _parse_utc(initial["valid_at_utc"])
    target_time = _parse_utc(truth["valid_at_utc"])
    lead_hours = (target_time - initial_time).total_seconds() / 3_600.0
    if lead_hours <= 0.0:
        raise ObservationContractError("target time must follow initial analysis time")

    initial_value = float(initial["wind_value_native"])
    forecast_value = float(forecast["wind_value_native"])
    truth_value = float(truth["wind_value_native"])
    forecast_change = forecast_value - initial_value
    observed_change = truth_value - initial_value
    error = forecast_value - truth_value
    unit = initial["wind_unit_native"]
    forecast_change_ms = wind_to_ms(forecast_change, unit)
    observed_change_ms = wind_to_ms(observed_change, unit)
    error_ms = wind_to_ms(error, unit)
    forecast_direction = _direction(forecast_change)
    observed_direction = _direction(observed_change)

    return NativeWindScore(
        agency=agency,
        averaging_period_seconds=initial["wind_averaging_period_seconds"],
        native_unit=unit,
        lead_hours=lead_hours,
        initial_wind_native=initial_value,
        forecast_wind_native=forecast_value,
        observed_wind_native=truth_value,
        forecast_change_native=forecast_change,
        observed_change_native=observed_change,
        forecast_error_native=error,
        absolute_error_native=abs(error),
        forecast_change_ms=forecast_change_ms,
        observed_change_ms=observed_change_ms,
        forecast_error_ms=error_ms,
        forecast_direction=forecast_direction,
        observed_direction=observed_direction,
        event_direction_captured=forecast_direction == observed_direction,
    )


def require_cross_channel_operator(
    left_record: dict[str, Any],
    right_record: dict[str, Any],
    observation_operator: dict[str, Any] | None = None,
) -> None:
    """Block cross-definition scores until a traceable uncertain operator exists."""

    same_channel = all(
        left_record[key] == right_record[key]
        for key in (
            "agency",
            "wind_unit_native",
            "wind_averaging_period_seconds",
            "wind_height_m",
        )
    )
    if same_channel:
        return
    required = {
        "operator_id",
        "source_channel",
        "target_channel",
        "uncertainty_model",
        "validation_dataset",
    }
    if observation_operator is None or not required.issubset(observation_operator):
        raise ObservationContractError(
            "cross-channel scoring requires a registered observation operator "
            "with uncertainty and validation metadata"
        )


def audit_regime_wind_influence(
    probes: Iterable[tuple[State, Forcing]],
    constants: PhysicalConstants,
    *,
    tolerance_ms_per_day: float = 1.0e-9,
) -> RegimeWindInfluenceReport:
    """Run counterfactual derivatives to test whether Z changes dV/dt."""

    values: dict[str, list[float]] = {regime.name.lower(): [] for regime in Regime}
    for state, forcing in probes:
        for regime in Regime:
            derivative = state_tendencies(state, regime, forcing, constants)
            values[regime.name.lower()].append(derivative[0] * 86_400.0)

    flattened = [value for samples in values.values() for value in samples]
    if not flattened:
        raise ValueError("at least one structural probe is required")
    maximum_difference = 0.0
    for probe_index in range(len(next(iter(values.values())))):
        probe_values = [samples[probe_index] for samples in values.values()]
        maximum_difference = max(
            maximum_difference, max(probe_values) - min(probe_values)
        )
    representative = {name: samples[0] for name, samples in values.items()}
    return RegimeWindInfluenceReport(
        influences_wind_tendency=maximum_difference > tolerance_ms_per_day,
        wind_tendency_ms_per_day=representative,
        maximum_pairwise_difference_ms_per_day=maximum_difference,
        tolerance_ms_per_day=tolerance_ms_per_day,
    )


def evaluate_model_eligibility(
    project_root: Path,
    model_config: dict[str, Any],
    case: dict[str, Any],
    structural_report: RegimeWindInfluenceReport,
) -> ModelEligibilityReport:
    """Apply structural, calibration, provenance, and artifact gates before a case run."""

    required = case["model_evaluation"]["required_artifacts"]
    missing = tuple(
        item["id"] for item in required if not (project_root / item["path"]).is_file()
    )
    fitted_parameters = all(
        details.get("status") == "fitted"
        for details in model_config["free_parameters"].values()
    )
    identifiability_status = model_config["parameter_budget"].get(
        "formal_identifiability_audit_status", "not_run"
    )
    checks = {
        "regime_changes_wind_tendency": structural_report.influences_wind_tendency,
        "formal_identifiability_audit_passed": identifiability_status == "passed",
        "model_status_is_research_accepted": model_config["status"]
        == "research-accepted",
        "all_free_parameters_are_fitted": fitted_parameters,
        "authoritative_forecast_enabled": bool(
            model_config["authoritative_forecast_enabled"]
        ),
        "all_case_artifacts_exist": not missing,
    }
    if not checks["regime_changes_wind_tendency"]:
        verdict = "structural-fail-regime-has-zero-wind-effect"
    elif not all(checks.values()):
        verdict = "ineligible-prerequisites-incomplete"
    else:
        verdict = "eligible-for-case-integration"
    return ModelEligibilityReport(
        eligible=all(checks.values()),
        verdict=verdict,
        checks=checks,
        missing_artifacts=missing,
    )


def serialize_dataclass(value: Any) -> dict[str, Any]:
    return asdict(value)
