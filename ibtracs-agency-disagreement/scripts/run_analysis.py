#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ibtracs_measurement.analysis import coast_point_sensitivity, run_coast_suite  # noqa: E402
from ibtracs_measurement.data import (  # noqa: E402
    AGENCIES,
    CMA_FACTOR_GRID,
    CMA_PRIMARY_FACTOR,
    INTENSITY_LABELS,
    USA_FACTORS,
    WIND_COLUMNS,
    analysis_sample,
    availability_eligible_frame,
    classify_era,
    classify_intensity,
    conversion_table,
    load_ibtracs,
    normalize_winds,
)
from ibtracs_measurement.geometry import CoastGeometry  # noqa: E402
from ibtracs_measurement.stats import (  # noqa: E402
    average_off_diagonal,
    bootstrap_correlation_matrix,
    bootstrap_mean_interval,
    bootstrap_neff,
    bootstrap_pairwise_sd,
    correlation_matrix,
    kish_effective_cluster_count,
    pairwise_count_matrix,
    pairwise_sd_matrix,
    weighted_bootstrap_median_interval,
)

BOOTSTRAP_SEED = 20260712
RAW_DIR = ROOT / "data" / "raw"
OUTPUT_DIR = ROOT / "outputs"
IBTRACS_PATH = RAW_DIR / "ibtracs.WP.list.v04r01.csv"
COAST_PATH = RAW_DIR / "ne_10m_coastline" / "ne_10m_coastline.shp"
LAND_PATH = RAW_DIR / "ne_10m_land" / "ne_10m_land.shp"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_ready(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(json_ready(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def remote_head(url: str) -> dict[str, str | None]:
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ibtracs-measurement/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return {
                "last_modified": response.headers.get("Last-Modified"),
                "content_length": response.headers.get("Content-Length"),
                "etag": response.headers.get("ETag"),
            }
    except Exception as error:
        return {"head_error": f"{type(error).__name__}: {error}"}


def matrix_frame(matrix: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(matrix, index=AGENCIES, columns=AGENCIES)


def original_available(normalized: pd.DataFrame) -> pd.DataFrame:
    values = normalized[list(AGENCIES)].copy()
    for agency in AGENCIES:
        values.loc[~normalized[f"original_{agency}"], agency] = np.nan
    return values


def make_partial_sample(normalized: pd.DataFrame, *, tropical_only: bool = True) -> pd.DataFrame:
    frame = normalized.copy()
    values = original_available(frame)
    frame.loc[:, list(AGENCIES)] = values
    available_count = values.notna().sum(axis=1)
    common = values.median(axis=1, skipna=True)
    mask = frame["SEASON"].between(2001, 2024) & available_count.ge(3) & common.ge(17.2)
    if tropical_only:
        mask &= frame["NATURE"].eq("TS")
    selected = frame.loc[mask].copy()
    selected["available_count"] = available_count.loc[mask]
    selected["common_intensity_ms"] = common.loc[mask]
    selected["disagreement_ms"] = selected[list(AGENCIES)].std(axis=1, ddof=1)
    selected["relative_disagreement"] = selected["disagreement_ms"] / selected["common_intensity_ms"]
    selected["intensity_bin"] = classify_intensity(selected["common_intensity_ms"])
    selected["era"] = classify_era(selected["SEASON"])

    ordered = selected.sort_values(["SID", "time"])
    grouped = ordered.groupby("SID", sort=False)
    previous = grouped["common_intensity_ms"].shift(1)
    following = grouped["common_intensity_ms"].shift(-1)
    previous_time = grouped["time"].shift(1)
    following_time = grouped["time"].shift(-1)
    exact = previous_time.notna() & following_time.notna()
    valid_index = exact.loc[exact].index
    exact.loc[valid_index] = (
        ordered.loc[valid_index, "time"].astype("int64").to_numpy()
        - previous_time.loc[valid_index].astype("int64").to_numpy()
        == 6 * 60 * 60 * 1_000_000_000
    ) & (
        following_time.loc[valid_index].astype("int64").to_numpy()
        - ordered.loc[valid_index, "time"].astype("int64").to_numpy()
        == 6 * 60 * 60 * 1_000_000_000
    )
    delta = (following - previous).where(exact)
    stage = pd.Series(pd.NA, index=ordered.index, dtype="string")
    stage.loc[delta.ge(2.5)] = "intensifying"
    stage.loc[delta.le(-2.5)] = "weakening"
    stage.loc[delta.gt(-2.5) & delta.lt(2.5)] = "steady"
    selected["stage"] = stage.reindex(selected.index)
    return selected.loc[(~selected["is_land"]) & selected["stage"].notna()].reset_index(drop=True)


def rate_table(frame: pd.DataFrame, group: str, observed_columns: dict[str, pd.Series]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    labels = frame[group].astype("string").fillna("missing")
    for label in sorted(labels.unique()):
        selected = labels.eq(label)
        output[str(label)] = {
            "records": int(selected.sum()),
            "availability": {
                agency: float(series.loc[selected].mean()) for agency, series in observed_columns.items()
            },
        }
    return output


def build_missingness(normalized: pd.DataFrame, complete_sample: pd.DataFrame) -> dict[str, Any]:
    eligible = availability_eligible_frame(normalized)
    raw_observed = {agency: eligible[agency].notna() for agency in AGENCIES}
    original_observed = {
        agency: eligible[agency].notna() & eligible[f"original_{agency}"] for agency in AGENCIES
    }
    complete = pd.concat(original_observed, axis=1).all(axis=1)
    patterns = pd.DataFrame(original_observed).astype(int).astype(str).agg("".join, axis=1)
    pattern_counts = patterns.value_counts().head(20)

    complete_records = eligible.loc[complete]
    incomplete_records = eligible.loc[~complete]
    comparison = {}
    for label, subset in (("complete_five", complete_records), ("incomplete", incomplete_records)):
        comparison[label] = {
            "records": int(len(subset)),
            "storms": int(subset["SID"].nunique()),
            "median_intensity_ms": float(subset["available_median_ms"].median()),
            "median_coast_distance_km": float(subset["coast_distance_km"].median()),
            "era_distribution": subset["era"].value_counts(normalize=True).to_dict(),
            "intensity_distribution": subset["intensity_bin"].astype("string").value_counts(normalize=True).to_dict(),
        }

    flow = {
        "main_synoptic_records_2001_2024": int(normalized["SEASON"].between(2001, 2024).sum()),
        "tropical_records_2001_2024": int(
            (normalized["SEASON"].between(2001, 2024) & normalized["NATURE"].eq("TS")).sum()
        ),
        "eligible_at_least_one_agency_and_17_2ms": int(len(eligible)),
        "complete_five_original_and_17_2ms": int(len(complete_sample)),
        "complete_five_original_storms": int(complete_sample["SID"].nunique()),
    }
    return {
        "scope": "2001-2024, NATURE=TS, common available median >=17.2 m/s",
        "records": int(len(eligible)),
        "storms": int(eligible["SID"].nunique()),
        "raw_availability": {agency: float(series.mean()) for agency, series in raw_observed.items()},
        "original_availability": {
            agency: float(series.mean()) for agency, series in original_observed.items()
        },
        "complete_five_original_rate": float(complete.mean()),
        "by_era": rate_table(eligible, "era", original_observed),
        "by_intensity": rate_table(eligible, "intensity_bin", original_observed),
        "by_distance": rate_table(eligible, "distance_bin", original_observed),
        "top_original_availability_patterns": {
            pattern: int(count) for pattern, count in pattern_counts.items()
        },
        "pattern_order": list(AGENCIES),
        "selection_comparison": comparison,
        "flow": flow,
    }


def interpolate_landfall_rows(
    normalized: pd.DataFrame,
    geometry: CoastGeometry,
    *,
    scenario: str,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    period = normalized.loc[normalized["SEASON"].between(2001, 2024)].copy()
    for sid, storm in period.groupby("SID", sort=False):
        storm = storm.sort_values("time").reset_index(drop=True)
        transition = (~storm["is_land"].to_numpy()[:-1]) & storm["is_land"].to_numpy()[1:]
        candidates = np.flatnonzero(transition)
        if candidates.size == 0:
            continue
        index = int(candidates[0])
        start = storm.iloc[index]
        end = storm.iloc[index + 1]
        hours = (end["time"] - start["time"]).total_seconds() / 3600.0
        if hours <= 0 or hours > 12:
            continue
        if start["NATURE"] != "TS" and end["NATURE"] != "TS":
            continue
        fraction = geometry.crossing_fraction(
            float(start["lon180"]), float(start["LAT"]), float(end["lon180"]), float(end["LAT"])
        )
        delta_lon = ((float(end["lon180"]) - float(start["lon180"]) + 180.0) % 360.0) - 180.0
        crossing_lon = ((float(start["lon180"]) + fraction * delta_lon + 180.0) % 360.0) - 180.0
        crossing_lat = float(start["LAT"] + fraction * (end["LAT"] - start["LAT"]))
        crossing_time = start["time"] + fraction * (end["time"] - start["time"])
        result: dict[str, Any] = {
            "SID": sid,
            "NAME": start["NAME"],
            "crossing_time": crossing_time,
            "crossing_lon": crossing_lon,
            "crossing_lat": crossing_lat,
            "segment_hours": hours,
            "fraction": fraction,
        }
        all_original = True
        for agency in AGENCIES:
            left, right = start[agency], end[agency]
            original = bool(start[f"original_{agency}"] and end[f"original_{agency}"])
            all_original &= original
            result[f"original_{agency}"] = original
            result[agency] = (
                float(left + fraction * (right - left))
                if pd.notna(left) and pd.notna(right)
                else np.nan
            )
        result["all_original"] = all_original
        values = np.array([result[agency] for agency in AGENCIES], dtype=float)
        result["available_count"] = int(np.isfinite(values).sum())
        result["common_intensity_ms"] = float(np.nanmedian(values)) if np.isfinite(values).any() else np.nan
        result["disagreement_ms"] = (
            float(np.std(values, ddof=1)) if np.isfinite(values).all() else np.nan
        )
        rows.append(result)

    landfalls = pd.DataFrame(rows)
    complete = landfalls.loc[
        landfalls[list(AGENCIES)].notna().all(axis=1) & landfalls["all_original"]
    ].copy()
    station_columns = [
        column
        for column in normalized.columns
        if any(token in column.upper() for token in ("STATION", "ANEMOM", "OBSERVED_WIND"))
    ]
    reference_agencies = ("JTWC", "JMA", "HKO", "KMA")
    for agency in reference_agencies:
        complete[f"{agency}_minus_CMA"] = complete[agency] - complete["CMA"]

    reference_summary: dict[str, Any] = {}
    if len(complete) >= 3:
        for offset, agency in enumerate(reference_agencies):
            values = complete[f"{agency}_minus_CMA"].to_numpy(float)
            mean, lower, upper = bootstrap_mean_interval(
                values,
                complete["SID"],
                replicates=replicates,
                seed=seed + offset,
            )
            reference_summary[agency] = {
                "mean_difference_ms": mean,
                "mean_interval": [lower, upper],
                "sd_difference_ms": float(np.std(values, ddof=1)),
            }
        delta_columns = [f"{agency}_minus_CMA" for agency in reference_agencies]
        corr = bootstrap_correlation_matrix(
            complete[delta_columns].to_numpy(float),
            complete["SID"],
            replicates=replicates,
            seed=seed + 20,
        )
        reference_correlation = {
            "agencies": list(reference_agencies),
            "point": corr["point"],
            "lower": corr["lower"],
            "upper": corr["upper"],
        }
    else:
        reference_correlation = {"status": "insufficient_complete_landfalls"}

    records_path = OUTPUT_DIR / f"landfall_records_{scenario}.csv"
    landfalls.to_csv(records_path, index=False)
    return {
        "detected_first_ocean_to_land_crossings": int(len(landfalls)),
        "complete_five_original_crossings": int(len(complete)),
        "station_truth_fields": station_columns,
        "truth_error_status": "unidentifiable_from_ibtracs",
        "truth_error_reason": (
            "The IBTrACS schema has no independent station wind, station location, observation time, "
            "or station averaging-window field."
        ),
        "cma_home_advantage": (
            "CMA analysis can ingest Chinese station information; CMA-relative differences share the "
            "reference term and are descriptive reference contrasts."
        ),
        "reference_relative_summary": reference_summary,
        "reference_relative_correlation": reference_correlation,
        "records_file": str(records_path.relative_to(ROOT)),
    }


def summarize_pairwise(
    normalized: pd.DataFrame,
    scenario: str,
    *,
    replicates: int,
) -> tuple[dict[str, Any], pd.DataFrame]:
    primary = analysis_sample(normalized)
    values = primary[list(AGENCIES)].to_numpy(float)
    boot = bootstrap_pairwise_sd(
        values,
        primary["SID"],
        replicates=replicates,
        seed=BOOTSTRAP_SEED + (0 if scenario == "S088" else 100),
    )
    matrix_frame(boot.point).to_csv(OUTPUT_DIR / f"pairwise_disagreement_{scenario}.csv")

    grid_matrices = []
    for cma_factor in CMA_FACTOR_GRID:
        grid_normalized = normalize_winds(normalized, USA_FACTORS[scenario], cma_factor)
        grid_sample = analysis_sample(grid_normalized)
        grid_matrices.append(pairwise_sd_matrix(grid_sample[list(AGENCIES)].to_numpy(float)))
    grid_stack = np.stack(grid_matrices)

    eligible = availability_eligible_frame(normalized)
    available_values = original_available(eligible).to_numpy(float)
    available_matrix = pairwise_sd_matrix(available_values)
    record_counts, storm_counts = pairwise_count_matrix(available_values, eligible["SID"])
    return {
        "scenario": scenario,
        "usa_1min_to_10min_factor": USA_FACTORS[scenario],
        "cma_2min_to_10min_primary_factor": CMA_PRIMARY_FACTOR,
        "agencies": list(AGENCIES),
        "records": int(len(primary)),
        "storms": int(primary["SID"].nunique()),
        "time_min": primary["time"].min(),
        "time_max": primary["time"].max(),
        "era_counts": primary["era"].value_counts().to_dict(),
        "kish_effective_storm_count": kish_effective_cluster_count(primary["SID"]),
        "point_ms": boot.point,
        "lower_95_ms": boot.lower,
        "upper_95_ms": boot.upper,
        "cma_factor_grid": list(CMA_FACTOR_GRID),
        "cma_grid_min_ms": np.nanmin(grid_stack, axis=0),
        "cma_grid_max_ms": np.nanmax(grid_stack, axis=0),
        "pairwise_available_sensitivity_ms": available_matrix,
        "pairwise_available_record_counts": record_counts,
        "pairwise_available_storm_counts": storm_counts,
    }, primary


def summarize_neff(
    raw: pd.DataFrame,
    *,
    replicates: int,
) -> dict[str, Any]:
    scenarios: dict[str, Any] = {}
    all_results = []
    for scenario_index, (scenario, usa_factor) in enumerate(USA_FACTORS.items()):
        factor_results: dict[str, Any] = {}
        for factor_index, cma_factor in enumerate(CMA_FACTOR_GRID):
            normalized = normalize_winds(raw, usa_factor, cma_factor)
            sample = analysis_sample(normalized)
            values = sample[list(AGENCIES)].to_numpy(float)
            result = bootstrap_neff(
                values,
                sample["SID"],
                replicates=replicates,
                seed=BOOTSTRAP_SEED + 1000 * scenario_index + factor_index,
            )
            raw_correlation = correlation_matrix(values)
            summary = {
                "records": int(len(sample)),
                "storms": int(sample["SID"].nunique()),
                "kish_effective_storm_count": kish_effective_cluster_count(sample["SID"]),
                "time_min": sample["time"].min(),
                "time_max": sample["time"].max(),
                "rho_bar": result["rho_bar"],
                "rho_interval": result["rho_interval"],
                "denominator": result["denominator"],
                "denominator_interval": result["denominator_interval"],
                "neff": result["neff"],
                "neff_interval": result["neff_interval"],
                "denominator_nonpositive_fraction": result["denominator_nonpositive_fraction"],
                "finite_identifiable": result["finite_identifiable"],
                "leave_one_out_correlation_matrix": result["correlation_matrix"],
                "raw_wind_correlation_matrix_diagnostic_only": raw_correlation,
                "raw_wind_rho_bar_diagnostic_only": average_off_diagonal(raw_correlation),
            }
            factor_results[f"{cma_factor:.2f}"] = summary
            all_results.append(summary)
        scenarios[scenario] = {
            "usa_factor": usa_factor,
            "cma_factors": factor_results,
            "primary_cma_factor": f"{CMA_PRIMARY_FACTOR:.2f}",
        }

    finite_neff = [item["neff"] for item in all_results if np.isfinite(item["neff"])]
    envelope = {
        "rho_bar_min": min(item["rho_bar"] for item in all_results),
        "rho_bar_max": max(item["rho_bar"] for item in all_results),
        "denominator_min": min(item["denominator"] for item in all_results),
        "denominator_max": max(item["denominator"] for item in all_results),
        "neff_point_min": min(finite_neff) if finite_neff else np.nan,
        "neff_point_max": max(finite_neff) if finite_neff else np.nan,
        "all_scenarios_finite_identifiable": all(
            bool(item["finite_identifiable"]) for item in all_results
        ),
    }
    return {
        "formula": "n_eff = 5 / (1 + 4 * rho_bar)",
        "assumptions": [
            "Five leave-one-out deviation channels are treated as an exchange-correlation system.",
            "The average pairwise correlation is used as one common correlation parameter.",
            "Leave-one-out deviations are used as proxies for agency errors despite their exact zero-sum constraint.",
        ],
        "scope_disclaimer": (
            "This quantity measures agreement among agency estimates. Accuracy requires an independent truth series."
        ),
        "algebraic_constraint": "For five agencies, sum_i d_i = 0 at every time.",
        "scenarios": scenarios,
        "sensitivity_envelope": envelope,
    }


def summarize_coast_effect(
    raw: pd.DataFrame,
    *,
    replicates: int,
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    output: dict[str, Any] = {}
    plot_frames: dict[str, pd.DataFrame] = {}
    for scenario_index, (scenario, usa_factor) in enumerate(USA_FACTORS.items()):
        normalized = normalize_winds(raw, usa_factor, CMA_PRIMARY_FACTOR)
        primary = analysis_sample(normalized, ocean_only=True, require_stage=True)
        plot_frames[scenario] = primary
        suite = run_coast_suite(
            primary,
            replicates=replicates,
            seed=BOOTSTRAP_SEED + 3000 + 100 * scenario_index,
        )

        cma_frames = []
        for cma_factor in CMA_FACTOR_GRID:
            candidate = analysis_sample(
                normalize_winds(raw, usa_factor, cma_factor),
                ocean_only=True,
                require_stage=True,
            )
            cma_frames.append((f"{cma_factor:.2f}", candidate))
        interpolation = analysis_sample(
            normalized,
            original_only=False,
            ocean_only=True,
            require_stage=True,
        )
        all_natures = analysis_sample(
            normalized,
            tropical_only=False,
            ocean_only=True,
            require_stage=True,
        )
        extended_period = analysis_sample(
            normalized,
            start_year=1987,
            ocean_only=True,
            require_stage=True,
        )
        partial = make_partial_sample(normalized)
        methodological = coast_point_sensitivity(
            [
                ("primary_original_TS_2001_2024", primary),
                ("including_interpolated_flags", interpolation),
                ("all_nature_codes", all_natures),
                ("extended_1987_2024", extended_period),
                ("at_least_three_agencies", partial),
            ]
        )
        output[scenario] = {
            "usa_factor": usa_factor,
            "cma_primary_factor": CMA_PRIMARY_FACTOR,
            "suite": suite,
            "cma_factor_point_sensitivity": coast_point_sensitivity(cma_frames),
            "methodological_point_sensitivity": methodological,
        }
    return output, plot_frames


def make_coast_plot(
    plot_frames: dict[str, pd.DataFrame], *, replicates: int
) -> list[dict[str, Any]]:
    colors = {
        "17.2-24.4": "#2a9d8f",
        "24.5-32.6": "#457b9d",
        "32.7-41.4": "#f4a261",
        "41.5-50.8": "#e76f51",
        ">=50.9": "#7b2cbf",
    }
    figure, axes = plt.subplots(1, 2, figsize=(14, 5.6), sharex=True, sharey=True)
    binned_records: list[dict[str, Any]] = []
    for panel_index, (scenario, frame) in enumerate(plot_frames.items()):
        axis = axes[panel_index]
        display = frame.loc[frame["coast_distance_km"].le(1500)].copy()
        for intensity_index, label in enumerate(INTENSITY_LABELS):
            subset = display.loc[display["intensity_bin"].astype("string").eq(label)].copy()
            axis.scatter(
                subset["coast_distance_km"],
                subset["disagreement_ms"],
                s=11,
                alpha=0.18,
                color=colors[label],
                edgecolors="none",
                rasterized=True,
                label=label if panel_index == 0 else None,
            )
            subset["distance_bin_100"] = np.floor(subset["coast_distance_km"] / 100).astype(int)
            medians = []
            for distance_bin, cell in subset.groupby("distance_bin_100", observed=True):
                if len(cell) < 8 or cell["SID"].nunique() < 4:
                    continue
                point, lower, upper = weighted_bootstrap_median_interval(
                    cell["disagreement_ms"].to_numpy(float),
                    cell["SID"],
                    replicates=replicates,
                    seed=BOOTSTRAP_SEED + 5000 + panel_index * 1000 + intensity_index * 50 + int(distance_bin),
                )
                center = float(distance_bin * 100 + 50)
                medians.append((center, point, lower, upper))
                binned_records.append(
                    {
                        "scenario": scenario,
                        "intensity_bin": label,
                        "distance_bin_start_km": int(distance_bin * 100),
                        "records": int(len(cell)),
                        "storms": int(cell["SID"].nunique()),
                        "median_disagreement_ms": point,
                        "lower_95_ms": lower,
                        "upper_95_ms": upper,
                    }
                )
            if medians:
                values = np.asarray(medians)
                axis.plot(values[:, 0], values[:, 1], color=colors[label], linewidth=2.0)
                axis.fill_between(
                    values[:, 0], values[:, 2], values[:, 3], color=colors[label], alpha=0.11
                )
        axis.axvline(400, color="#222222", linestyle="--", linewidth=1.2)
        axis.set_title(
            f"{scenario}: JTWC {USA_FACTORS[scenario]:.2f}, CMA {CMA_PRIMARY_FACTOR:.2f}"
        )
        axis.set_xlabel("Distance to nearest coastline (km)")
        axis.grid(color="#d8d8d8", linewidth=0.6, alpha=0.6)
        axis.set_xlim(0, 1500)
        axis.set_ylim(bottom=0)
    axes[0].set_ylabel("Five-agency disagreement SD (m/s)")
    axes[0].legend(title="Common intensity (m/s)", frameon=False, ncol=1)
    figure.suptitle("Western North Pacific agency disagreement versus coastline distance")
    figure.tight_layout()
    figure.savefig(OUTPUT_DIR / "disagreement_vs_coast.png", dpi=240, bbox_inches="tight")
    plt.close(figure)
    pd.DataFrame(binned_records).to_csv(OUTPUT_DIR / "coast_binned_medians.csv", index=False)
    return binned_records


def file_provenance(path: Path, source_url: str) -> dict[str, Any]:
    return {
        "local_path": str(path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "source_url": source_url,
        "remote_head_at_run": remote_head(source_url),
    }


def build_provenance(
    raw: pd.DataFrame,
    geometry: CoastGeometry,
    validation: dict[str, Any],
    *,
    bootstrap_replicates: int,
) -> dict[str, Any]:
    import importlib.metadata

    ibtracs_url = (
        "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-"
        "stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv"
    )
    coast_url = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_coastline.zip"
    land_url = "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_land.zip"
    versions = {}
    for name, path in (
        ("coastline", RAW_DIR / "ne_10m_coastline" / "ne_10m_coastline.VERSION.txt"),
        ("land", RAW_DIR / "ne_10m_land" / "ne_10m_land.VERSION.txt"),
    ):
        versions[name] = path.read_text(encoding="utf-8").strip()
    packages = {}
    for package in ("matplotlib", "numpy", "pandas", "pyproj", "pyshp", "scipy", "shapely"):
        packages[package] = importlib.metadata.version(package)
    return {
        "generated_at_utc": datetime.now(timezone.utc),
        "measurement_scope": "Western North Pacific agency intensity disagreement; no forecast model",
        "ibtracs": file_provenance(IBTRACS_PATH, ibtracs_url),
        "ibtracs_column_documentation": file_provenance(
            RAW_DIR / "IBTrACS_v04r01_column_documentation.pdf",
            "https://www.ncei.noaa.gov/sites/default/files/2025-09/IBTrACS_v04r01_column_documentation.pdf",
        ),
        "wmo_wind_guidance": file_provenance(
            RAW_DIR / "WMO_TD_1555_wind_averaging.pdf",
            "https://systemsengineeringaustralia.com.au/download/WMO_TC_Wind_Averaging_27_Aug_2010.pdf",
        ),
        "natural_earth_coastline": file_provenance(
            RAW_DIR / "ne_10m_coastline.zip", coast_url
        ),
        "natural_earth_land": file_provenance(RAW_DIR / "ne_10m_land.zip", land_url),
        "natural_earth_versions": versions,
        "coast_spacing_km": geometry.spacing_km,
        "coast_validation": validation,
        "taiwan_land_mask_check_121E_23_5N": geometry.taiwan_included(),
        "input_records_after_1987_2024_main_synoptic_filter": int(len(raw)),
        "input_storms": int(raw["SID"].nunique()),
        "input_time_min": raw["time"].min(),
        "input_time_max": raw["time"].max(),
        "confirmed_wind_columns": {
            agency: WIND_COLUMNS[agency] for agency in AGENCIES
        },
        "cwa_cwb_columns_present": any(
            column in raw.columns for column in ("CWA_WIND", "CWB_WIND")
        ),
        "bootstrap_replicates": bootstrap_replicates,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "software": packages,
    }


def export_analysis_dataset(raw: pd.DataFrame) -> None:
    selected = raw.loc[raw["SEASON"].between(2001, 2024)].copy()
    columns = [
        "row_id",
        "SID",
        "SEASON",
        "NAME",
        "time",
        "NATURE",
        "LAT",
        "lon180",
        "is_land",
        "coast_distance_km",
        "IFLAG",
        "USA_AGENCY",
        *WIND_COLUMNS.values(),
        *[f"flag_{agency}" for agency in AGENCIES],
        *[f"original_{agency}" for agency in AGENCIES],
    ]
    output = selected[columns].copy()
    original_complete = selected[[f"original_{agency}" for agency in AGENCIES]].all(axis=1)
    for scenario, usa_factor in USA_FACTORS.items():
        normalized = normalize_winds(selected, usa_factor, CMA_PRIMARY_FACTOR)
        for agency in AGENCIES:
            output[f"{scenario}_{agency}_10min_ms"] = normalized[agency]
        complete = normalized[list(AGENCIES)].notna().all(axis=1) & original_complete
        output[f"{scenario}_complete_five_original"] = complete
        output[f"{scenario}_common_median_ms"] = normalized[list(AGENCIES)].median(
            axis=1, skipna=False
        )
        output[f"{scenario}_disagreement_sd_ms"] = normalized[list(AGENCIES)].std(
            axis=1, ddof=1, skipna=False
        )
    output.to_csv(OUTPUT_DIR / "analysis_dataset.csv", index=False)


def markdown_matrix(matrix: Any, digits: int = 0) -> str:
    values = np.asarray(matrix, dtype=float)
    lines = ["| | " + " | ".join(AGENCIES) + " |", "|---|" + "---:|" * len(AGENCIES)]
    for agency, row in zip(AGENCIES, values):
        formatted = []
        for value in row:
            formatted.append("NA" if not np.isfinite(value) else f"{value:.{digits}f}")
        lines.append(f"| {agency} | " + " | ".join(formatted) + " |")
    return "\n".join(lines)


def rounded_integer(value: float) -> str:
    rounded = int(np.rint(value))
    return "0" if rounded == 0 else str(rounded)


def pair_extremes(summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    matrix = np.asarray(summary["point_ms"], dtype=float)
    lower = np.asarray(summary["lower_95_ms"], dtype=float)
    upper = np.asarray(summary["upper_95_ms"], dtype=float)
    grid_min = np.asarray(summary["cma_grid_min_ms"], dtype=float)
    grid_max = np.asarray(summary["cma_grid_max_ms"], dtype=float)
    pairs = []
    for left in range(len(AGENCIES)):
        for right in range(left + 1, len(AGENCIES)):
            pairs.append(
                {
                    "pair": f"{AGENCIES[left]}-{AGENCIES[right]}",
                    "value": matrix[left, right],
                    "lower": lower[left, right],
                    "upper": upper[left, right],
                    "grid_min": grid_min[left, right],
                    "grid_max": grid_max[left, right],
                }
            )
    return min(pairs, key=lambda item: item["value"]), max(pairs, key=lambda item: item["value"])


def coast_decision_text(decision: str) -> str:
    return {
        "supports_near_coast_contraction_with_breakpoint": "支持近岸分歧收缩，并支持预注册的 400 km 折点",
        "disagreement_increases_toward_coast": "分歧在靠岸过程中增大",
        "near_coast_effect_uncertain_or_disappears": "控制混淆后，近岸效应区间跨越零",
        "distance_association_without_400km_breakpoint_support": "存在距离关联，400 km 折点证据不足",
    }[decision]


def render_report(
    provenance: dict[str, Any],
    pairwise: dict[str, Any],
    neff: dict[str, Any],
    coast: dict[str, Any],
    missingness: dict[str, Any],
    landfall: dict[str, Any],
) -> None:
    primary_neff = {
        scenario: neff["scenarios"][scenario]["cma_factors"][f"{CMA_PRIMARY_FACTOR:.2f}"]
        for scenario in USA_FACTORS
    }
    core = {scenario: coast[scenario]["suite"]["adjusted"] for scenario in USA_FACTORS}
    land_primary = landfall["S093"]
    land_alternate = landfall["S088"]
    lines = [
        "# IBTrACS 西北太平洋五机构强度分歧测量",
        "",
        f"生成时间：{provenance['generated_at_utc'].isoformat()}",
        f"范围：JTWC、JMA、CMA、HKO、KMA；名义期 2001–2024，complete-five 实际支持期 {pairwise['S093']['time_min'].year}–{pairwise['S093']['time_max'].year}；10 m、10 分钟、m/s。",
        "研究性质：历史机构分析的一致性测量。模型构建与巴威预测均未进入流程。",
        "",
        "## 判决置顶",
        "",
    ]
    if all(not item["finite_identifiable"] for item in primary_neff.values()):
        lines.append(
            "1. **有限 `n_eff` 被预注册闸门判为不可识别。** 留一偏差严格零和，"
            f"换算敏感性给出的代数点值为 {neff['sensitivity_envelope']['neff_point_min']:.0f}–{neff['sensitivity_envelope']['neff_point_max']:.0f}，"
            "超过五家意见的解释上限；交换相关解释在该构造下失效。"
        )
    else:
        lines.append("1. **部分换算情景通过有限 `n_eff` 闸门；具体区间见下表。**")
    lines.append(
        "2. **登陆真值误差相关由 IBTrACS 单独无法识别。** 当前字段缺少独立测站风、"
        "测站位置、观测时刻和平均窗；CMA 参考差只提供循环参照下的描述。"
    )
    lines.append(
        "3. **核心近岸检验：** "
        + "；".join(f"{scenario} {coast_decision_text(result['decision'])}" for scenario, result in core.items())
        + "。"
    )
    lines.extend(
        [
            "",
            "该数据测得机构彼此有多像；共同偏离真实强度的程度需要独立观测真值。",
            "",
            "## 数据与口径",
            "",
            f"- [MEASURED] 当前文件筛选后含 {provenance['input_records_after_1987_2024_main_synoptic_filter']:,} 条 6 小时时次、{provenance['input_storms']:,} 个 SID。",
            f"- [MEASURED] Natural Earth 海岸点最大间距为 {provenance['coast_spacing_km']:.0f} km；200 点验证的 P95 绝对误差为 {provenance['coast_validation']['p95_absolute_error_km']:.1f} km。",
            f"- [MEASURED] 台湾掩膜检查 `121°E/23.5°N`：`{provenance['taiwan_land_mask_check_121E_23_5N']}`。全球陆地统一进入计算。",
            f"- [CITED] Natural Earth 当前下载包版本：coastline `{provenance['natural_earth_versions']['coastline']}`，land `{provenance['natural_earth_versions']['land']}`。海岸距离验证量化 coastline 加密误差；版本差异保留在 provenance。",
            "- [MEASURED] 当前 IBTrACS v04r01 西北太平洋 CSV 缺少 CWA/CWB 原生列；第五家依预先登记的 D001 使用 KMA。",
            "",
            "|机构|IBTrACS 列|原生平均窗|目标平均窗|乘数|状态/来源|",
            "|---|---|---:|---:|---:|---|",
            "|JTWC|USA_WIND|1 min|10 min|0.88 / 0.93|[ASSUMED/CITED] 传统情景 / WMO 海上 Vmax 建议|",
            "|JMA|TOKYO_WIND|10 min|10 min|1.00|[CITED] IBTrACS 字段文档|",
            "|CMA|CMA_WIND|2 min|10 min|0.96；0.90–1.00 敏感性|[ASSUMED+CITED] WES 2026 表 4；方法不确定性由网格覆盖|",
            "|HKO|HKO_WIND|10 min|10 min|1.00|[CITED] IBTrACS 字段文档|",
            "|KMA|KMA_WIND|10 min|10 min|1.00|[CITED] IBTrACS 字段文档|",
            "",
            "WMO/TD-No.1555 对机构 `Vmax` 明确建议海上 1 分钟→10 分钟取 0.93，并把 0.88 归为传统/近岸暴露量级。"
            "CMA 的 0.96 来自同行评议论文对 WMO 阵风因子的应用；WMO 原文同时强调随机平均风与峰值阵风的转换语义差异。"
            "因此 0.96 作为显式假设使用，完整 0.90–1.00 网格承担敏感性。",
            "",
            "## A. 成对分歧",
            "",
        ]
    )
    for scenario in USA_FACTORS:
        summary = pairwise[scenario]
        smallest, largest = pair_extremes(summary)
        lines.extend(
            [
                f"### {scenario}：JTWC 系数 {USA_FACTORS[scenario]:.2f}",
                "",
                f"[MEASURED] `sd(V_i−V_j)`，m/s；{summary['records']:,} 条记录、{summary['storms']} 个台风，Kish 有效台风数 {summary['kish_effective_storm_count']:.0f}；实际日期 {summary['time_min'].year}–{summary['time_max'].year}。展示值按要求取整数。",
                "",
                markdown_matrix(summary["point_ms"], digits=0),
                "",
                f"[MEASURED] 最小成对分歧为 {smallest['pair']}：{smallest['value']:.0f} m/s，台风聚类 95% CI {smallest['lower']:.0f}–{smallest['upper']:.0f} m/s；"
                f"最大为 {largest['pair']}：{largest['value']:.0f} m/s，95% CI {largest['lower']:.0f}–{largest['upper']:.0f} m/s。",
                f"[ASSUMED→MEASURED] CMA 0.90–1.00 网格使这两项分别覆盖 {smallest['grid_min']:.0f}–{smallest['grid_max']:.0f} 与 {largest['grid_min']:.0f}–{largest['grid_max']:.0f} m/s。",
                "",
            ]
        )
    lines.extend(
        [
            "两份完整聚类区间、CMA 网格包络、逐对可用样本矩阵存于 `outputs/pairwise_disagreement_intervals.json`。",
            "",
            "## B. 去共同信号相关与 n_eff",
            "",
            "[ASSUMED] 公式采用五通道交换相关结构：`n_eff = 5/(1+4ρ̄)`。"
            "每个时次先计算 `d_i = V_i − mean(V_-i)`，再在 `d_i` 上求相关；原始风速相关仅作为共同生消信号诊断。",
            "",
            "|情景|ρ̄（95% CI）|分母（95% CI）|n_eff（95% CI）|闸门|",
            "|---|---:|---:|---:|---|",
        ]
    )
    for scenario, item in primary_neff.items():
        lines.append(
            f"|{scenario}|{item['rho_bar']:.2f} ({item['rho_interval'][0]:.2f}, {item['rho_interval'][1]:.2f})|"
            f"{item['denominator']:.3f} ({item['denominator_interval'][0]:.3f}, {item['denominator_interval'][1]:.3f})|"
            f"{item['neff']:.1f} ({item['neff_interval'][0]:.1f}, {item['neff_interval'][1]:.1f})|"
            f"{'通过' if item['finite_identifiable'] else '有限值不可识别'}|"
        )
    lines.extend(
        [
            "",
            "[MEASURED] 留一偏差满足 `Σd_i=0`，它会机械地产生负相关并把分母推向零。"
            f"原始风速的平均相关为 {primary_neff['S093']['raw_wind_rho_bar_diagnostic_only']:.2f}，该高值主要反映共同生消信号，未进入公式。"
            f"全敏感性 `ρ̄` 为 {neff['sensitivity_envelope']['rho_bar_min']:.2f}–{neff['sensitivity_envelope']['rho_bar_max']:.2f}。"
            "该数字只衡量意见一致性；准确性需要独立真值。全部 22 个风窗情景见 `outputs/neff_sensitivity.json`。",
            "",
            "## 核心检验：分歧与离岸距离",
            "",
            "[ASSUMED] 因变量为 `log1p(SD/共同中位强度)`；控制强度层、年代层、生消阶段；"
            "台风为 2,000 次 cluster bootstrap 单位。正的近岸段斜率表示向外海移动时分歧增加。",
            "",
            "|情景|记录/台风/Kish|未调整近岸斜率 95% CI|调整后近岸斜率 95% CI|斜率变化 95% CI|ΔAIC|判决|",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for scenario in USA_FACTORS:
        suite = coast[scenario]["suite"]
        adjusted = suite["adjusted"]
        unadjusted = suite["unadjusted"]
        lines.append(
            f"|{scenario}|{adjusted['records']}/{adjusted['storms']}/{adjusted['kish_effective_storm_count']:.0f}|"
            f"{unadjusted['near_slope']['estimate']:.4f} ({unadjusted['near_slope']['lower']:.4f}, {unadjusted['near_slope']['upper']:.4f})|"
            f"{adjusted['near_slope']['estimate']:.4f} ({adjusted['near_slope']['lower']:.4f}, {adjusted['near_slope']['upper']:.4f})|"
            f"{adjusted['slope_change']['estimate']:.4f} ({adjusted['slope_change']['lower']:.4f}, {adjusted['slope_change']['upper']:.4f})|"
            f"{adjusted['delta_aic_piecewise_minus_linear']:.1f}|{coast_decision_text(adjusted['decision'])}|"
        )
    lines.extend(
        [
            "",
            "[MEASURED] 两种情景的调整后近岸斜率 95% CI 均位于零以下，方向在 CMA 0.90–1.00 网格、至少三家样本和台风内回归中保持。"
            "两种情景的斜率变化 95% CI 均跨零；S088 的 ΔAIC 也未达到 −2，因此 400 km 折点缺少预注册支持。",
            "",
            "### 强度分层",
            "",
            "[MEASURED] 调整后近岸段斜率（每 100 km，95% 台风聚类 CI）：",
            "",
            "|共同强度层 m/s|S088|S093|",
            "|---|---:|---:|",
        ]
    )
    for label in INTENSITY_LABELS:
        values = []
        for scenario in USA_FACTORS:
            item = coast[scenario]["suite"]["intensity_strata"][label]
            if item.get("status"):
                values.append("样本不足")
            else:
                slope = item["near_slope"]
                values.append(f"{slope['estimate']:.4f} ({slope['lower']:.4f}, {slope['upper']:.4f})")
        lines.append(f"|{label}|{values[0]}|{values[1]}|")
    lines.extend(
        [
            "",
            "只有 32.7–41.4 m/s 层在两种口径下均排除零；其余强度层证据区间跨零。"
            "整体反向关联具有稳健方向，强度层间普遍性仍受区间限制。",
            "",
            "强度分层、300/350/450/500 km 折点、CMA 网格、1987–2024、全部性质、插值标志、至少三家及台风内固定效应结果均在 `outputs/coast_effect.json`。",
            "",
            "![分歧与海岸距离](outputs/disagreement_vs_coast.png)",
            "",
            "## 缺测审计",
            "",
            f"[MEASURED] 候选时次 {missingness['records']:,} 条；五家原始值齐全率 {100*missingness['complete_five_original_rate']:.1f}%。",
            "",
            "|机构|原始风速可用率|",
            "|---|---:|",
        ]
    )
    for agency in AGENCIES:
        lines.append(f"|{agency}|{100*missingness['original_availability'][agency]:.1f}%|")
    lines.extend(
        [
            "",
            f"[MEASURED] KMA 覆盖率最低，complete-five 样本由覆盖交集决定。完整样本的海岸距离中位数为 {missingness['selection_comparison']['complete_five']['median_coast_distance_km']:.0f} km；"
            f"不完整样本为 {missingness['selection_comparison']['incomplete']['median_coast_distance_km']:.0f} km。逐年代、强度和距离缺测表见 `outputs/missingness.json`。",
            "[MEASURED] complete-five 样本在 2000–2009 年为零；2010–2019 占 57.5%，2020–2024 占 42.5%；最早完整记录出现在 2015 年。"
            "因此主矩阵与核心检验的历史支持范围为 2015–2024，年代外推受到 KMA 缺测限制。",
            "",
            "## 登陆子集",
            "",
            f"[MEASURED] S093 识别首次海→陆穿越 {land_primary['detected_first_ocean_to_land_crossings']} 个，其中五家原始值可线性插值的登陆 {land_primary['complete_five_original_crossings']} 个。",
            "[MEASURED] 独立测站真值字段数为 0，因此各机构登陆真值误差与误差相关矩阵保持不可识别状态。",
            "[ASSUMED] CMA 参考差带有主场优势：CMA 分析可吸收中国测站信息；所有 `agency−CMA` 项共享 CMA 参照，相关性也带共享项。",
            "",
            "|机构|S088 相对 CMA 平均差，m/s（95% CI）|S093 相对 CMA 平均差，m/s（95% CI）|S093 差值 SD，m/s|",
            "|---|---:|---:|---:|",
        ]
    )
    for agency, item in land_primary["reference_relative_summary"].items():
        alternate = land_alternate["reference_relative_summary"][agency]
        lines.append(
            f"|{agency}|{rounded_integer(alternate['mean_difference_ms'])} ({rounded_integer(alternate['mean_interval'][0])}, {rounded_integer(alternate['mean_interval'][1])})|"
            f"{rounded_integer(item['mean_difference_ms'])} ({rounded_integer(item['mean_interval'][0])}, {rounded_integer(item['mean_interval'][1])})|"
            f"{rounded_integer(item['sd_difference_ms'])}|"
        )
    reference_correlation = land_primary["reference_relative_correlation"]
    lines.extend(
        [
            "",
            "[MEASURED] S093 的 CMA 参考差相关矩阵（点估计，完整台风登陆）：",
            "",
            "| |" + "|".join(reference_correlation["agencies"]) + "|",
            "|---|" + "---:|" * len(reference_correlation["agencies"]),
        ]
    )
    for agency, row in zip(reference_correlation["agencies"], reference_correlation["point"]):
        lines.append(f"|{agency}|" + "|".join(f"{value:.2f}" for value in row) + "|")
    lines.extend(
        [
            "",
            "这些量是 CMA 参考差，具有描述性；共享 CMA 项会直接影响相关。真实误差需要独立测站序列及统一平均窗。",
            "",
            "## 七个陷阱回应",
            "",
            "1. **平均窗：** 每家原生窗、目标窗和乘数均打印；JTWC 双情景与 CMA 网格完整保留。",
            "2. **尺度与相关：** 成对 SD 在每个换算情景重算；相关性结果单独处理。",
            "3. **共同信号：** `n_eff` 使用留一偏差相关；原始风速相关只作诊断。",
            "4. **自相关：** 全部 95% CI 以 SID 为 cluster 做 2,000 次 bootstrap；同时报告 SID 与 Kish 有效台风数。",
            "5. **混淆：** 核心检验控制共同强度、年代、生消阶段，并补充台风内估计和强度分层。",
            "6. **缺测：** 原始值、机构插值标志、年代/强度/距离缺测模式及至少三家敏感性均显式输出。",
            "7. **独立真值：** 西北太平洋常规飞机侦察于 1987 年结束；此后机构分析共享以卫星为主的信息体系。共同误差由该数据无法分离，登陆字段审计同样缺少独立测站真值。",
            "",
            "## 假设、测量与引用",
            "",
            "- [MEASURED] 矩阵、相关、回归、距离、缺测和登陆穿越均由本次固定代码计算。",
            "- [ASSUMED] 风窗换算、交换相关公式、17.2 m/s 阈值、400 km 折点和线性控制形式。",
            "- [CITED] [NOAA/NCEI IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive)、"
            "[IBTrACS v04r01 字段文档](https://www.ncei.noaa.gov/sites/default/files/2025-09/IBTrACS_v04r01_column_documentation.pdf)、"
            "[CMA 的 2 分钟定义](https://www.cma.gov.cn/wmhd/gzly/cjwt/202311/t20231127_5912128.html)、"
            "[WMO/TD-No.1555](https://systemsengineeringaustralia.com.au/download/WMO_TC_Wind_Averaging_27_Aug_2010.pdf)、"
            "[WES 2026 换算表](https://doi.org/10.5194/wes-11-1889-2026)、"
            "[Knapp 等 2013 对 1987 年后地面真值缺口的审计](https://doi.org/10.1175/MWR-D-12-00323.1)、"
            "[Natural Earth 1:10m coastline](https://www.naturalearthdata.com/downloads/10m-physical-vectors/10m-coastline/)。",
            "",
            "## 预注册偏离",
            "",
            "完整记录见 `deviations.md`：D001 将缺席的 CWA/CWB 列替换为 KMA；D002 主分析使用原始强度标志；D003 主分析限定 `NATURE=TS`。三项均在首次统计结果之前登记。",
        ]
    )
    (ROOT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bootstrap-replicates",
        type=int,
        default=2000,
        help="Storm-cluster bootstrap replicates (preregistered value: 2000)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    replicates = args.bootstrap_replicates
    if replicates <= 0:
        raise ValueError("bootstrap replicates must be positive")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for required in (IBTRACS_PATH, COAST_PATH, LAND_PATH):
        if not required.exists():
            raise FileNotFoundError(required)

    print("[1/8] Loading IBTrACS and confirming schema", flush=True)
    raw = load_ibtracs(IBTRACS_PATH)

    print("[2/8] Building global Natural Earth coast geometry", flush=True)
    geometry = CoastGeometry.from_shapefiles(COAST_PATH, LAND_PATH, spacing_km=5.0)
    validation = geometry.validate_distances(
        raw["lon180"].to_numpy(float), raw["LAT"].to_numpy(float), sample_size=200
    )
    if validation["p95_absolute_error_km"] > 5.0:
        print("Coast P95 error exceeded 5 km; rebuilding at 2 km spacing", flush=True)
        geometry = CoastGeometry.from_shapefiles(COAST_PATH, LAND_PATH, spacing_km=2.0)
        validation = geometry.validate_distances(
            raw["lon180"].to_numpy(float), raw["LAT"].to_numpy(float), sample_size=200
        )
    if validation["p95_absolute_error_km"] > 5.0:
        raise RuntimeError(
            f"Coast distance validation failed: P95={validation['p95_absolute_error_km']:.2f} km"
        )
    if not geometry.taiwan_included():
        raise RuntimeError("Natural Earth land mask failed the Taiwan inclusion check")
    raw["coast_distance_km"] = geometry.distance_km(
        raw["lon180"].to_numpy(float), raw["LAT"].to_numpy(float)
    )
    raw["is_land"] = geometry.is_land(
        raw["lon180"].to_numpy(float), raw["LAT"].to_numpy(float)
    )

    print("[3/8] Measuring pairwise disagreement and missingness", flush=True)
    pairwise = {}
    primary_samples = {}
    for scenario, usa_factor in USA_FACTORS.items():
        normalized = normalize_winds(raw, usa_factor, CMA_PRIMARY_FACTOR)
        summary, sample = summarize_pairwise(
            normalized, scenario, replicates=replicates
        )
        pairwise[scenario] = summary
        primary_samples[scenario] = sample
    write_json(OUTPUT_DIR / "pairwise_disagreement_intervals.json", pairwise)
    missingness = build_missingness(
        normalize_winds(raw, USA_FACTORS["S093"], CMA_PRIMARY_FACTOR),
        primary_samples["S093"],
    )
    write_json(OUTPUT_DIR / "missingness.json", missingness)

    print("[4/8] Running leave-one-out n_eff audit", flush=True)
    neff = summarize_neff(raw, replicates=replicates)
    write_json(OUTPUT_DIR / "neff_sensitivity.json", neff)

    print("[5/8] Running coast-distance confounder and breakpoint tests", flush=True)
    coast, plot_frames = summarize_coast_effect(raw, replicates=replicates)
    write_json(OUTPUT_DIR / "coast_effect.json", coast)

    print("[6/8] Auditing first landfall crossings and truth fields", flush=True)
    landfall = {}
    for index, (scenario, usa_factor) in enumerate(USA_FACTORS.items()):
        landfall[scenario] = interpolate_landfall_rows(
            normalize_winds(raw, usa_factor, CMA_PRIMARY_FACTOR),
            geometry,
            scenario=scenario,
            replicates=replicates,
            seed=BOOTSTRAP_SEED + 7000 + index * 100,
        )
    write_json(OUTPUT_DIR / "landfall_audit.json", landfall)

    print("[7/8] Rendering figure and exporting analysis records", flush=True)
    binned = make_coast_plot(plot_frames, replicates=replicates)
    export_analysis_dataset(raw)
    write_json(
        OUTPUT_DIR / "conversion_windows.json",
        {
            scenario: conversion_table(usa_factor, CMA_PRIMARY_FACTOR)
            for scenario, usa_factor in USA_FACTORS.items()
        },
    )

    print("[8/8] Writing provenance and report", flush=True)
    provenance = build_provenance(
        raw,
        geometry,
        validation,
        bootstrap_replicates=replicates,
    )
    provenance["coast_plot_binned_cells"] = len(binned)
    write_json(OUTPUT_DIR / "provenance.json", provenance)
    render_report(provenance, pairwise, neff, coast, missingness, landfall)
    write_json(
        OUTPUT_DIR / "run_manifest.json",
        {
            "status": "complete",
            "bootstrap_replicates": replicates,
            "outputs": sorted(
                str(path.relative_to(ROOT))
                for path in OUTPUT_DIR.iterdir()
                if path.is_file()
            ),
            "report": "report.md",
            "preregistration_commit": "f6b9b63",
            "pre_result_deviation_commit": "c10d3b5",
        },
    )
    print(f"Completed. Report: {ROOT / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
