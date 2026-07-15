#!/usr/bin/env python3
"""Audit cyclone-polar CyclObs SAR wind profiles for multiple wind maxima."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Sequence
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CONFIG = ROOT / "config" / "erc_observation_sources.json"
DEFAULT_OBSERVATION_CONTRACT = (
    ROOT / "config" / "eyewall_structure_observation_contract.json"
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require_profile_dependencies() -> None:
    try:
        import h5py  # noqa: F401  # type: ignore[import-not-found]
        import numpy  # noqa: F401  # type: ignore[import-not-found]
    except ImportError as error:
        raise SystemExit(
            "Install typhoon/markov/requirements-research.txt before profile extraction"
        ) from error


def build_api_url(
    api_url: str,
    *,
    sid: str,
    start_date: str,
    stop_date: str,
) -> str:
    columns = (
        "sid,cyclone_name,mission,instrument,acquisition_start_time,"
        "eye_in_acq,analysis_vmax,analysis_rmax,"
        "analysis_center_quality_flag,l2_sar_fix_product_url"
    )
    query = urllib.parse.urlencode(
        {
            "sid": sid,
            "acquisition_start_time": start_date,
            "acquisition_stop_time": stop_date,
            "include_cols": columns,
        }
    )
    return f"{api_url}?{query}"


def fetch_bytes(url: str, timeout_seconds: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "typhoon-markov-research-audit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def parse_api_rows(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    required = {
        "sid",
        "acquisition_start_time",
        "eye_in_acq",
        "analysis_center_quality_flag",
        "l2_sar_fix_product_url",
    }
    if not rows:
        return []
    missing = required.difference(rows[0])
    if missing:
        raise ValueError(f"CyclObs API response is missing columns: {sorted(missing)}")
    return rows


def moving_average(
    values: Sequence[float | None], window_points: int
) -> list[float | None]:
    if window_points <= 0 or window_points % 2 == 0:
        raise ValueError("moving-average window must be a positive odd integer")
    radius = window_points // 2
    required = math.ceil(0.8 * window_points)
    smoothed: list[float | None] = []
    for index in range(len(values)):
        sample = [
            value
            for value in values[max(0, index - radius): index + radius + 1]
            if value is not None and math.isfinite(value)
        ]
        smoothed.append(sum(sample) / len(sample) if len(sample) >= required else None)
    return smoothed


def detect_profile_peaks(
    radius_km: Sequence[float],
    profile_ms: Sequence[float | None],
    coverage_fraction: Sequence[float],
    *,
    search_bounds_km: tuple[float, float],
    smoothing_window_km: float,
    prominence_window_km: float,
    minimum_coverage_fraction: float,
    minimum_prominence_ms: float,
) -> tuple[list[dict[str, float]], list[float | None]]:
    if not (len(radius_km) == len(profile_ms) == len(coverage_fraction)):
        raise ValueError("radius, profile, and coverage arrays must have equal length")
    if len(radius_km) < 3:
        raise ValueError("at least three radial samples are required")
    spacings = [
        radius_km[index + 1] - radius_km[index]
        for index in range(len(radius_km) - 1)
    ]
    spacing_km = sorted(spacings)[len(spacings) // 2]
    if spacing_km <= 0.0 or any(
        abs(value - spacing_km) > 1.0e-6 for value in spacings
    ):
        raise ValueError("CyclObs radial coordinate must be uniformly increasing")
    smoothing_points = max(1, round(smoothing_window_km / spacing_km))
    if smoothing_points % 2 == 0:
        smoothing_points += 1
    prominence_points = max(1, round(prominence_window_km / spacing_km))
    smoothed = moving_average(profile_ms, smoothing_points)

    peaks: list[dict[str, float]] = []
    for index in range(1, len(radius_km) - 1):
        radius = radius_km[index]
        value = smoothed[index]
        left_neighbor = smoothed[index - 1]
        right_neighbor = smoothed[index + 1]
        if not search_bounds_km[0] <= radius <= search_bounds_km[1]:
            continue
        if coverage_fraction[index] < minimum_coverage_fraction:
            continue
        if value is None or left_neighbor is None or right_neighbor is None:
            continue
        if not (value >= left_neighbor and value > right_neighbor):
            continue
        left_values = [
            item
            for item in smoothed[max(0, index - prominence_points): index + 1]
            if item is not None
        ]
        right_values = [
            item
            for item in smoothed[
                index: min(len(smoothed), index + prominence_points + 1)
            ]
            if item is not None
        ]
        if not left_values or not right_values:
            continue
        prominence = value - max(min(left_values), min(right_values))
        if prominence < minimum_prominence_ms:
            continue
        peaks.append(
            {
                "radius_km": round(radius, 6),
                "smoothed_axisymmetric_wind_ms": round(value, 6),
                "prominence_ms": round(prominence, 6),
                "valid_azimuth_fraction": round(coverage_fraction[index], 6),
            }
        )
    return peaks, smoothed


def select_dual_peak_pair(
    peaks: Sequence[dict[str, float]],
    *,
    minimum_prominence_ms: float,
    minimum_separation_km: float,
) -> list[dict[str, float]]:
    eligible = [
        peak for peak in peaks if peak["prominence_ms"] >= minimum_prominence_ms
    ]
    pairs: list[tuple[float, list[dict[str, float]]]] = []
    for left_index, left in enumerate(eligible):
        for right in eligible[left_index + 1:]:
            separation = right["radius_km"] - left["radius_km"]
            if separation >= minimum_separation_km:
                pairs.append((left["prominence_ms"] + right["prominence_ms"], [left, right]))
    if not pairs:
        return []
    return max(pairs, key=lambda item: item[0])[1]


def decode_attribute(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "item"):
        return decode_attribute(value.item())
    return value


def scalar_float(group: Any, name: str) -> float:
    return float(group[name][()])


def finite_or_none(value: float) -> float | None:
    return round(value, 6) if math.isfinite(value) else None


def audit_netcdf_pass(
    payload: bytes,
    row: dict[str, str],
    contract: dict[str, Any],
) -> dict[str, Any]:
    try:
        import h5py  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "CyclObs profile extraction requires requirements-research.txt"
        ) from error

    sar = contract["cyclobs_sar"]
    with h5py.File(io.BytesIO(payload), "r") as handle:
        wind = handle["wind_speed"][0, ...]
        quality = handle["quality_level"][0, ...]
        radius_km_array = handle["rad"][...] / 1000.0
        accepted = np.where(
            (quality >= sar["minimum_wind_cell_quality_level"])
            & np.isfinite(wind),
            wind,
            np.nan,
        )
        counts = np.sum(np.isfinite(accepted), axis=1)
        totals = np.nansum(accepted, axis=1)
        profile_array = np.divide(
            totals,
            counts,
            out=np.full(totals.shape, np.nan, dtype=float),
            where=counts > 0,
        )
        coverage_array = counts / accepted.shape[1]

        upper_radius = float(sar["radial_search_bounds_km"][1])
        keep = radius_km_array <= upper_radius
        radius_km = [float(value) for value in radius_km_array[keep]]
        profile_ms = [
            finite_or_none(float(value)) for value in profile_array[keep]
        ]
        coverage_fraction = [
            round(float(value), 6) for value in coverage_array[keep]
        ]
        peaks, smoothed = detect_profile_peaks(
            radius_km,
            profile_ms,
            coverage_fraction,
            search_bounds_km=tuple(sar["radial_search_bounds_km"]),
            smoothing_window_km=float(sar["smoothing_window_km"]),
            prominence_window_km=float(sar["prominence_window_km"]),
            minimum_coverage_fraction=float(
                sar["minimum_azimuthal_coverage_fraction"]
            ),
            minimum_prominence_ms=float(
                sar["exploratory_peak_minimum_prominence_ms"]
            ),
        )
        dual_pair = select_dual_peak_pair(
            peaks,
            minimum_prominence_ms=float(sar["dual_peak_minimum_prominence_ms"]),
            minimum_separation_km=float(sar["dual_peak_minimum_separation_km"]),
        )
        center_quality = scalar_float(handle, "center_quality_flag")
        eye_in_acquisition = row["eye_in_acq"].strip().lower() == "true"
        dual_candidate = bool(dual_pair) and (
            center_quality < sar["maximum_center_quality_flag_exclusive"]
        )
        if sar["dual_peak_requires_eye_in_acquisition"]:
            dual_candidate = dual_candidate and eye_in_acquisition

        return {
            "source": {
                "url": row["l2_sar_fix_product_url"],
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            },
            "metadata": {
                "sid": row["sid"],
                "cyclone_name": row.get("cyclone_name", ""),
                "mission": row.get("mission", ""),
                "instrument": row.get("instrument", ""),
                "valid_at_utc": row["acquisition_start_time"] + "Z",
                "available_at_utc": None,
                "eye_in_acquisition": eye_in_acquisition,
                "processing_version": {
                    "algorithm": decode_attribute(
                        handle.attrs.get("Algorithm version", "")
                    ),
                    "cyclobs": decode_attribute(
                        handle.attrs.get("CyclObs version", "")
                    ),
                },
            },
            "center": {
                "latitude_deg": scalar_float(handle, "lat_storm_center"),
                "longitude_deg": scalar_float(handle, "lon_storm_center"),
                "quality_flag": center_quality,
            },
            "provider_summary": {
                "maximum_sustained_wind_ms": scalar_float(handle, "msw"),
                "radius_of_maximum_wind_km": scalar_float(handle, "rmw") / 1000.0,
                "axisymmetric_maximum_wind_ms": scalar_float(handle, "vmx"),
                "axisymmetric_maximum_radius_km": scalar_float(handle, "rmx")
                / 1000.0,
            },
            "axisymmetric_profile": {
                "radius_km": radius_km,
                "mean_wind_ms": profile_ms,
                "smoothed_mean_wind_ms": [
                    finite_or_none(value) if value is not None else None
                    for value in smoothed
                ],
                "valid_azimuth_fraction": coverage_fraction,
                "wind_time_semantics": "instantaneous",
                "source_effective_resolution_km": 3.0,
                "polar_grid_spacing": {
                    "radius_km": radius_km[1] - radius_km[0],
                    "azimuth_deg": 1.0,
                },
            },
            "structure_diagnostic": {
                "candidate_peaks": peaks,
                "selected_dual_peak_pair": dual_pair,
                "dual_peak_candidate": dual_candidate,
                "label_semantics": "exploratory SAR wind-structure candidate; not an ERC event label",
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sid", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--stop-date", required=True)
    parser.add_argument("--source-config", type=Path, default=DEFAULT_SOURCE_CONFIG)
    parser.add_argument(
        "--observation-contract",
        type=Path,
        default=DEFAULT_OBSERVATION_CONTRACT,
    )
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    require_profile_dependencies()
    sources = load_json(args.source_config)
    contract = load_json(args.observation_contract)
    api_url = sources["sources"]["cyclobs_sar"]["api_url"]
    query_url = build_api_url(
        api_url,
        sid=args.sid,
        start_date=args.start_date,
        stop_date=args.stop_date,
    )
    api_payload = fetch_bytes(query_url, args.timeout_seconds)
    rows = parse_api_rows(api_payload)
    rows.sort(key=lambda row: row["acquisition_start_time"])

    audited_passes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for row in rows:
        try:
            payload = fetch_bytes(
                row["l2_sar_fix_product_url"], args.timeout_seconds
            )
            audited_passes.append(audit_netcdf_pass(payload, row, contract))
        except Exception as error:  # Preserve the remaining independent passes.
            failures.append(
                {
                    "valid_at_utc": row.get("acquisition_start_time", ""),
                    "source_url": row.get("l2_sar_fix_product_url", ""),
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    dual_times = [
        item["metadata"]["valid_at_utc"]
        for item in audited_passes
        if item["structure_diagnostic"]["dual_peak_candidate"]
    ]
    report = {
        "report_id": f"cyclobs-structure-{args.sid.lower()}",
        "status": "research-structural-observation",
        "authoritative_forecast": False,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "implementation": {
            "path": str(Path(__file__).resolve()),
            "sha256": file_sha256(Path(__file__).resolve()),
        },
        "query": {
            "url": query_url,
            "response_bytes": len(api_payload),
            "response_sha256": sha256_bytes(api_payload),
            "requested_sid": args.sid,
            "requested_date_interval": [args.start_date, args.stop_date],
            "stop_date_semantics": "CyclObs API upper date boundary",
        },
        "contracts": {
            "source_registry": str(args.source_config.resolve()),
            "source_registry_sha256": file_sha256(args.source_config),
            "observation_contract": str(args.observation_contract.resolve()),
            "observation_contract_sha256": file_sha256(
                args.observation_contract
            ),
        },
        "summary": {
            "api_pass_count": len(rows),
            "audited_pass_count": len(audited_passes),
            "failed_pass_count": len(failures),
            "eye_in_acquisition_count": sum(
                item["metadata"]["eye_in_acquisition"] for item in audited_passes
            ),
            "dual_peak_candidate_count": len(dual_times),
            "dual_peak_candidate_times_utc": dual_times,
        },
        "passes": audited_passes,
        "failures": failures,
        "interpretation_limits": [
            "A SAR dual-peak candidate is a direct wind-structure observation at one overpass time; an ERC label requires temporal formation, contraction, and replacement evidence.",
            "CyclObs does not expose a product publication timestamp in this API response. Operational ingestion must record first-seen time as available_at_utc.",
            "The exploratory prominence and separation thresholds require validation on a sealed, independently labeled sample before entering a forecast model.",
        ],
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
