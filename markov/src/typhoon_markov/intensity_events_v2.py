"""Development-selected intensity waveform labels with sealed temporal scoring."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from itertools import combinations
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .intensity_events import JEFFREYS_ALPHA, KT_TO_MS, storm_block_bootstrap


OFFSETS_HOURS = (-24, -18, -12, -6, 0, 6, 12, 18, 24)


@dataclass(frozen=True)
class LabelSelection:
    selected: dict[str, Any]
    candidates: pd.DataFrame
    target_achieved: bool


@dataclass(frozen=True)
class TemporalBenchmark:
    development: pd.DataFrame
    validation: pd.DataFrame
    training_audit: dict[str, Any]
    metrics: dict[str, Any]
    reliability: pd.DataFrame


def _offset_label(offset_hours: int) -> str:
    if offset_hours < 0:
        return f"m{abs(offset_hours)}"
    if offset_hours > 0:
        return f"p{offset_hours}"
    return "0"


def build_nine_point_windows(source: pd.DataFrame) -> pd.DataFrame:
    """Build exact t-24 through t+24 windows on one shared row set."""

    ordered = source.sort_values(["SID", "time"]).reset_index(drop=True).copy()
    grouped = ordered.groupby("SID", sort=False)
    exact = pd.Series(True, index=ordered.index)
    for offset in OFFSETS_HOURS:
        label = _offset_label(offset)
        periods = -(offset // 6)
        ordered[f"time_{label}"] = grouped["time"].shift(periods)
        ordered[f"v_{label}_kt"] = grouped["USA_WIND"].shift(periods)
        ordered[f"dist_{label}_km"] = grouped["DIST2LAND"].shift(periods)
        exact &= ordered[f"time_{label}"].eq(
            ordered["time"] + pd.Timedelta(hours=offset)
        )

    windows = ordered.loc[exact].copy()
    for offset in OFFSETS_HOURS:
        label = _offset_label(offset)
        windows[f"v_{label}_ms"] = windows[f"v_{label}_kt"] * KT_TO_MS
    columns = ["SID", "SEASON", "NAME", "time"]
    columns.extend(f"time_{_offset_label(value)}" for value in OFFSETS_HOURS)
    columns.extend(f"v_{_offset_label(value)}_kt" for value in OFFSETS_HOURS)
    columns.extend(f"v_{_offset_label(value)}_ms" for value in OFFSETS_HOURS)
    columns.extend(f"dist_{_offset_label(value)}_km" for value in OFFSETS_HOURS)
    return windows.loc[:, columns].reset_index(drop=True)


def _waveform_mask(
    frame: pd.DataFrame,
    offsets: Sequence[int],
    threshold_ms: float,
) -> pd.Series:
    if len(offsets) < 3:
        raise ValueError("waveform event requires at least three time points")
    event = pd.Series(False, index=frame.index)
    for first, valley, last in combinations(offsets, 3):
        first_values = frame[f"v_{_offset_label(first)}_ms"]
        valley_values = frame[f"v_{_offset_label(valley)}_ms"]
        last_values = frame[f"v_{_offset_label(last)}_ms"]
        event |= (first_values - valley_values).ge(threshold_ms) & (
            last_values - valley_values
        ).ge(threshold_ms)
    return event


def label_candidate(
    windows: pd.DataFrame,
    *,
    horizon_hours: int,
    threshold_ms: float,
    minimum_initial_intensity_ms: float,
    minimum_future_distance_km: float,
) -> pd.DataFrame:
    if horizon_hours not in (12, 18, 24):
        raise ValueError("horizon_hours must be one of 12, 18, or 24")
    if threshold_ms <= 0:
        raise ValueError("threshold_ms must be positive")
    future_offsets = tuple(range(0, horizon_hours + 1, 6))
    past_offsets = tuple(range(-horizon_hours, 1, 6))
    future_at_sea = pd.concat(
        [
            windows[f"dist_{_offset_label(offset)}_km"].gt(
                minimum_future_distance_km
            )
            for offset in future_offsets
        ],
        axis=1,
    ).all(axis=1)
    domain = windows["v_0_ms"].ge(minimum_initial_intensity_ms) & future_at_sea
    result = windows.loc[domain].copy()
    result["event"] = _waveform_mask(
        result, future_offsets, threshold_ms
    ).astype(int)
    result["past_event"] = _waveform_mask(
        result, past_offsets, threshold_ms
    ).astype(int)
    result["horizon_hours"] = horizon_hours
    result["threshold_ms"] = threshold_ms
    return result


def _event_vector_sha256(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    for row in frame.sort_values(["SID", "time"]).itertuples():
        token = f"{row.SID}|{row.time.isoformat()}|{int(row.event)}\n"
        digest.update(token.encode("ascii"))
    return digest.hexdigest()


def select_development_label(
    windows: pd.DataFrame,
    *,
    horizons: Sequence[int],
    thresholds_ms: Sequence[float],
    development_seasons: tuple[int, int],
    minimum_initial_intensity_ms: float,
    minimum_future_distance_km: float,
    target_interval: tuple[float, float],
    target_midpoint: float,
    bootstrap_replicates: int,
    bootstrap_seed: int,
) -> LabelSelection:
    rows: list[dict[str, Any]] = []
    for horizon in horizons:
        for threshold in thresholds_ms:
            labelled = label_candidate(
                windows,
                horizon_hours=int(horizon),
                threshold_ms=float(threshold),
                minimum_initial_intensity_ms=minimum_initial_intensity_ms,
                minimum_future_distance_km=minimum_future_distance_km,
            )
            development = labelled.loc[
                labelled["SEASON"].between(*development_seasons)
            ].copy()
            if development.empty:
                raise ValueError("candidate produced an empty development set")
            events = int(development["event"].sum())
            rate = events / len(development)
            rate_intervals = _rate_intervals(
                development,
                replicates=bootstrap_replicates,
                seed=bootstrap_seed,
            )
            rows.append(
                {
                    "horizon_hours": int(horizon),
                    "threshold_ms": float(threshold),
                    "rows": int(len(development)),
                    "storms": int(development["SID"].nunique()),
                    "events": events,
                    "event_storms": int(
                        development.loc[development["event"].eq(1), "SID"].nunique()
                    ),
                    "event_row_rate": rate,
                    "event_row_rate_ci95_low": rate_intervals["event_row_rate"][
                        "ci95_low"
                    ],
                    "event_row_rate_ci95_high": rate_intervals["event_row_rate"][
                        "ci95_high"
                    ],
                    "event_storm_rate": rate_intervals["event_storm_rate"]["value"],
                    "event_storm_rate_ci95_low": rate_intervals["event_storm_rate"][
                        "ci95_low"
                    ],
                    "event_storm_rate_ci95_high": rate_intervals["event_storm_rate"][
                        "ci95_high"
                    ],
                    "distance_from_target_midpoint": abs(rate - target_midpoint),
                    "in_target_interval": target_interval[0]
                    <= rate
                    <= target_interval[1],
                    "event_vector_sha256": _event_vector_sha256(development),
                }
            )
    candidates = pd.DataFrame(rows)
    eligible = candidates.loc[candidates["in_target_interval"]].copy()
    target_achieved = not eligible.empty
    ranked = eligible if target_achieved else candidates
    selected = (
        ranked.sort_values(
            ["distance_from_target_midpoint", "threshold_ms", "horizon_hours"],
            ascending=[True, False, True],
            kind="mergesort",
        )
        .iloc[0]
        .to_dict()
    )
    selected["selection_qualification"] = (
        "target-achieved" if target_achieved else "diagnostic-only-target-failed"
    )
    return LabelSelection(selected, candidates, target_achieved)


def _jeffreys_rate(successes: int, total: int, alpha: float) -> float:
    return (successes + alpha) / (total + 2.0 * alpha)


def _rate_intervals(
    frame: pd.DataFrame,
    *,
    replicates: int,
    seed: int,
) -> dict[str, dict[str, float]]:
    groups = [group for _, group in frame.groupby("SID", sort=True)]
    storm_count = len(groups)
    if storm_count < 2:
        raise ValueError("rate interval requires at least two storms")
    blocks = np.asarray(
        [
            (len(group), int(group["event"].sum()), int(group["event"].max()))
            for group in groups
        ],
        dtype=float,
    )
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, storm_count, size=(replicates, storm_count))
    sampled = blocks[draws].sum(axis=1)
    row_rates = sampled[:, 1] / sampled[:, 0]
    storm_rates = sampled[:, 2] / storm_count

    def metric(value: float, samples: np.ndarray) -> dict[str, float]:
        lower, upper = np.quantile(samples, (0.025, 0.975))
        return {
            "value": float(value),
            "ci95_low": float(lower),
            "ci95_high": float(upper),
        }

    return {
        "event_row_rate": metric(float(frame["event"].mean()), row_rates),
        "event_storm_rate": metric(
            float(frame.groupby("SID")["event"].max().mean()), storm_rates
        ),
    }


def _validation_reliability(predictions: pd.DataFrame) -> pd.DataFrame:
    tables: list[pd.DataFrame] = []
    for model, column in (
        ("climatology", "p_climatology"),
        ("persistence", "p_persistence"),
    ):
        table = (
            predictions.assign(_probability=predictions[column].round(12))
            .groupby("_probability", as_index=False)
            .agg(
                observed_rate=("event", "mean"),
                rows=("event", "size"),
                storms=("SID", "nunique"),
                events=("event", "sum"),
            )
            .rename(columns={"_probability": "predicted_probability"})
        )
        table.insert(0, "model", model)
        tables.append(table)
    return pd.concat(tables, ignore_index=True)


def run_temporal_benchmark(
    labelled: pd.DataFrame,
    *,
    development_seasons: tuple[int, int],
    validation_seasons: tuple[int, int],
    alpha: float = JEFFREYS_ALPHA,
    bootstrap_replicates: int,
    bootstrap_seed: int,
) -> TemporalBenchmark:
    development = labelled.loc[
        labelled["SEASON"].between(*development_seasons)
    ].copy()
    validation = labelled.loc[
        labelled["SEASON"].between(*validation_seasons)
    ].copy()
    if development.empty or validation.empty:
        raise ValueError("development and validation must both contain rows")
    overlap = set(development["SID"]) & set(validation["SID"])
    if overlap:
        raise ValueError(f"storm leakage across temporal split: {sorted(overlap)}")

    development_events = int(development["event"].sum())
    p_climatology = _jeffreys_rate(
        development_events, len(development), alpha
    )
    persistence: dict[int, float] = {}
    strata: dict[str, dict[str, Any]] = {}
    for history in (0, 1):
        selected = development.loc[development["past_event"].eq(history)]
        if selected.empty:
            raise ValueError(f"empty development persistence stratum: {history}")
        events = int(selected["event"].sum())
        probability = _jeffreys_rate(events, len(selected), alpha)
        persistence[history] = probability
        strata[str(history)] = {
            "rows": int(len(selected)),
            "storms": int(selected["SID"].nunique()),
            "events": events,
            "raw_rate": events / len(selected),
            "jeffreys_probability": probability,
        }

    validation["p_climatology"] = p_climatology
    validation["p_persistence"] = validation["past_event"].map(persistence)
    if validation["p_persistence"].isna().any():
        raise RuntimeError("validation contains an unknown persistence stratum")

    brier = storm_block_bootstrap(
        validation,
        replicates=bootstrap_replicates,
        seed=bootstrap_seed,
    )
    development_rates = _rate_intervals(
        development, replicates=bootstrap_replicates, seed=bootstrap_seed
    )
    validation_rates = _rate_intervals(
        validation, replicates=bootstrap_replicates, seed=bootstrap_seed
    )
    unique_validation_classes = sorted(validation["event"].unique().tolist())
    probability_difference = abs(persistence[1] - persistence[0])
    training_audit = {
        "development_rows": int(len(development)),
        "development_storms": int(development["SID"].nunique()),
        "development_events": development_events,
        "climatology_probability": p_climatology,
        "persistence_strata": strata,
        "persistence_probability_absolute_difference": probability_difference,
        "persistence_degenerate_to_six_decimals": round(persistence[0], 6)
        == round(persistence[1], 6),
    }
    metrics = {
        "development_rates": development_rates,
        "validation_rates": validation_rates,
        "validation_rows": int(len(validation)),
        "validation_storms": int(validation["SID"].nunique()),
        "validation_events": int(validation["event"].sum()),
        "validation_event_storms": int(
            validation.loc[validation["event"].eq(1), "SID"].nunique()
        ),
        "validation_classes": unique_validation_classes,
        "nondegenerate_validation_classes": unique_validation_classes == [0, 1],
        "brier": brier,
    }
    return TemporalBenchmark(
        development,
        validation,
        training_audit,
        metrics,
        _validation_reliability(validation),
    )


def quantization_audit(source: pd.DataFrame) -> dict[str, Any]:
    wind = source["USA_WIND"].dropna().to_numpy(dtype=float)
    nearest_five = np.round(wind / 5.0) * 5.0
    multiple_of_five = np.isclose(wind, nearest_five, atol=1e-9)
    return {
        "rows": int(len(wind)),
        "wind_average_window_minutes": 1,
        "native_unit": "kt",
        "multiple_of_5kt_rows": int(multiple_of_five.sum()),
        "multiple_of_5kt_fraction": float(multiple_of_five.mean()),
        "minimum_positive_native_increment_ms": 5.0 * KT_TO_MS,
    }
