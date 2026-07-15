"""CyclObs structure time-series extraction for retrospective case figures."""

from __future__ import annotations

from typing import Any, Sequence

import pandas as pd


def _widest_peak_pair(peaks: Sequence[dict[str, float]]) -> list[dict[str, float]]:
    pairs: list[tuple[float, list[dict[str, float]]]] = []
    for left_index, left in enumerate(peaks):
        for right in peaks[left_index + 1 :]:
            separation = right["radius_km"] - left["radius_km"]
            pairs.append((separation, [left, right]))
    if not pairs:
        return []
    return max(pairs, key=lambda item: item[0])[1]


def build_cyclobs_structure_series(
    audit: dict[str, Any],
    *,
    maximum_center_quality_flag_exclusive: float = 2.0,
    minimum_peak_separation_km: float = 30.0,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in audit["passes"]:
        metadata = item["metadata"]
        provider = item["provider_summary"]
        diagnostic = item["structure_diagnostic"]
        eye = bool(metadata["eye_in_acquisition"])
        center_quality = float(item["center"]["quality_flag"])
        quality_eligible = eye and center_quality < maximum_center_quality_flag_exclusive
        peaks = diagnostic["candidate_peaks"]
        selected = diagnostic["selected_dual_peak_pair"]
        widest = _widest_peak_pair(peaks)
        subthreshold_pair = bool(
            quality_eligible
            and not diagnostic["dual_peak_candidate"]
            and widest
            and widest[1]["radius_km"] - widest[0]["radius_km"]
            >= minimum_peak_separation_km
        )
        display_pair = selected or (widest if subthreshold_pair else [])
        wind_ms = float(provider["axisymmetric_maximum_wind_ms"])
        radius_km = float(provider["axisymmetric_maximum_radius_km"])
        rows.append(
            {
                "time": pd.to_datetime(metadata["valid_at_utc"], utc=True),
                "mission": metadata["mission"],
                "eye_in_acquisition": eye,
                "center_quality_flag": center_quality,
                "quality_eligible": quality_eligible,
                "axisymmetric_maximum_wind_ms": wind_ms,
                "axisymmetric_maximum_radius_km": radius_km,
                "v_times_r_1e6_m2s": wind_ms * radius_km / 1000.0,
                "dual_peak_candidate": bool(diagnostic["dual_peak_candidate"]),
                "subthreshold_pair": subthreshold_pair,
                "inner_peak_radius_km": display_pair[0]["radius_km"] if display_pair else None,
                "outer_peak_radius_km": display_pair[1]["radius_km"] if display_pair else None,
                "inner_peak_prominence_ms": display_pair[0]["prominence_ms"] if display_pair else None,
                "outer_peak_prominence_ms": display_pair[1]["prominence_ms"] if display_pair else None,
            }
        )
    return pd.DataFrame(rows).sort_values("time").reset_index(drop=True)


def observed_dual_ring_intervals(series: pd.DataFrame) -> list[dict[str, Any]]:
    """Group dual observations; only an eligible negative pass separates intervals."""

    intervals: list[dict[str, Any]] = []
    current: list[pd.Series] = []
    for _, row in series.loc[series["quality_eligible"]].iterrows():
        if bool(row["dual_peak_candidate"]):
            current.append(row)
            continue
        if current:
            intervals.append(_summarize_interval(current))
            current = []
    if current:
        intervals.append(_summarize_interval(current))
    return intervals


def _summarize_interval(rows: Sequence[pd.Series]) -> dict[str, Any]:
    return {
        "start_utc": rows[0]["time"].isoformat(),
        "end_utc": rows[-1]["time"].isoformat(),
        "dual_peak_overpass_count": len(rows),
        "observed_times_utc": [row["time"].isoformat() for row in rows],
        "interpretation": "observed dual-ring interval; gaps remain unobserved",
    }
