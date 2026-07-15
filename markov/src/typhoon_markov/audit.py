"""Identifiability and preregistered falsification gates."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Sequence

from .model import FREE_PARAMETER_NAMES, OBSERVATION_CHANNELS


@dataclass(frozen=True)
class AuditReport:
    passed: bool
    parameter_count: int
    observation_channel_count: int
    effective_observation_dimension: float
    scalar_observation_count: int
    sensitivity_rank: int
    training_storm_count: int
    holdout_storm_count: int
    checks: dict[str, bool]
    diagnostics: dict[str, bool]


@dataclass(frozen=True)
class HindcastMetrics:
    wind_mae_ms: float
    pressure_mae_hpa: float
    rmw_mae_km: float
    landfall_wind_mae_ms: float
    ri_brier_score: float
    central_80_interval_coverage: float
    physical_qc_failures: int


@dataclass(frozen=True)
class FalsificationReport:
    passed: bool
    checks: dict[str, bool]


def matrix_rank(matrix: Sequence[Sequence[float]], tolerance: float = 1.0e-10) -> int:
    """Compute rank by partial-pivot Gaussian elimination."""

    work = [[float(value) for value in row] for row in matrix]
    if not work:
        return 0
    width = len(work[0])
    if width == 0 or any(len(row) != width for row in work):
        raise ValueError("sensitivity matrix must be rectangular and non-empty")
    if any(not math.isfinite(value) for row in work for value in row):
        raise ValueError("sensitivity matrix must contain finite values")

    rank = 0
    for column in range(width):
        pivot = max(range(rank, len(work)), key=lambda row: abs(work[row][column]))
        if abs(work[pivot][column]) <= tolerance:
            continue
        work[rank], work[pivot] = work[pivot], work[rank]
        pivot_value = work[rank][column]
        for index in range(column, width):
            work[rank][index] /= pivot_value
        for row in range(len(work)):
            if row == rank:
                continue
            factor = work[row][column]
            for index in range(column, width):
                work[row][index] -= factor * work[rank][index]
        rank += 1
        if rank == len(work) or rank == width:
            break
    return rank


def audit_identifiability(
    sensitivity_matrix: Sequence[Sequence[float]],
    *,
    effective_observation_dimension: float,
    observation_error_covariance_registered: bool,
    scalar_observation_count: int,
    training_storm_ids: Iterable[str],
    holdout_storm_ids: Iterable[str],
) -> AuditReport:
    """Apply sample, whitened-sensitivity-rank, covariance, and leakage gates.

    The caller must whiten the sensitivity matrix with a registered observation-error
    covariance and scale parameter columns according to the preregistered parameter
    scales. Cross-channel effective dimension is retained as a redundancy diagnostic.
    """

    training = set(training_storm_ids)
    holdout = set(holdout_storm_ids)
    parameter_count = len(FREE_PARAMETER_NAMES)
    channel_count = len(OBSERVATION_CHANNELS)
    if not math.isfinite(effective_observation_dimension) or not (
        0.0 < effective_observation_dimension <= channel_count
    ):
        raise ValueError(
            "effective_observation_dimension must lie within (0, channel count]"
        )
    rank = matrix_rank(sensitivity_matrix)
    checks = {
        "observation_error_covariance_registered": (
            observation_error_covariance_registered
        ),
        "at_least_20_scalar_observations_per_parameter": scalar_observation_count
        >= 20 * parameter_count,
        "full_whitened_parameter_sensitivity_rank": rank == parameter_count,
        "at_least_30_training_storms": len(training) >= 30,
        "at_least_10_holdout_storms": len(holdout) >= 10,
        "storm_disjoint_split": training.isdisjoint(holdout),
    }
    diagnostics = {
        "parameter_count_below_raw_field_count": parameter_count < channel_count,
        "observation_channels_are_redundant": (
            effective_observation_dimension < channel_count
        ),
        "effective_dimension_below_parameter_count": (
            effective_observation_dimension < parameter_count
        ),
    }
    return AuditReport(
        passed=all(checks.values()),
        parameter_count=parameter_count,
        observation_channel_count=channel_count,
        effective_observation_dimension=effective_observation_dimension,
        scalar_observation_count=scalar_observation_count,
        sensitivity_rank=rank,
        training_storm_count=len(training),
        holdout_storm_count=len(holdout),
        checks=checks,
        diagnostics=diagnostics,
    )


def evaluate_falsification(
    model: HindcastMetrics,
    persistence_baseline: HindcastMetrics,
    climatology_ri_brier_score: float,
) -> FalsificationReport:
    """Reject a calibration that misses any preregistered holdout criterion."""

    checks = {
        "wind_beats_persistence_by_5_percent": model.wind_mae_ms
        <= 0.95 * persistence_baseline.wind_mae_ms,
        "pressure_beats_persistence": model.pressure_mae_hpa
        < persistence_baseline.pressure_mae_hpa,
        "rmw_beats_persistence": model.rmw_mae_km < persistence_baseline.rmw_mae_km,
        "landfall_wind_beats_persistence": model.landfall_wind_mae_ms
        < persistence_baseline.landfall_wind_mae_ms,
        "ri_probability_beats_climatology": model.ri_brier_score
        < climatology_ri_brier_score,
        "central_80_interval_is_calibrated": 0.72
        <= model.central_80_interval_coverage
        <= 0.88,
        "zero_physical_qc_failures": model.physical_qc_failures == 0,
    }
    return FalsificationReport(passed=all(checks.values()), checks=checks)
