from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

AGENCIES = ("JTWC", "JMA", "CMA", "HKO", "KMA")
WIND_COLUMNS = {
    "JTWC": "USA_WIND",
    "JMA": "TOKYO_WIND",
    "CMA": "CMA_WIND",
    "HKO": "HKO_WIND",
    "KMA": "KMA_WIND",
}
IFLAG_INDEX = {agency: index for index, agency in enumerate(AGENCIES)}
NATIVE_WINDOWS_MIN = {"JTWC": 1, "JMA": 10, "CMA": 2, "HKO": 10, "KMA": 10}
USA_FACTORS = {"S088": 0.88, "S093": 0.93}
CMA_PRIMARY_FACTOR = 0.96
CMA_FACTOR_GRID = tuple(round(value, 2) for value in np.arange(0.90, 1.001, 0.01))
KT_TO_MS = 0.5144444444444445
INTENSITY_LABELS = (
    "17.2-24.4",
    "24.5-32.6",
    "32.7-41.4",
    "41.5-50.8",
    ">=50.9",
)
INTENSITY_BINS = (17.2, 24.5, 32.7, 41.5, 50.9, np.inf)
DISTANCE_LABELS = ("0-100", "100-200", "200-400", "400-800", ">=800")
DISTANCE_BINS = (0.0, 100.0, 200.0, 400.0, 800.0, np.inf)


def load_ibtracs(path: Path, start_year: int = 1987, end_year: int = 2024) -> pd.DataFrame:
    columns = [
        "SID",
        "SEASON",
        "NAME",
        "ISO_TIME",
        "NATURE",
        "LAT",
        "LON",
        "TRACK_TYPE",
        "IFLAG",
        "USA_AGENCY",
        "USA_WIND",
        "TOKYO_WIND",
        "CMA_WIND",
        "HKO_WIND",
        "KMA_WIND",
        "LANDFALL",
        "DIST2LAND",
    ]
    frame = pd.read_csv(path, skiprows=[1], usecols=columns, low_memory=False)
    frame.columns = frame.columns.str.strip()
    for column in ("SID", "NAME", "NATURE", "TRACK_TYPE", "IFLAG", "USA_AGENCY"):
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    for column in ("SEASON", "LAT", "LON", "LANDFALL", "DIST2LAND", *WIND_COLUMNS.values()):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for column in WIND_COLUMNS.values():
        frame[column] = frame[column].where(frame[column] > 0)

    frame["time"] = pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True)
    synoptic = frame["time"].dt.hour.isin((0, 6, 12, 18)) & frame["time"].dt.minute.eq(0)
    frame = frame.loc[
        frame["SEASON"].between(start_year, end_year)
        & frame["TRACK_TYPE"].str.lower().eq("main")
        & synoptic
    ].copy()

    # USA_WIND can come from HURDAT or another basin agency after a basin crossing.
    frame.loc[~frame["USA_AGENCY"].eq("jtwc_wp"), "USA_WIND"] = np.nan
    frame["lon180"] = ((frame["LON"] + 180.0) % 360.0) - 180.0
    frame["row_id"] = np.arange(len(frame), dtype=np.int64)

    padded_flags = frame["IFLAG"].str.pad(width=max(IFLAG_INDEX.values()) + 1, side="right", fillchar="_")
    for agency, index in IFLAG_INDEX.items():
        frame[f"flag_{agency}"] = padded_flags.str[index]
        frame[f"original_{agency}"] = frame[f"flag_{agency}"].isin(("O", "V"))

    duplicated = frame.duplicated(("SID", "time"), keep=False)
    if duplicated.any():
        availability = frame[list(WIND_COLUMNS.values())].notna().sum(axis=1)
        frame = (
            frame.assign(_availability=availability)
            .sort_values(["SID", "time", "_availability"], ascending=[True, True, False])
            .drop_duplicates(("SID", "time"), keep="first")
            .drop(columns="_availability")
        )
    return frame.sort_values(["SID", "time"]).reset_index(drop=True)


def normalize_winds(frame: pd.DataFrame, usa_factor: float, cma_factor: float) -> pd.DataFrame:
    result = frame.copy()
    factors = {
        "JTWC": usa_factor,
        "JMA": 1.0,
        "CMA": cma_factor,
        "HKO": 1.0,
        "KMA": 1.0,
    }
    for agency in AGENCIES:
        result[agency] = result[WIND_COLUMNS[agency]] * KT_TO_MS * factors[agency]
    return result


def classify_intensity(values: pd.Series) -> pd.Series:
    return pd.cut(
        values,
        bins=INTENSITY_BINS,
        labels=INTENSITY_LABELS,
        right=False,
        ordered=True,
    )


def classify_distance(values: pd.Series) -> pd.Series:
    return pd.cut(
        values,
        bins=DISTANCE_BINS,
        labels=DISTANCE_LABELS,
        right=False,
        ordered=True,
    )


def classify_era(years: pd.Series) -> pd.Series:
    conditions = (
        years.between(1987, 1999),
        years.between(2000, 2009),
        years.between(2010, 2019),
        years.between(2020, 2024),
    )
    labels = ("1987-1999", "2000-2009", "2010-2019", "2020-2024")
    return pd.Series(np.select(conditions, labels, default="outside"), index=years.index)


def _stage_for_complete_records(frame: pd.DataFrame) -> pd.Series:
    ordered = frame.sort_values(["SID", "time"]).copy()
    grouped = ordered.groupby("SID", sort=False)
    previous_value = grouped["common_intensity_ms"].shift(1)
    next_value = grouped["common_intensity_ms"].shift(-1)
    previous_time = grouped["time"].shift(1)
    next_time = grouped["time"].shift(-1)
    exact_neighbors = previous_time.notna() & next_time.notna()
    valid_index = exact_neighbors.loc[exact_neighbors].index
    exact_neighbors.loc[valid_index] = (
        ordered.loc[valid_index, "time"].astype("int64").to_numpy()
        - previous_time.loc[valid_index].astype("int64").to_numpy()
        == 6 * 60 * 60 * 1_000_000_000
    ) & (
        next_time.loc[valid_index].astype("int64").to_numpy()
        - ordered.loc[valid_index, "time"].astype("int64").to_numpy()
        == 6 * 60 * 60 * 1_000_000_000
    )
    delta = (next_value - previous_value).where(exact_neighbors)
    stage = pd.Series(pd.NA, index=ordered.index, dtype="string")
    stage.loc[delta.ge(2.5)] = "intensifying"
    stage.loc[delta.le(-2.5)] = "weakening"
    stage.loc[delta.gt(-2.5) & delta.lt(2.5)] = "steady"
    return stage.reindex(frame.index)


def analysis_sample(
    normalized: pd.DataFrame,
    *,
    start_year: int = 2001,
    end_year: int = 2024,
    original_only: bool = True,
    tropical_only: bool = True,
    ocean_only: bool = False,
    require_stage: bool = False,
) -> pd.DataFrame:
    values = normalized[list(AGENCIES)]
    complete = values.notna().all(axis=1)
    if original_only:
        original = normalized[[f"original_{agency}" for agency in AGENCIES]].all(axis=1)
    else:
        original = pd.Series(True, index=normalized.index)
    nature = normalized["NATURE"].eq("TS") if tropical_only else pd.Series(True, index=normalized.index)
    period = normalized["SEASON"].between(start_year, end_year)
    common = values.median(axis=1, skipna=False)
    selected = normalized.loc[complete & original & nature & period & common.ge(17.2)].copy()
    selected["common_intensity_ms"] = selected[list(AGENCIES)].median(axis=1)
    selected["disagreement_ms"] = selected[list(AGENCIES)].std(axis=1, ddof=1)
    selected["relative_disagreement"] = selected["disagreement_ms"] / selected["common_intensity_ms"]
    selected["intensity_bin"] = classify_intensity(selected["common_intensity_ms"])
    selected["era"] = classify_era(selected["SEASON"])
    selected["stage"] = _stage_for_complete_records(selected)
    if ocean_only:
        selected = selected.loc[~selected["is_land"]].copy()
    if require_stage:
        selected = selected.loc[selected["stage"].notna()].copy()
    return selected.sort_values(["SID", "time"]).reset_index(drop=True)


def availability_eligible_frame(
    normalized: pd.DataFrame,
    *,
    start_year: int = 2001,
    end_year: int = 2024,
    tropical_only: bool = True,
) -> pd.DataFrame:
    values = normalized[list(AGENCIES)]
    available_count = values.notna().sum(axis=1)
    available_median = values.median(axis=1, skipna=True)
    mask = (
        normalized["SEASON"].between(start_year, end_year)
        & available_count.ge(1)
        & available_median.ge(17.2)
    )
    if tropical_only:
        mask &= normalized["NATURE"].eq("TS")
    result = normalized.loc[mask].copy()
    result["available_count"] = available_count.loc[mask]
    result["available_median_ms"] = available_median.loc[mask]
    result["intensity_bin"] = classify_intensity(result["available_median_ms"])
    result["era"] = classify_era(result["SEASON"])
    result["distance_bin"] = classify_distance(result["coast_distance_km"])
    return result


def conversion_table(usa_factor: float, cma_factor: float) -> list[dict[str, object]]:
    factors = {"JTWC": usa_factor, "JMA": 1.0, "CMA": cma_factor, "HKO": 1.0, "KMA": 1.0}
    sources = {
        "JTWC": "IBTrACS field documentation; scenario factor",
        "JMA": "IBTrACS field documentation",
        "CMA": "IBTrACS field documentation; WMO gust-factor ratio",
        "HKO": "IBTrACS field documentation",
        "KMA": "IBTrACS field documentation",
    }
    return [
        {
            "agency": agency,
            "source_column": WIND_COLUMNS[agency],
            "native_window_minutes": NATIVE_WINDOWS_MIN[agency],
            "target_window_minutes": 10,
            "multiplier": factors[agency],
            "unit_multiplier_kt_to_ms": KT_TO_MS,
            "source": sources[agency],
        }
        for agency in AGENCIES
    ]


def require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
