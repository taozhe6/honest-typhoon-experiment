"""Evidence-layered coverage audit for sparse inner-core observations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Sequence
from urllib.parse import urlparse


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_cyclobs_passes(
    audit: dict[str, Any],
    *,
    maximum_center_quality_exclusive: float,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in audit["passes"]:
        metadata = item["metadata"]
        center = item["center"]
        diagnostic = item["structure_diagnostic"]
        eye_covered = bool(metadata["eye_in_acquisition"])
        center_quality = float(center["quality_flag"])
        reasons: list[str] = []
        if not eye_covered:
            reasons.append("eye_not_in_acquisition")
        if center_quality >= maximum_center_quality_exclusive:
            reasons.append("center_quality_not_below_threshold")
        quality_eligible = not reasons
        selected_pair = diagnostic.get("selected_dual_peak_pair", [])
        dual_observed = bool(quality_eligible and diagnostic["dual_peak_candidate"])
        if dual_observed and len(selected_pair) != 2:
            raise ValueError("dual-peak scene must carry exactly two selected peaks")
        source = item["source"]
        source_name = PurePosixPath(urlparse(source["url"]).path).name
        records.append(
            {
                "scene_id": source_name,
                "source_sha256": source["sha256"],
                "valid_at_utc": parse_utc(metadata["valid_at_utc"]).isoformat(),
                "mission": metadata["mission"],
                "center_latitude_deg": float(center["latitude_deg"]),
                "center_longitude_deg": float(center["longitude_deg"]),
                "eye_in_acquisition": eye_covered,
                "center_quality_flag": center_quality,
                "quality_eligible": quality_eligible,
                "ineligibility_reasons": reasons,
                "dual_ring_structure_observed": dual_observed,
                "inner_peak_radius_km": (
                    float(selected_pair[0]["radius_km"]) if dual_observed else None
                ),
                "outer_peak_radius_km": (
                    float(selected_pair[1]["radius_km"]) if dual_observed else None
                ),
                "inner_peak_prominence_ms": (
                    float(selected_pair[0]["prominence_ms"]) if dual_observed else None
                ),
                "outer_peak_prominence_ms": (
                    float(selected_pair[1]["prominence_ms"]) if dual_observed else None
                ),
                "evidence_class": (
                    "primary_double_ring_wind_structure"
                    if dual_observed
                    else (
                        "primary_structurally_eligible_no_operator_trigger"
                        if quality_eligible
                        else "primary_indeterminate_quality_or_spatial_coverage"
                    )
                ),
            }
        )
    return sorted(records, key=lambda record: record["valid_at_utc"])


def observed_dual_ring_intervals(
    passes: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group positive scenes; an eligible operator-negative scene closes an interval."""

    intervals: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for record in passes:
        if not record["quality_eligible"]:
            continue
        if record["dual_ring_structure_observed"]:
            current.append(record)
            continue
        if current:
            intervals.append(_summarize_interval(current))
            current = []
    if current:
        intervals.append(_summarize_interval(current))
    return intervals


def _summarize_interval(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "start_utc": records[0]["valid_at_utc"],
        "end_utc": records[-1]["valid_at_utc"],
        "positive_scene_count": len(records),
        "scene_ids": [record["scene_id"] for record in records],
        "observed_times_utc": [record["valid_at_utc"] for record in records],
        "evidence_class": "primary_observed_double_ring_interval",
        "interpretation": (
            "At least one double-ring wind-structure interval is observed; "
            "unobserved gaps prevent ERC-cycle boundary counting."
        ),
    }


def classify_narrative_windows(
    passes: Sequence[dict[str, Any]],
    windows: Sequence[dict[str, Any]],
    *,
    maximum_sufficient_sampling_gap_hours: float,
    operator_detection_rate_available: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for window in windows:
        start = parse_utc(window["start_utc"])
        end = parse_utc(window["end_utc"])
        if end <= start:
            raise ValueError(f"window {window['id']} must have positive duration")
        temporal = [
            record
            for record in passes
            if start <= parse_utc(record["valid_at_utc"]) < end
        ]
        eligible = [record for record in temporal if record["quality_eligible"]]
        positives = [
            record for record in eligible if record["dual_ring_structure_observed"]
        ]
        gap_hours = _maximum_gap_hours(start, end, eligible)
        sufficient_sampling = bool(
            eligible
            and gap_hours <= maximum_sufficient_sampling_gap_hours
            and operator_detection_rate_available
        )
        if positives:
            verdict = "primary_double_ring_structure_observed_in_window"
        elif temporal:
            verdict = "primary_indeterminate"
        else:
            verdict = "no_primary_temporal_coverage"
        results.append(
            {
                **window,
                "temporal_pass_count": len(temporal),
                "quality_eligible_pass_count": len(eligible),
                "double_ring_positive_scene_count": len(positives),
                "temporal_scene_ids": [record["scene_id"] for record in temporal],
                "eligible_scene_ids": [record["scene_id"] for record in eligible],
                "positive_scene_ids": [record["scene_id"] for record in positives],
                "maximum_quality_eligible_sampling_gap_hours": gap_hours,
                "sufficient_to_assert_physical_absence": sufficient_sampling,
                "primary_verdict": verdict,
            }
        )
    return results


def _maximum_gap_hours(
    start: datetime,
    end: datetime,
    eligible: Sequence[dict[str, Any]],
) -> float:
    times = [start, *(parse_utc(record["valid_at_utc"]) for record in eligible), end]
    return max(
        (right - left).total_seconds() / 3600.0
        for left, right in zip(times, times[1:])
    )


def evidence_tally(
    intervals: Sequence[dict[str, Any]],
    windows: Sequence[dict[str, Any]],
) -> dict[str, int]:
    return {
        "confirmed_primary_double_ring_intervals": len(intervals),
        "indeterminate_narrative_windows": sum(
            window["primary_verdict"] == "primary_indeterminate"
            for window in windows
        ),
        "narrative_windows_without_primary_temporal_coverage": sum(
            window["primary_verdict"] == "no_primary_temporal_coverage"
            for window in windows
        ),
    }
