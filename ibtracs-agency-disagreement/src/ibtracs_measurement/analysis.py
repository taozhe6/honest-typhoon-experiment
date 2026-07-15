from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from .data import INTENSITY_LABELS
from .stats import fit_cluster_bootstrap_regression, kish_effective_cluster_count


def _dummy_columns(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    values = frame[column].astype("string").fillna("missing")
    return pd.get_dummies(values, prefix=column, drop_first=True, dtype=float)


def regression_design(
    frame: pd.DataFrame,
    *,
    breakpoint_km: float,
    controls: bool,
    within_storm: bool = False,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    distance = frame["coast_distance_km"].to_numpy(float) / 100.0
    breakpoint = breakpoint_km / 100.0
    design = pd.DataFrame(
        {
            "intercept": np.ones(len(frame)),
            "distance_near_per_100km": np.minimum(distance, breakpoint),
            "distance_far_per_100km": np.maximum(distance - breakpoint, 0.0),
        },
        index=frame.index,
    )
    if controls:
        control_columns = ["intensity_bin", "era", "stage"]
        if "available_count" in frame.columns:
            control_columns.append("available_count")
        for column in control_columns:
            design = pd.concat((design, _dummy_columns(frame, column)), axis=1)
    outcome = np.log1p(frame["relative_disagreement"].to_numpy(float))

    if within_storm:
        groups = frame["SID"]
        outcome = outcome - pd.Series(outcome, index=frame.index).groupby(groups).transform("mean").to_numpy()
        design = design.drop(columns="intercept")
        design = design - design.groupby(groups).transform("mean")

    varying = design.std(axis=0, ddof=0).gt(1e-12)
    if "intercept" in design:
        varying.loc["intercept"] = True
    design = design.loc[:, varying]
    return design.to_numpy(float), outcome, list(design.columns)


def _coefficient(result: dict, names: list[str], name: str) -> dict[str, float]:
    index = names.index(name)
    return {
        "estimate": float(result["beta"][index]),
        "lower": float(result["lower"][index]),
        "upper": float(result["upper"][index]),
    }


def _point_fit(frame: pd.DataFrame, breakpoint_km: float, controls: bool) -> dict[str, object]:
    x, y, names = regression_design(
        frame, breakpoint_km=breakpoint_km, controls=controls, within_storm=False
    )
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ beta
    rss = float(residual @ residual)
    aic = float(len(y) * np.log(rss / len(y)) + 2 * x.shape[1])
    return {"beta": beta, "names": names, "aic": aic, "rss": rss}


def run_piecewise_test(
    frame: pd.DataFrame,
    *,
    breakpoint_km: float = 400.0,
    controls: bool = True,
    within_storm: bool = False,
    replicates: int = 2000,
    seed: int = 20260712,
) -> dict[str, object]:
    x, y, names = regression_design(
        frame,
        breakpoint_km=breakpoint_km,
        controls=controls,
        within_storm=within_storm,
    )
    fit = fit_cluster_bootstrap_regression(
        x, y, frame["SID"].to_numpy(), replicates=replicates, seed=seed
    )
    near_name = "distance_near_per_100km"
    far_name = "distance_far_per_100km"
    near_index = names.index(near_name)
    far_index = names.index(far_name)
    slope_change = fit["beta"][far_index] - fit["beta"][near_index]
    boot_change = fit["bootstrap_beta"][:, far_index] - fit["bootstrap_beta"][:, near_index]
    change_interval = np.nanpercentile(boot_change, (2.5, 97.5))

    linear_frame = frame.copy()
    linear_frame["coast_distance_km"] = np.minimum(
        linear_frame["coast_distance_km"], 1_000_000.0
    )
    linear_distance = linear_frame["coast_distance_km"].to_numpy(float) / 100.0
    linear_design = pd.DataFrame(
        {"intercept": 1.0, "distance_per_100km": linear_distance}, index=frame.index
    )
    if controls:
        control_columns = ["intensity_bin", "era", "stage"]
        if "available_count" in frame.columns:
            control_columns.append("available_count")
        for column in control_columns:
            linear_design = pd.concat((linear_design, _dummy_columns(frame, column)), axis=1)
    if within_storm:
        groups = frame["SID"]
        linear_design = linear_design.drop(columns="intercept")
        linear_design = linear_design - linear_design.groupby(groups).transform("mean")
    varying = linear_design.std(axis=0, ddof=0).gt(1e-12)
    if "intercept" in linear_design:
        varying.loc["intercept"] = True
    linear_x = linear_design.loc[:, varying].to_numpy(float)
    linear_beta = np.linalg.lstsq(linear_x, y, rcond=None)[0]
    linear_residual = y - linear_x @ linear_beta
    linear_rss = float(linear_residual @ linear_residual)
    linear_aic = float(len(y) * np.log(linear_rss / len(y)) + 2 * linear_x.shape[1])
    delta_aic = float(fit["aic"] - linear_aic)

    near = _coefficient(fit, names, near_name)
    far = _coefficient(fit, names, far_name)
    supports = bool(
        near["lower"] > 0
        and (change_interval[0] > 0 or change_interval[1] < 0)
        and delta_aic <= -2
    )
    if supports:
        decision = "supports_near_coast_contraction_with_breakpoint"
    elif near["upper"] < 0:
        decision = "disagreement_increases_toward_coast"
    elif near["lower"] <= 0 <= near["upper"]:
        decision = "near_coast_effect_uncertain_or_disappears"
    else:
        decision = "distance_association_without_400km_breakpoint_support"

    return {
        "records": int(len(frame)),
        "storms": int(frame["SID"].nunique()),
        "kish_effective_storm_count": kish_effective_cluster_count(frame["SID"]),
        "breakpoint_km": float(breakpoint_km),
        "controls": controls,
        "within_storm": within_storm,
        "near_slope": near,
        "far_slope": far,
        "slope_change": {
            "estimate": float(slope_change),
            "lower": float(change_interval[0]),
            "upper": float(change_interval[1]),
        },
        "piecewise_aic": float(fit["aic"]),
        "linear_aic": linear_aic,
        "delta_aic_piecewise_minus_linear": delta_aic,
        "decision": decision,
    }


def run_coast_suite(
    frame: pd.DataFrame,
    *,
    replicates: int = 2000,
    seed: int = 20260712,
) -> dict[str, object]:
    adjusted = run_piecewise_test(
        frame, controls=True, replicates=replicates, seed=seed
    )
    unadjusted = run_piecewise_test(
        frame, controls=False, replicates=replicates, seed=seed + 1
    )
    within = run_piecewise_test(
        frame, controls=True, within_storm=True, replicates=replicates, seed=seed + 2
    )
    breakpoints = {}
    for breakpoint in (300.0, 350.0, 450.0, 500.0):
        point = _point_fit(frame, breakpoint, controls=True)
        names = point["names"]
        breakpoints[str(int(breakpoint))] = {
            "near_slope": float(point["beta"][names.index("distance_near_per_100km")]),
            "far_slope": float(point["beta"][names.index("distance_far_per_100km")]),
            "aic": float(point["aic"]),
        }

    strata = {}
    for offset, label in enumerate(INTENSITY_LABELS):
        subset = frame.loc[frame["intensity_bin"].astype("string").eq(label)].copy()
        if len(subset) < 30 or subset["SID"].nunique() < 10:
            strata[label] = {
                "records": int(len(subset)),
                "storms": int(subset["SID"].nunique()),
                "status": "insufficient",
            }
            continue
        strata[label] = run_piecewise_test(
            subset,
            controls=True,
            replicates=replicates,
            seed=seed + 10 + offset,
        )
    return {
        "adjusted": adjusted,
        "unadjusted": unadjusted,
        "within_storm": within,
        "breakpoint_sensitivity": breakpoints,
        "intensity_strata": strata,
    }


def coast_point_sensitivity(
    frames: Iterable[tuple[str, pd.DataFrame]], breakpoint_km: float = 400.0
) -> dict[str, object]:
    result = {}
    for label, frame in frames:
        fit = _point_fit(frame, breakpoint_km, controls=True)
        names = fit["names"]
        near = float(fit["beta"][names.index("distance_near_per_100km")])
        far = float(fit["beta"][names.index("distance_far_per_100km")])
        result[label] = {
            "records": int(len(frame)),
            "storms": int(frame["SID"].nunique()),
            "near_slope": near,
            "far_slope": far,
            "slope_change": far - near,
            "piecewise_aic": float(fit["aic"]),
        }
    return result
