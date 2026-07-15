"""Zero-manual-label intensity-waveform event benchmark utilities."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


KT_TO_MS = 0.514444
FOLD_SEED = "20260715"
JEFFREYS_ALPHA = 0.5
BOOTSTRAP_SEED = 20260715
BOOTSTRAP_REPLICATES = 2000
SOURCE_COLUMNS = (
    "SID",
    "SEASON",
    "NAME",
    "ISO_TIME",
    "NATURE",
    "TRACK_TYPE",
    "DIST2LAND",
    "IFLAG",
    "USA_AGENCY",
    "USA_WIND",
)


@dataclass(frozen=True)
class EventBenchmark:
    predictions: pd.DataFrame
    summary: dict[str, Any]
    reliability: pd.DataFrame


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_event_source(
    path: Path,
    *,
    start_year: int = 2001,
    end_year: int = 2024,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the preregistered original JTWC 6-hourly tropical sequence."""

    frame = pd.read_csv(
        path,
        skiprows=[1],
        usecols=list(SOURCE_COLUMNS),
        low_memory=False,
    )
    frame.columns = frame.columns.str.strip()
    for column in ("SID", "NAME", "NATURE", "TRACK_TYPE", "IFLAG", "USA_AGENCY"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    for column in ("SEASON", "DIST2LAND", "USA_WIND"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["time"] = pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True)
    frame["usa_iflag"] = frame["IFLAG"].str.pad(1, side="right", fillchar="_").str[0]

    masks: list[tuple[str, pd.Series]] = [
        ("year_2001_2024", frame["SEASON"].between(start_year, end_year)),
        ("main_track", frame["TRACK_TYPE"].str.lower().eq("main")),
        (
            "synoptic_6h",
            frame["time"].notna()
            & frame["time"].dt.hour.isin((0, 6, 12, 18))
            & frame["time"].dt.minute.eq(0),
        ),
        ("jtwc_wp", frame["USA_AGENCY"].eq("jtwc_wp")),
        ("positive_wind", frame["USA_WIND"].gt(0)),
        ("original_or_verified", frame["usa_iflag"].isin(("O", "V"))),
        ("tropical_nature", frame["NATURE"].eq("TS")),
    ]
    selected = pd.Series(True, index=frame.index)
    selection_counts: dict[str, int] = {"raw_rows": int(len(frame))}
    for name, mask in masks:
        selected &= mask
        selection_counts[name] = int(selected.sum())

    source = frame.loc[selected].copy()
    duplicate_rows = int(source.duplicated(("SID", "time"), keep=False).sum())
    if duplicate_rows:
        source = source.sort_values(["SID", "time", "USA_WIND"]).drop_duplicates(
            ["SID", "time"], keep="last"
        )
    source = source.sort_values(["SID", "time"]).reset_index(drop=True)
    audit = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "start_year": start_year,
        "end_year": end_year,
        "wind_column": "USA_WIND",
        "wind_average_window_minutes": 1,
        "native_unit": "kt",
        "kt_to_ms": KT_TO_MS,
        "selection_counts": selection_counts,
        "selected_rows": int(len(source)),
        "selected_storms": int(source["SID"].nunique()),
        "duplicate_rows_before_resolution": duplicate_rows,
    }
    return source, audit


def build_five_point_windows(source: pd.DataFrame) -> pd.DataFrame:
    """Build exact t-12 through t+12 windows without bridging missing records."""

    ordered = source.sort_values(["SID", "time"]).reset_index(drop=True).copy()
    grouped = ordered.groupby("SID", sort=False)
    shifts = {"m12": 2, "m6": 1, "p6": -1, "p12": -2}
    for label, periods in shifts.items():
        ordered[f"time_{label}"] = grouped["time"].shift(periods)
        ordered[f"v_{label}_kt"] = grouped["USA_WIND"].shift(periods)

    ordered["v_0_kt"] = ordered["USA_WIND"]
    six_hours = pd.Timedelta(hours=6)
    exact = (
        (ordered["time"] - ordered["time_m12"]).eq(2 * six_hours)
        & (ordered["time"] - ordered["time_m6"]).eq(six_hours)
        & (ordered["time_p6"] - ordered["time"]).eq(six_hours)
        & (ordered["time_p12"] - ordered["time"]).eq(2 * six_hours)
    )
    windows = ordered.loc[exact].copy()
    for label in ("m12", "m6", "0", "p6", "p12"):
        windows[f"v_{label}_ms"] = windows[f"v_{label}_kt"] * KT_TO_MS
    windows["fold"] = windows["SID"].map(storm_fold)
    columns = [
        "SID",
        "SEASON",
        "NAME",
        "time",
        "DIST2LAND",
        "fold",
        *(f"v_{label}_kt" for label in ("m12", "m6", "0", "p6", "p12")),
        *(f"v_{label}_ms" for label in ("m12", "m6", "0", "p6", "p12")),
    ]
    return windows.loc[:, columns].reset_index(drop=True)


def storm_fold(sid: str, *, seed: str = FOLD_SEED, folds: int = 5) -> int:
    digest = hashlib.sha256(f"{seed}:{sid}".encode("ascii")).digest()
    return int.from_bytes(digest, "big") % folds


def label_waveform_events(windows: pd.DataFrame, threshold_ms: float) -> pd.DataFrame:
    if threshold_ms <= 0:
        raise ValueError("threshold_ms must be positive")
    result = windows.copy()
    result["future_drop_ms"] = result["v_0_ms"] - result["v_p6_ms"]
    result["future_rebound_ms"] = result["v_p12_ms"] - result["v_p6_ms"]
    result["past_drop_ms"] = result["v_m12_ms"] - result["v_m6_ms"]
    result["past_rebound_ms"] = result["v_0_ms"] - result["v_m6_ms"]
    result["event"] = (
        result["future_drop_ms"].ge(threshold_ms)
        & result["future_rebound_ms"].ge(threshold_ms)
    ).astype(int)
    result["past_event"] = (
        result["past_drop_ms"].ge(threshold_ms)
        & result["past_rebound_ms"].ge(threshold_ms)
    ).astype(int)
    result["threshold_ms"] = threshold_ms
    return result


def _jeffreys_rate(successes: int, total: int) -> float:
    return (successes + JEFFREYS_ALPHA) / (total + 2.0 * JEFFREYS_ALPHA)


def out_of_fold_probabilities(labelled: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    result = labelled.copy()
    result["p_climatology"] = np.nan
    result["p_persistence"] = np.nan
    fold_audit: list[dict[str, Any]] = []

    for fold in range(5):
        test = result["fold"].eq(fold)
        train = ~test
        if not test.any() or not train.any():
            raise ValueError(f"fold {fold} has an empty train or test partition")
        train_events = int(result.loc[train, "event"].sum())
        train_count = int(train.sum())
        p_clim = _jeffreys_rate(train_events, train_count)
        result.loc[test, "p_climatology"] = p_clim

        strata: dict[str, dict[str, Any]] = {}
        for history in (0, 1):
            stratum = train & result["past_event"].eq(history)
            successes = int(result.loc[stratum, "event"].sum())
            total = int(stratum.sum())
            probability = _jeffreys_rate(successes, total)
            result.loc[test & result["past_event"].eq(history), "p_persistence"] = probability
            strata[str(history)] = {
                "rows": total,
                "events": successes,
                "raw_rate": successes / total if total else None,
                "jeffreys_probability": probability,
            }
        fold_audit.append(
            {
                "fold": fold,
                "training_rows": train_count,
                "training_storms": int(result.loc[train, "SID"].nunique()),
                "test_rows": int(test.sum()),
                "test_storms": int(result.loc[test, "SID"].nunique()),
                "training_events": train_events,
                "training_raw_rate": train_events / train_count,
                "climatology_probability": p_clim,
                "persistence_strata": strata,
            }
        )

    if result[["p_climatology", "p_persistence"]].isna().any().any():
        raise RuntimeError("out-of-fold prediction left missing probabilities")
    return result, fold_audit


def _metric_interval(observed: float, samples: np.ndarray) -> dict[str, float]:
    lower, upper = np.quantile(samples, (0.025, 0.975))
    return {
        "value": float(observed),
        "ci95_low": float(lower),
        "ci95_high": float(upper),
    }


def storm_block_bootstrap(
    predictions: pd.DataFrame,
    *,
    replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, dict[str, float]]:
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    per_storm: list[tuple[int, int, float, float, int]] = []
    for _, group in predictions.groupby("SID", sort=True):
        event = group["event"].to_numpy(dtype=float)
        climate = group["p_climatology"].to_numpy(dtype=float)
        persistence = group["p_persistence"].to_numpy(dtype=float)
        per_storm.append(
            (
                len(group),
                int(event.sum()),
                float(np.square(climate - event).sum()),
                float(np.square(persistence - event).sum()),
                int(event.max()),
            )
        )
    block = np.asarray(per_storm, dtype=float)
    storm_count = len(block)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, storm_count, size=(replicates, storm_count))
    sampled = block[draws].sum(axis=1)
    row_count = sampled[:, 0]
    storm_denominator = float(storm_count)
    event_rate = sampled[:, 1] / row_count
    climate_brier = sampled[:, 2] / row_count
    persistence_brier = sampled[:, 3] / row_count
    brier_difference = persistence_brier - climate_brier
    brier_skill = 1.0 - persistence_brier / climate_brier
    event_storm_rate = sampled[:, 4] / storm_denominator

    event = predictions["event"].to_numpy(dtype=float)
    climate = predictions["p_climatology"].to_numpy(dtype=float)
    persistence = predictions["p_persistence"].to_numpy(dtype=float)
    climate_observed = float(np.square(climate - event).mean())
    persistence_observed = float(np.square(persistence - event).mean())
    storm_event_observed = float(predictions.groupby("SID")["event"].max().mean())
    return {
        "event_row_rate": _metric_interval(float(event.mean()), event_rate),
        "event_storm_rate": _metric_interval(storm_event_observed, event_storm_rate),
        "climatology_brier": _metric_interval(climate_observed, climate_brier),
        "persistence_brier": _metric_interval(persistence_observed, persistence_brier),
        "persistence_minus_climatology_brier": _metric_interval(
            persistence_observed - climate_observed, brier_difference
        ),
        "persistence_brier_skill": _metric_interval(
            1.0 - persistence_observed / climate_observed, brier_skill
        ),
    }


def reliability_table(predictions: pd.DataFrame) -> pd.DataFrame:
    tables: list[pd.DataFrame] = []
    for model, column in (
        ("climatology", "p_climatology"),
        ("persistence", "p_persistence"),
    ):
        grouped = (
            predictions.assign(_probability=predictions[column].round(12))
            .groupby("_probability", as_index=False)
            .agg(
                observed_rate=("event", "mean"),
                rows=("event", "size"),
                storms=("SID", "nunique"),
            )
            .rename(columns={"_probability": "predicted_probability"})
        )
        grouped.insert(0, "model", model)
        tables.append(grouped)
    return pd.concat(tables, ignore_index=True)


def run_event_benchmark(
    windows: pd.DataFrame,
    *,
    threshold_ms: float,
    subset_name: str,
    bootstrap_replicates: int = BOOTSTRAP_REPLICATES,
) -> EventBenchmark:
    if subset_name == "all_tropical":
        subset = windows.copy()
    elif subset_name == "intense":
        subset = windows.loc[windows["v_0_ms"].ge(33.0)].copy()
    elif subset_name == "intense_over_ocean":
        subset = windows.loc[
            windows["v_0_ms"].ge(33.0) & windows["DIST2LAND"].gt(0)
        ].copy()
    else:
        raise ValueError(f"unknown subset: {subset_name}")
    labelled = label_waveform_events(subset, threshold_ms)
    predictions, fold_audit = out_of_fold_probabilities(labelled)
    intervals = storm_block_bootstrap(
        predictions,
        replicates=bootstrap_replicates,
    )
    summary = {
        "subset": subset_name,
        "threshold_ms": threshold_ms,
        "rows": int(len(predictions)),
        "storms": int(predictions["SID"].nunique()),
        "events": int(predictions["event"].sum()),
        "event_storms": int(predictions.loc[predictions["event"].eq(1), "SID"].nunique()),
        "past_pattern_rows": int(predictions["past_event"].sum()),
        "effective_independent_units": {
            "unit": "storm SID",
            "count": int(predictions["SID"].nunique()),
        },
        "bootstrap": {
            "cluster_unit": "storm SID",
            "replicates": bootstrap_replicates,
            "seed": BOOTSTRAP_SEED,
        },
        "metrics": intervals,
        "folds": fold_audit,
    }
    return EventBenchmark(predictions, summary, reliability_table(predictions))


def concatenate_reliability(tables: Iterable[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(list(tables), ignore_index=True)
