from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .data import KT_TO_MS
from .stats import (
    bootstrap_correlation_matrix,
    cluster_draws,
    factorize_clusters,
    fit_cluster_bootstrap_regression,
)

REFERENCE_PRESSURE_HPA = 1010.0
BOOTSTRAP_SEED = 20260715

_COLUMNS = (
    "SID",
    "SEASON",
    "ISO_TIME",
    "TRACK_TYPE",
    "IFLAG",
    "USA_AGENCY",
    "USA_WIND",
    "USA_PRES",
    "USA_RMW",
)


def load_wind_pressure_samples(
    path: Path,
    *,
    start_year: int = 2001,
    end_year: int = 2024,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Load the preregistered primary sample and the legacy V/Pc/RMW audit sample."""

    frame = pd.read_csv(path, skiprows=[1], usecols=list(_COLUMNS), low_memory=False)
    frame.columns = frame.columns.str.strip()
    for column in ("SID", "ISO_TIME", "TRACK_TYPE", "IFLAG", "USA_AGENCY"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    for column in ("SEASON", "USA_WIND", "USA_PRES", "USA_RMW"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["time"] = pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True)
    period_main = frame["SEASON"].between(start_year, end_year) & frame[
        "TRACK_TYPE"
    ].str.lower().eq("main")
    positive_vp = frame["USA_WIND"].gt(0) & frame["USA_PRES"].gt(0)

    legacy_all = frame.loc[period_main & positive_vp & frame["USA_RMW"].gt(0)].copy()
    legacy = legacy_all.loc[legacy_all["USA_AGENCY"].eq("jtwc_wp")].copy()
    legacy["wind_ms"] = legacy["USA_WIND"] * KT_TO_MS
    legacy["pressure_hpa"] = legacy["USA_PRES"]

    synoptic = (
        frame["time"].notna()
        & frame["time"].dt.hour.isin((0, 6, 12, 18))
        & frame["time"].dt.minute.eq(0)
    )
    usa_original = frame["IFLAG"].str[:1].isin(("O", "V"))
    primary_mask = (
        period_main
        & positive_vp
        & synoptic
        & usa_original
        & frame["USA_AGENCY"].eq("jtwc_wp")
    )
    primary = frame.loc[primary_mask].copy()
    duplicate_count = int(primary.duplicated(("SID", "time"), keep=False).sum())
    if duplicate_count:
        raise ValueError(
            f"Primary wind-pressure sample has {duplicate_count} duplicate SID/time rows"
        )
    primary["wind_ms"] = primary["USA_WIND"] * KT_TO_MS
    primary["pressure_hpa"] = primary["USA_PRES"]
    primary["pressure_deficit_hpa"] = REFERENCE_PRESSURE_HPA - primary["pressure_hpa"]
    primary = primary.sort_values(["SID", "time"]).reset_index(drop=True)

    audit = {
        "source_rows": int(len(frame)),
        "primary_rows": int(len(primary)),
        "primary_storms": int(primary["SID"].nunique()),
        "primary_duplicate_sid_time_rows": duplicate_count,
        "legacy_all_agencies_complete_v_pc_rmw_rows": int(len(legacy_all)),
        "legacy_all_agencies_complete_v_pc_rmw_storms": int(legacy_all["SID"].nunique()),
        "legacy_complete_v_pc_rmw_rows": int(len(legacy)),
        "legacy_complete_v_pc_rmw_storms": int(legacy["SID"].nunique()),
        "legacy_complete_v_pc_rmw_agency_filter": "USA_AGENCY=jtwc_wp",
    }
    return primary, legacy.reset_index(drop=True), audit


def _regression_residual_scale(
    x: np.ndarray,
    y: np.ndarray,
    storm_ids: Sequence[str],
    bootstrap_beta: np.ndarray,
    *,
    replicates: int,
    seed: int,
) -> tuple[float, np.ndarray]:
    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    column_count = x.shape[1]
    xtx = np.zeros((len(clusters), column_count, column_count), dtype=float)
    xty = np.zeros((len(clusters), column_count), dtype=float)
    yty = np.zeros(len(clusters), dtype=float)
    counts = np.zeros(len(clusters), dtype=float)
    for cluster in range(len(clusters)):
        selected = labels == cluster
        xc = x[selected]
        yc = y[selected]
        xtx[cluster] = xc.T @ xc
        xty[cluster] = xc.T @ yc
        yty[cluster] = yc @ yc
        counts[cluster] = selected.sum()

    boot_xtx = np.tensordot(draws, xtx, axes=(1, 0))
    boot_xty = draws @ xty
    boot_yty = draws @ yty
    boot_n = draws @ counts
    linear = np.einsum("ri,ri->r", bootstrap_beta, boot_xty)
    quadratic = np.einsum("ri,rij,rj->r", bootstrap_beta, boot_xtx, bootstrap_beta)
    rss = np.maximum(boot_yty - 2.0 * linear + quadratic, 0.0)
    scale = np.sqrt(rss / np.maximum(boot_n - column_count, 1.0))

    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ beta
    point = float(np.sqrt((residual @ residual) / (len(y) - column_count)))
    return point, scale


def fit_relation(
    predictor: np.ndarray,
    response: np.ndarray,
    storm_ids: Sequence[str],
    *,
    predictor_name: str,
    response_name: str,
    replicates: int = 2000,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    predictor = np.asarray(predictor, dtype=float)
    response = np.asarray(response, dtype=float)
    x = np.column_stack((np.ones(len(predictor)), predictor))
    fit = fit_cluster_bootstrap_regression(
        x,
        response,
        storm_ids,
        replicates=replicates,
        seed=seed,
    )
    point_scale, boot_scale = _regression_residual_scale(
        x,
        response,
        storm_ids,
        np.asarray(fit["bootstrap_beta"]),
        replicates=replicates,
        seed=seed,
    )
    beta = np.asarray(fit["beta"])
    fitted = x @ beta
    residual = response - fitted
    scale_interval = np.nanpercentile(boot_scale, (2.5, 97.5))
    return {
        "predictor": predictor_name,
        "response": response_name,
        "intercept": float(beta[0]),
        "intercept_95ci": [float(fit["lower"][0]), float(fit["upper"][0])],
        "slope": float(beta[1]),
        "slope_95ci": [float(fit["lower"][1]), float(fit["upper"][1])],
        "residual_scale": point_scale,
        "residual_scale_95ci": [float(scale_interval[0]), float(scale_interval[1])],
        "fitted": fitted,
        "residual": residual,
    }


def diagnose_wind_pressure(
    frame: pd.DataFrame,
    *,
    replicates: int = 2000,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    wind = frame["wind_ms"].to_numpy(float)
    pressure = frame["pressure_hpa"].to_numpy(float)
    deficit = frame["pressure_deficit_hpa"].to_numpy(float)
    storm_ids = frame["SID"].astype(str).to_numpy()
    correlation = bootstrap_correlation_matrix(
        np.column_stack((wind, pressure)),
        storm_ids,
        replicates=replicates,
        seed=seed + 1,
    )
    direct = fit_relation(
        deficit,
        wind,
        storm_ids,
        predictor_name="1010_minus_pc_hpa",
        response_name="wind_1min_ms",
        replicates=replicates,
        seed=seed + 2,
    )
    inverse = fit_relation(
        wind,
        pressure,
        storm_ids,
        predictor_name="wind_1min_ms",
        response_name="pc_hpa",
        replicates=replicates,
        seed=seed + 3,
    )
    return {
        "rows": int(len(frame)),
        "storms": int(frame["SID"].nunique()),
        "wind_pressure_pearson_r": float(correlation["point"][0, 1]),
        "wind_pressure_pearson_r_95ci": [
            float(correlation["lower"][0, 1]),
            float(correlation["upper"][0, 1]),
        ],
        "wind_from_pressure": direct,
        "pressure_from_wind": inverse,
    }


def _storm_folds(storm_ids: Sequence[str], folds: int, seed: int) -> dict[str, int]:
    unique = sorted(set(str(value) for value in storm_ids))
    ranked = sorted(
        unique,
        key=lambda sid: hashlib.sha256(f"{seed}:{sid}".encode("ascii")).hexdigest(),
    )
    return {sid: index % folds for index, sid in enumerate(ranked)}


def error_metrics(observed: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = np.asarray(predicted, dtype=float) - np.asarray(observed, dtype=float)
    absolute = np.abs(error)
    return {
        "mae_ms": float(np.mean(absolute)),
        "rmse_ms": float(np.sqrt(np.mean(error**2))),
        "bias_ms": float(np.mean(error)),
        "residual_sd_ms": float(np.std(error, ddof=1)),
        "absolute_error_p80_ms": float(np.percentile(absolute, 80)),
        "absolute_error_p95_ms": float(np.percentile(absolute, 95)),
    }


def cross_validate_pressure_only(
    frame: pd.DataFrame,
    *,
    folds: int = 5,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if folds < 2:
        raise ValueError("At least two folds are required")
    fold_map = _storm_folds(frame["SID"].astype(str), folds, seed)
    result = frame[["SID", "time", "wind_ms", "pressure_hpa", "pressure_deficit_hpa"]].copy()
    result["fold"] = result["SID"].map(fold_map).astype(int)
    result["predicted_wind_ms"] = np.nan
    result["baseline_wind_ms"] = np.nan
    coefficients: list[dict[str, float | int]] = []

    for fold in range(folds):
        test = result["fold"].eq(fold)
        train = ~test
        x_train = np.column_stack(
            (np.ones(int(train.sum())), result.loc[train, "pressure_deficit_hpa"].to_numpy(float))
        )
        y_train = result.loc[train, "wind_ms"].to_numpy(float)
        beta = np.linalg.lstsq(x_train, y_train, rcond=None)[0]
        x_test = np.column_stack(
            (np.ones(int(test.sum())), result.loc[test, "pressure_deficit_hpa"].to_numpy(float))
        )
        result.loc[test, "predicted_wind_ms"] = x_test @ beta
        result.loc[test, "baseline_wind_ms"] = float(np.mean(y_train))
        coefficients.append(
            {
                "fold": fold,
                "train_storms": int(result.loc[train, "SID"].nunique()),
                "test_storms": int(result.loc[test, "SID"].nunique()),
                "train_rows": int(train.sum()),
                "test_rows": int(test.sum()),
                "intercept": float(beta[0]),
                "slope": float(beta[1]),
            }
        )

    if result[["predicted_wind_ms", "baseline_wind_ms"]].isna().any().any():
        raise AssertionError("Every held-out row must receive both predictions")
    observed = result["wind_ms"].to_numpy(float)
    predicted = result["predicted_wind_ms"].to_numpy(float)
    baseline = result["baseline_wind_ms"].to_numpy(float)
    model_metrics = error_metrics(observed, predicted)
    baseline_metrics = error_metrics(observed, baseline)
    variance_reduction = 1.0 - model_metrics["rmse_ms"] ** 2 / baseline_metrics["rmse_ms"] ** 2
    summary = {
        "folds": folds,
        "assignment": "SHA256(seed:SID), sorted, round-robin",
        "seed": seed,
        "rows": int(len(result)),
        "storms": int(result["SID"].nunique()),
        "pressure_only": model_metrics,
        "training_mean_baseline": baseline_metrics,
        "cross_validated_variance_reduction": float(variance_reduction),
        "fold_coefficients": coefficients,
    }
    return result, summary


def bootstrap_error_intervals(
    observed: np.ndarray,
    predicted: np.ndarray,
    storm_ids: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, list[float]]:
    error = np.asarray(predicted, dtype=float) - np.asarray(observed, dtype=float)
    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    weights = draws[:, labels]
    count = weights.sum(axis=1)
    total = weights @ error
    absolute_total = weights @ np.abs(error)
    square_total = weights @ (error**2)
    bias = total / count
    mae = absolute_total / count
    mse = square_total / count
    rmse = np.sqrt(mse)
    sample_variance = (square_total - total**2 / count) / np.maximum(count - 1.0, 1.0)
    residual_sd = np.sqrt(np.maximum(sample_variance, 0.0))

    return {
        "mae_ms": [float(value) for value in np.percentile(mae, (2.5, 97.5))],
        "rmse_ms": [float(value) for value in np.percentile(rmse, (2.5, 97.5))],
        "bias_ms": [float(value) for value in np.percentile(bias, (2.5, 97.5))],
        "residual_sd_ms": [
            float(value) for value in np.percentile(residual_sd, (2.5, 97.5))
        ],
    }


def legacy_wind_pressure_correlation(legacy: pd.DataFrame) -> float:
    return float(legacy[["USA_WIND", "USA_PRES"]].corr().iloc[0, 1])
