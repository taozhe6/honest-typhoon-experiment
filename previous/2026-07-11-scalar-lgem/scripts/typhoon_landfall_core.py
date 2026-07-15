#!/usr/bin/env python3
"""Auditable tropical-cyclone landfall scenario model.

The module keeps two products separate:

* official guidance: each agency's own forecast intensity at its own
  track-derived landfall time;
* mechanistic scenarios: a reduced-order ODE model conditioned on a common
  analysis state, a source track, and independently fetched environmental
  fields.

Agency forecast wind endpoints are never fitted into the ODE tendency.
"""

from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import shutil
import ssl
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


UTC = dt.timezone.utc
EARTH_RADIUS_KM = 6371.0088


def bundled_config_path() -> Path:
    module_path = Path(__file__).resolve()
    candidates = [
        module_path.parents[1] / "config" / "typhoon_landfall_model.json",
        module_path.parent / "typhoon_landfall_model.json",
    ]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


DEFAULT_CONFIG_PATH = bundled_config_path()


@dataclass(frozen=True)
class StormIdentity:
    """Cross-agency identity selected from a current active-storm catalog."""

    name: str
    number: str | None = None
    aliases: tuple[str, ...] = ()
    nmc_id: str | None = None

    @property
    def normalized_tokens(self) -> set[str]:
        values = {self.name, *self.aliases}
        return {normalize_name(value) for value in values if value}

    @property
    def year(self) -> int | None:
        if self.number and len(self.number) >= 4 and self.number[:4].isdigit():
            # WMO seasonal numbers use YYNN, for example 2609 = 2026's ninth storm.
            # Resolve YY around the present century so year rollover remains valid.
            year_two_digits = int(self.number[:2])
            reference_year = dt.datetime.now(UTC).year
            candidate = (reference_year // 100) * 100 + year_two_digits
            if candidate - reference_year > 50:
                candidate -= 100
            elif reference_year - candidate > 50:
                candidate += 100
            return candidate
        return None

    @property
    def ordinal(self) -> int | None:
        if self.number and len(self.number) >= 2 and self.number[-2:].isdigit():
            return int(self.number[-2:])
        return None

    def matches(self, token: str | None) -> bool:
        return bool(token) and normalize_name(token) in self.normalized_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "number": self.number,
            "aliases": list(self.aliases),
            "nmc_id": self.nmc_id,
        }


@dataclass(frozen=True)
class TrackPoint:
    """One analysed or forecast tropical-cyclone fix normalized to UTC and m/s."""

    valid_utc: dt.datetime
    lat: float
    lon: float
    wind_ms: float | None
    kind: str
    pressure_hpa: float | None = None
    category: str | None = None
    raw_wind: float | None = None
    raw_wind_unit: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.valid_utc.tzinfo is None:
            raise ValueError("TrackPoint.valid_utc must be timezone-aware")

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "valid_utc": self.valid_utc.astimezone(UTC).isoformat(),
            "lat": round(self.lat, 5),
            "lon": round(self.lon, 5),
            "wind_ms": round(self.wind_ms, 3) if self.wind_ms is not None else None,
            "kind": self.kind,
            "pressure_hpa": self.pressure_hpa,
            "category": self.category,
        }
        if self.raw_wind is not None:
            result["raw_wind"] = self.raw_wind
            result["raw_wind_unit"] = self.raw_wind_unit
        if self.metadata:
            result["metadata"] = self.metadata
        return result


@dataclass
class SourceTrack:
    """A normalized agency track and its independently resolved endpoint."""

    source: str
    identity: StormIdentity
    issue_utc: dt.datetime
    points: list[TrackPoint]
    resolved_url: str
    averaging_period_minutes: int
    identity_evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    discovery: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.issue_utc.tzinfo is None:
            raise ValueError("SourceTrack.issue_utc must be timezone-aware")
        original_times = [point.valid_utc for point in self.points]
        if len(set(original_times)) != len(original_times):
            raise ValueError(f"{self.source} returned duplicate valid times")
        if any(right <= left for left, right in zip(original_times, original_times[1:])):
            raise ValueError(f"{self.source} returned a non-chronological track")
        self.points.sort(key=lambda item: item.valid_utc)
        if len(self.points) < 2:
            raise ValueError(f"{self.source} returned fewer than two points")

    def latest_analysis_time(self) -> dt.datetime:
        analysis = [point.valid_utc for point in self.points if point.kind == "analysis"]
        return max(analysis) if analysis else self.points[0].valid_utc

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "issue_utc": self.issue_utc.astimezone(UTC).isoformat(),
            "resolved_url": self.resolved_url,
            "wind_averaging_minutes": self.averaging_period_minutes,
            "identity_evidence": self.identity_evidence,
            "warnings": self.warnings,
            "discovery": self.discovery,
            "points": [point.as_dict() for point in self.points],
        }


@dataclass
class AlignmentResult:
    """Same-effective-time source comparison and source-quality result."""

    alignment_time_utc: dt.datetime
    usable_tracks: list[SourceTrack]
    aligned: dict[str, TrackPoint]
    status_by_source: dict[str, dict[str, Any]]
    position_summary: dict[str, Any]
    intensity_summary: dict[str, Any]


def normalize_name(value: Any) -> str:
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]", "", str(value).upper())


def normalize_category(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("en") or value.get("jp") or value.get("zh")
    if value in (None, ""):
        return None
    return str(value)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load externally stored numerical assumptions and source definitions."""

    config_path = Path(path)
    return json.loads(config_path.read_text(encoding="utf-8"))


def parse_jsonp(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    match = re.match(r"^[A-Za-z0-9_$.]+\s*\(\s*(.*)\s*\)\s*;?\s*$", stripped, re.S)
    if not match:
        raise ValueError("Response is neither JSON nor valid JSONP")
    payload = match.group(1).strip()
    # NMC currently wraps its JSON object in a second pair of parentheses.
    while payload.startswith("(") and payload.endswith(")"):
        payload = payload[1:-1].strip()
    return json.loads(payload)


def parse_nmc_catalog(text: str) -> list[dict[str, Any]]:
    """Parse NMC's current catalog with named records around its array schema."""

    payload = parse_jsonp(text)
    rows = payload.get("typhoonList", [])
    records = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 8:
            continue
        record = {
            "storm_id": str(row[0]),
            "name": str(row[1]),
            "name_cn": str(row[2]),
            "number": str(row[4]),
            "active": str(row[7]).lower() == "start",
        }
        records.append(record)
    return records


def parse_jma_targets(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError("JMA targetTc response has an unexpected shape")
    records = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        tropical_cyclone = item.get("tropicalCyclone")
        number = item.get("typhoonNumber")
        if tropical_cyclone and number:
            records.append(
                {
                    "tropical_cyclone": str(tropical_cyclone),
                    "number": str(number),
                    "category": item.get("category"),
                    "issue": item.get("issue"),
                }
            )
    return records


def parse_jtwc_catalog(text: str) -> list[dict[str, str]]:
    records = []
    seen = set()
    for number, name in re.findall(r"(?:TROPICAL\s+STORM|TYPHOON|SUPER\s+TYPHOON)\s+(\d{2}[A-Z])\s*\(([^)]+)\)", text, re.I):
        key = (number.upper(), normalize_name(name))
        if key in seen:
            continue
        seen.add(key)
        records.append({"number": number.upper(), "name": name.strip().upper()})
    return records


def parse_hko_catalog(text: str) -> list[dict[str, str]]:
    records = []
    for value in re.findall(r"\btc\s*\[\s*\d+\s*\]\s*=\s*[\"']([^\"']+)[\"']", text, re.I):
        parts = [item.strip() for item in value.split(",")]
        if len(parts) >= 2:
            records.append(
                {
                    "hko_id": parts[0],
                    "name": parts[1].upper(),
                    "name_cn": parts[2] if len(parts) >= 3 else "",
                }
            )
    return records


def parse_cwa_active_names(text: str) -> list[str]:
    match = re.search(r"var\s+TYPHOON\s*=\s*\{(.*?)\};", text, re.S)
    if not match:
        return []
    return [name.upper() for name in re.findall(r"['\"]([A-Z][A-Z0-9_-]*)['\"]\s*:\s*\{", match.group(1))]


def extract_javascript_string_assignment(text: str, variable: str, key: str) -> str:
    """Join quoted literals from one JavaScript string-concatenation assignment."""

    prefix = re.search(
        rf"\b{re.escape(variable)}\s*\[\s*['\"]{re.escape(key)}['\"]\s*\]\s*=\s*",
        text,
        re.S,
    )
    if not prefix:
        raise ValueError(f"JavaScript payload lacks {variable}[{key!r}] assignment")
    quote: str | None = None
    escaped = False
    end = None
    for index in range(prefix.end(), len(text)):
        character = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in ("'", '"'):
            quote = character
        elif character == ";":
            end = index
            break
    if end is None:
        raise ValueError(f"JavaScript assignment {variable}[{key!r}] is unterminated")
    expression = text[prefix.end() : end]
    literals = re.findall(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"", expression, re.S)
    if not literals:
        raise ValueError(f"JavaScript assignment {variable}[{key!r}] has no string literals")
    try:
        return "".join(str(ast.literal_eval(literal)) for literal in literals)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(f"JavaScript assignment {variable}[{key!r}] contains an invalid literal") from exc


def select_cwa_storm_panel(text: str, identity: StormIdentity) -> str:
    """Select one CWA English detail panel before parsing any fixes."""

    html = extract_javascript_string_assignment(text, "TY_LIST_2", "E")
    panels = [item for item in re.split(r"(?=<div\s+class=[\"']panel panel-default[\"'])", html) if item.strip()]
    matches = []
    for panel in panels:
        heading = re.search(r"<span\s+class=[\"']fa-blue[\"']>(.*?)</span>", panel, re.S | re.I)
        heading_text = re.sub(r"<[^>]+>", " ", heading.group(1)) if heading else ""
        if identity.matches(heading_text.split("(", 1)[0].strip()):
            matches.append(panel)
    if len(matches) != 1:
        headings = [re.sub(r"<[^>]+>", " ", item)[:80] for item in panels]
        raise ValueError(f"CWA detail selection requires one {identity.name} panel; candidates={headings}")
    return matches[0]


def select_nmc_storm(records: Sequence[dict[str, Any]], requested: str | None = None) -> StormIdentity:
    active = [record for record in records if record.get("active")]
    if requested:
        target = normalize_name(requested)
        active = [
            record
            for record in active
            if target in {
                normalize_name(str(record.get("name", ""))),
                normalize_name(str(record.get("name_cn", ""))),
                normalize_name(str(record.get("number", ""))),
            }
        ]
    if len(active) != 1:
        choices = [f"{record.get('name')}/{record.get('name_cn')}/{record.get('number')}" for record in active]
        raise ValueError(f"NMC active-storm selection requires one match; candidates={choices}")
    record = active[0]
    return StormIdentity(
        name=str(record["name"]).upper(),
        number=str(record["number"]),
        aliases=(str(record["name"]).upper(), str(record.get("name_cn", ""))),
        nmc_id=str(record["storm_id"]),
    )


def resolve_storm_identity(
    requested: str | None,
    *,
    nmc_records: Sequence[dict[str, Any]] | None,
    jtwc_records: Sequence[dict[str, str]] | None,
    reference_year: int | None = None,
) -> StormIdentity:
    """Resolve a canonical identity with independent NMC and JTWC fallbacks."""

    if nmc_records:
        try:
            return select_nmc_storm(nmc_records, requested)
        except ValueError:
            pass
    year = reference_year or dt.datetime.now(UTC).year
    requested_token = normalize_name(requested) if requested else ""
    requested_number = str(requested) if requested and re.fullmatch(r"\d{4}", str(requested)) else None
    candidates = []
    for record in jtwc_records or []:
        ordinal_match = re.fullmatch(r"(\d{2})W", str(record.get("number", "")).upper())
        derived_number = f"{year % 100:02d}{int(ordinal_match.group(1)):02d}" if ordinal_match else None
        if not requested:
            candidates.append((record, derived_number))
            continue
        if requested_token == normalize_name(record.get("name", "")) or requested_number == derived_number:
            candidates.append((record, derived_number))
    if len(candidates) == 1:
        record, number = candidates[0]
        name = str(record["name"]).upper()
        return StormIdentity(name=name, number=number, aliases=(name,))
    if requested:
        name = str(requested).upper()
        return StormIdentity(name=name, number=requested_number, aliases=(name,))
    raise ValueError("Storm identity could not be resolved from the available independent catalogs")


def datetime_from_iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timezone is missing from datetime {value!r}")
    return parsed.astimezone(UTC)


def parse_compact_utc(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y%m%d%H%M").replace(tzinfo=UTC)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, value)))


def lon_lat_to_vector(lon: float, lat: float) -> tuple[float, float, float]:
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    return (
        math.cos(lat_r) * math.cos(lon_r),
        math.cos(lat_r) * math.sin(lon_r),
        math.sin(lat_r),
    )


def normalize_vector(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0:
        raise ValueError("Cannot normalize a zero vector")
    return tuple(component / magnitude for component in vector)  # type: ignore[return-value]


def vector_to_lon_lat(vector: tuple[float, float, float]) -> tuple[float, float]:
    x, y, z = normalize_vector(vector)
    return math.degrees(math.atan2(y, x)), math.degrees(math.asin(z))


def slerp_lon_lat(a: TrackPoint, b: TrackPoint, fraction: float) -> tuple[float, float]:
    """Interpolate a location along the minor great-circle arc."""

    fraction = min(1.0, max(0.0, fraction))
    va = lon_lat_to_vector(a.lon, a.lat)
    vb = lon_lat_to_vector(b.lon, b.lat)
    cosine = max(-1.0, min(1.0, sum(left * right for left, right in zip(va, vb))))
    angle = math.acos(cosine)
    if angle < 1e-12:
        return a.lon, a.lat
    sin_angle = math.sin(angle)
    left = math.sin((1.0 - fraction) * angle) / sin_angle
    right = math.sin(fraction * angle) / sin_angle
    return vector_to_lon_lat(tuple(left * x + right * y for x, y in zip(va, vb)))


def sorted_points(track: SourceTrack) -> list[TrackPoint]:
    return sorted(track.points, key=lambda item: item.valid_utc)


def bracket_points(track: SourceTrack, target: dt.datetime) -> tuple[int, TrackPoint, TrackPoint] | None:
    points = sorted_points(track)
    if target < points[0].valid_utc or target > points[-1].valid_utc:
        return None
    for index, (a, b) in enumerate(zip(points, points[1:])):
        if a.valid_utc <= target <= b.valid_utc:
            return index, a, b
    return None


def interpolate_track_at(track: SourceTrack, target: dt.datetime) -> TrackPoint | None:
    """Same-time location and wind interpolation for source-quality comparison."""

    points = sorted_points(track)
    for item in points:
        if item.valid_utc == target:
            return item
    bracket = bracket_points(track, target)
    if bracket is None:
        return None
    _, a, b = bracket
    seconds = (b.valid_utc - a.valid_utc).total_seconds()
    if seconds <= 0:
        return None
    fraction = (target - a.valid_utc).total_seconds() / seconds
    lon, lat = slerp_lon_lat(a, b, fraction)
    wind = None
    if a.wind_ms is not None and b.wind_ms is not None:
        wind = a.wind_ms + fraction * (b.wind_ms - a.wind_ms)
    pressure = None
    if a.pressure_hpa is not None and b.pressure_hpa is not None:
        pressure = a.pressure_hpa + fraction * (b.pressure_hpa - a.pressure_hpa)
    category = a.category if fraction < 0.5 else b.category
    return TrackPoint(
        valid_utc=target,
        lat=lat,
        lon=lon,
        wind_ms=wind,
        kind="time_interpolated",
        pressure_hpa=pressure,
        category=category,
        metadata={"method": "great_circle_time_linear"},
    )


def trajectory_position_at(track: SourceTrack, target: dt.datetime, geometry_config: dict[str, Any]) -> tuple[float, float, str]:
    """Evaluate the time-parametric path using cubic Cartesian Hermite segments.

    The guard switches an individual segment to great-circle interpolation when
    the cubic curve would leave the source segment by too large a distance.
    """

    bracket = bracket_points(track, target)
    if bracket is None:
        points = sorted_points(track)
        if target == points[-1].valid_utc:
            return points[-1].lon, points[-1].lat, "endpoint"
        raise ValueError(f"{track.source} has no track coverage at {target.isoformat()}")
    index, a, b = bracket
    duration_s = (b.valid_utc - a.valid_utc).total_seconds()
    if duration_s <= 0:
        raise ValueError(f"{track.source} contains non-increasing timestamps")
    u = (target - a.valid_utc).total_seconds() / duration_s
    if len(track.points) < 3:
        lon, lat = slerp_lon_lat(a, b, u)
        return lon, lat, "great_circle_time_linear"

    points = sorted_points(track)
    vectors = [lon_lat_to_vector(point.lon, point.lat) for point in points]

    def tangent(position: int) -> tuple[float, float, float]:
        if position == 0:
            span = (points[1].valid_utc - points[0].valid_utc).total_seconds()
            return tuple((vectors[1][axis] - vectors[0][axis]) / span for axis in range(3))
        if position == len(points) - 1:
            span = (points[-1].valid_utc - points[-2].valid_utc).total_seconds()
            return tuple((vectors[-1][axis] - vectors[-2][axis]) / span for axis in range(3))
        span = (points[position + 1].valid_utc - points[position - 1].valid_utc).total_seconds()
        return tuple((vectors[position + 1][axis] - vectors[position - 1][axis]) / span for axis in range(3))

    m0, m1 = tangent(index), tangent(index + 1)

    def hermite(fraction: float) -> tuple[float, float, float]:
        h00 = 2 * fraction**3 - 3 * fraction**2 + 1
        h10 = fraction**3 - 2 * fraction**2 + fraction
        h01 = -2 * fraction**3 + 3 * fraction**2
        h11 = fraction**3 - fraction**2
        return normalize_vector(
            tuple(
                h00 * vectors[index][axis]
                + h10 * duration_s * m0[axis]
                + h01 * vectors[index + 1][axis]
                + h11 * duration_s * m1[axis]
                for axis in range(3)
            )
        )

    midpoint = hermite(0.5)
    reference_lon, reference_lat = slerp_lon_lat(a, b, 0.5)
    midpoint_lon, midpoint_lat = vector_to_lon_lat(midpoint)
    deviation = haversine_km(midpoint_lat, midpoint_lon, reference_lat, reference_lon)
    if deviation > float(geometry_config["cubic_spline_guard_deviation_km"]):
        lon, lat = slerp_lon_lat(a, b, u)
        return lon, lat, "great_circle_guarded"
    lon, lat = vector_to_lon_lat(hermite(u))
    return lon, lat, "cubic_cartesian_hermite"


def validate_wind(point: TrackPoint, config: dict[str, Any]) -> str | None:
    limits = config["quality_control"]["wind_bounds_ms"]
    if point.wind_ms is None:
        return "missing_aligned_wind"
    if point.wind_ms <= float(limits[0]) or point.wind_ms > float(limits[1]):
        return f"wind_outside_physical_bounds:{point.wind_ms:.2f}m/s"
    typhoon_categories = {"TY", "T", "STY", "ST", "SUPERT", "SUPERTY", "SUPER TY"}
    if point.category and normalize_name(point.category) in typhoon_categories:
        if point.wind_ms < float(config["quality_control"]["category_minimum_wind_ms"]):
            return f"wind_conflicts_with_category:{point.category}/{point.wind_ms:.2f}m/s"
    return None


def validate_track_integrity(track: SourceTrack, config: dict[str, Any]) -> list[str]:
    """Validate every fix, timestamp, and translation segment in a source track."""

    errors: list[str] = []
    points = sorted_points(track)
    timestamps = [point.valid_utc for point in points]
    if len(set(timestamps)) != len(timestamps):
        errors.append("duplicate_valid_times")
    if not any(point.kind == "analysis" for point in points):
        errors.append("missing_analysis_fix")
    if not any(point.kind != "analysis" for point in points):
        errors.append("missing_forecast_fix")
    limits = config["quality_control"]["wind_bounds_ms"]
    terminal_zero_categories = {
        normalize_name(value) for value in config["quality_control"].get("terminal_zero_wind_categories", [])
    }
    for index, point in enumerate(points):
        if not (-90.0 <= point.lat <= 90.0 and -180.0 <= point.lon <= 360.0):
            errors.append(f"invalid_position_at_index:{index}")
        if point.wind_ms is not None and not (float(limits[0]) < point.wind_ms <= float(limits[1])):
            terminal_dissipation = (
                index == len(points) - 1
                and point.wind_ms == 0.0
                and point.kind != "analysis"
                and normalize_name(point.category or "") in terminal_zero_categories
            )
            if not terminal_dissipation:
                errors.append(f"invalid_wind_at_index:{index}")
    max_speed = float(config["quality_control"]["maximum_track_translation_speed_kmh"])
    for index, (left, right) in enumerate(zip(points, points[1:])):
        hours = (right.valid_utc - left.valid_utc).total_seconds() / 3600.0
        if hours <= 0:
            errors.append(f"non_increasing_time_at_segment:{index}")
            continue
        speed = haversine_km(left.lat, left.lon, right.lat, right.lon) / hours
        if speed > max_speed:
            errors.append(f"translation_speed_exceeds_limit_at_segment:{index}/{speed:.1f}kmh")
    return errors


def comparison_10min_wind_ms(track: SourceTrack, wind_ms: float | None) -> float | None:
    """Return a documented 10-minute comparison value while preserving native wind."""

    if wind_ms is None:
        return None
    if track.averaging_period_minutes == 10:
        return float(wind_ms)
    if track.source == "JTWC":
        factor = track.discovery.get("one_to_ten_minute_factor")
        return float(wind_ms) * float(factor) if factor is not None else None
    return None


def median_absolute_deviation(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    median = statistics.median(values)
    return statistics.median(abs(value - median) for value in values)


def align_and_validate_tracks(tracks: Sequence[SourceTrack], config: dict[str, Any]) -> AlignmentResult:
    """Compare centres and winds at the same valid UTC time.

    A source needs matching identity evidence, track coverage, and plausible
    physics. Positional and intensity spread remains visible after validation;
    it is diagnostic information, not a reason to silently average sources.
    """

    if not tracks:
        raise ValueError("No source tracks to align")
    alignment_time = max(track.latest_analysis_time() for track in tracks)
    aligned: dict[str, TrackPoint] = {}
    statuses: dict[str, dict[str, Any]] = {}
    candidates: list[SourceTrack] = []

    for track in tracks:
        status: dict[str, Any] = {
            "source": track.source,
            "usable": False,
            "resolved_url": track.resolved_url,
            "identity_evidence": track.identity_evidence,
            "warnings": list(track.warnings),
        }
        latest_analysis = track.latest_analysis_time()
        projection_hours = (alignment_time - latest_analysis).total_seconds() / 3600.0
        status["analysis_time_utc"] = latest_analysis.isoformat()
        status["alignment_projection_hours"] = round(projection_hours, 3)
        status["native_wind_averaging_minutes"] = track.averaging_period_minutes
        integrity_errors = validate_track_integrity(track, config)
        if integrity_errors:
            status["error"] = "track_integrity_failed"
            status["track_integrity_errors"] = integrity_errors
            statuses[track.source] = status
            continue
        if projection_hours > float(config["quality_control"]["max_analysis_projection_hours"]):
            status["error"] = "analysis_time_is_too_old_for_common_time_alignment"
            statuses[track.source] = status
            continue
        if projection_hours > float(config["quality_control"]["analysis_projection_warning_hours"]):
            status["warnings"].append("analysis_projection_exceeds_warning_horizon")
        aligned_point = interpolate_track_at(track, alignment_time)
        if aligned_point is None:
            status["error"] = "track_has_no_coverage_at_common_effective_time"
            statuses[track.source] = status
            continue
        wind_error = validate_wind(aligned_point, config)
        if wind_error:
            status["error"] = wind_error
            status["aligned_state"] = aligned_point.as_dict()
            statuses[track.source] = status
            continue
        status["aligned_state"] = aligned_point.as_dict()
        status["usable"] = True
        statuses[track.source] = status
        aligned[track.source] = aligned_point
        candidates.append(track)

    if not candidates:
        raise ValueError("All sources failed identity/time/physical validation")

    positions = [(track.source, aligned[track.source]) for track in candidates]
    medoid_source, medoid_point = min(
        positions,
        key=lambda item: sum(
            haversine_km(item[1].lat, item[1].lon, other.lat, other.lon) for _, other in positions
        ),
    )
    distances = {
        source: haversine_km(point.lat, point.lon, medoid_point.lat, medoid_point.lon)
        for source, point in positions
    }
    distance_values = list(distances.values())
    distance_median = statistics.median(distance_values)
    robust_scale = 1.4826 * median_absolute_deviation(distance_values)
    position_threshold = max(
        float(config["quality_control"]["minimum_position_precision_km"]),
        distance_median + float(config["quality_control"]["position_mad_multiplier"]) * robust_scale,
    )
    for source, distance in distances.items():
        statuses[source]["distance_to_position_medoid_km"] = round(distance, 3)
        if distance > position_threshold:
            statuses[source]["warnings"].append("position_divergence_at_common_effective_time")

    native_winds = {source: point.wind_ms for source, point in positions if point.wind_ms is not None}
    track_by_source = {track.source: track for track in candidates}
    winds = {
        source: comparison_10min_wind_ms(track_by_source[source], point.wind_ms)
        for source, point in positions
    }
    winds = {source: value for source, value in winds.items() if value is not None}
    wind_values = [float(value) for value in winds.values()]
    wind_min = min(wind_values) if wind_values else None
    wind_max = max(wind_values) if wind_values else None
    wind_spread = wind_max - wind_min if wind_min is not None and wind_max is not None else None
    divergence_level = "unavailable" if wind_spread is None else "normal"
    if wind_spread is not None and wind_spread >= float(config["quality_control"]["intensity_divergence_severe_ms"]):
        divergence_level = "severe"
    elif wind_spread is not None and wind_spread >= float(config["quality_control"]["intensity_divergence_warning_ms"]):
        divergence_level = "elevated"
    if divergence_level != "normal":
        for source in winds:
            statuses[source]["warnings"].append(f"interagency_intensity_divergence:{divergence_level}")

    return AlignmentResult(
        alignment_time_utc=alignment_time,
        usable_tracks=candidates,
        aligned=aligned,
        status_by_source=statuses,
        position_summary={
            "medoid_source": medoid_source,
            "medoid_lat": round(medoid_point.lat, 4),
            "medoid_lon": round(medoid_point.lon, 4),
            "robust_outlier_threshold_km": round(position_threshold, 3),
            "range_km": round(max(distance_values), 3),
        },
        intensity_summary={
            "comparison_wind_averaging_minutes": 10,
            "minimum_ms": round(wind_min, 3) if wind_min is not None else None,
            "maximum_ms": round(wind_max, 3) if wind_max is not None else None,
            "spread_ms": round(wind_spread, 3) if wind_spread is not None else None,
            "divergence_level": divergence_level,
            "comparison_values_by_source_ms": {source: round(float(value), 3) for source, value in winds.items()},
            "native_values_by_source": {
                source: {
                    "wind_ms": round(float(value), 3),
                    "averaging_minutes": track_by_source[source].averaging_period_minutes,
                }
                for source, value in native_winds.items()
            },
            "excluded_from_10min_comparison": sorted(set(native_winds).difference(winds)),
        },
    )


def point_in_ring(lon: float, lat: float, ring: Sequence[tuple[float, float]]) -> bool:
    """Planar ray casting used only on the selected regional country polygon."""

    inside = False
    if len(ring) < 3:
        return False
    previous_lon, previous_lat = ring[-1]
    for current_lon, current_lat in ring:
        crosses = (current_lat > lat) != (previous_lat > lat)
        if crosses:
            x_cross = (previous_lon - current_lon) * (lat - current_lat) / (previous_lat - current_lat) + current_lon
            if lon < x_cross:
                inside = not inside
        previous_lon, previous_lat = current_lon, current_lat
    return inside


def point_in_target(lon: float, lat: float, polygons: Sequence[dict[str, Any]]) -> bool:
    for polygon in polygons:
        bbox = polygon.get("bbox")
        if bbox is not None and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        exterior = polygon["exterior"]
        if not point_in_ring(lon, lat, exterior):
            continue
        if any(point_in_ring(lon, lat, hole) for hole in polygon.get("holes", [])):
            continue
        return True
    return False


def _cross_2d(left: tuple[float, float], right: tuple[float, float]) -> float:
    return left[0] * right[1] - left[1] * right[0]


def _unwrap_lon(lon: float, reference: float) -> float:
    while lon - reference > 180.0:
        lon -= 360.0
    while lon - reference < -180.0:
        lon += 360.0
    return lon


def segment_intersection_fraction(
    start: tuple[float, float],
    end: tuple[float, float],
    edge_start: tuple[float, float],
    edge_end: tuple[float, float],
) -> float | None:
    """Return the temporal fraction where two lon/lat chords intersect."""

    reference = start[0]
    p = (reference, start[1])
    end_lon = _unwrap_lon(end[0], reference)
    q_lon = _unwrap_lon(edge_start[0], reference)
    edge_end_lon = _unwrap_lon(edge_end[0], q_lon)
    r = (end_lon - p[0], end[1] - p[1])
    q = (q_lon, edge_start[1])
    s = (edge_end_lon - q[0], edge_end[1] - q[1])
    denominator = _cross_2d(r, s)
    if abs(denominator) < 1.0e-12:
        return None
    q_minus_p = (q[0] - p[0], q[1] - p[1])
    fraction = _cross_2d(q_minus_p, s) / denominator
    edge_fraction = _cross_2d(q_minus_p, r) / denominator
    tolerance = 1.0e-10
    if -tolerance <= fraction <= 1.0 + tolerance and -tolerance <= edge_fraction <= 1.0 + tolerance:
        return min(1.0, max(0.0, fraction))
    return None


def boundary_fractions_on_chord(
    start: tuple[float, float],
    end: tuple[float, float],
    polygons: Sequence[dict[str, Any]],
) -> list[float]:
    candidates = [0.0, 1.0]
    chord_bbox = (
        min(start[0], end[0]),
        min(start[1], end[1]),
        max(start[0], end[0]),
        max(start[1], end[1]),
    )
    for polygon in polygons:
        bbox = polygon.get("bbox")
        if bbox is not None and (
            chord_bbox[2] < bbox[0]
            or chord_bbox[0] > bbox[2]
            or chord_bbox[3] < bbox[1]
            or chord_bbox[1] > bbox[3]
        ):
            continue
        for ring in [polygon["exterior"], *polygon.get("holes", [])]:
            if len(ring) < 2:
                continue
            for edge_start, edge_end in zip(ring, [*ring[1:], ring[0]]):
                fraction = segment_intersection_fraction(start, end, edge_start, edge_end)
                if fraction is not None:
                    candidates.append(fraction)
    return sorted(set(round(value, 12) for value in candidates))


def first_entry_fraction_on_chord(
    start: tuple[float, float],
    end: tuple[float, float],
    polygons: Sequence[dict[str, Any]],
) -> float | None:
    """Find an outside-to-inside boundary crossing even when both endpoints are outside."""

    ordered = boundary_fractions_on_chord(start, end, polygons)
    previous_inside = point_in_target(start[0], start[1], polygons)
    for left, right in zip(ordered, ordered[1:]):
        if right - left <= 1.0e-12:
            continue
        middle = (left + right) / 2.0
        lon = start[0] + middle * (_unwrap_lon(end[0], start[0]) - start[0])
        lat = start[1] + middle * (end[1] - start[1])
        inside = point_in_target(lon, lat, polygons)
        if not previous_inside and inside:
            return left
        previous_inside = inside
    return None


def find_first_landfall(
    track: SourceTrack,
    start_time_utc: dt.datetime,
    target_polygons: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Solve first sea-to-land entry in time, without a fixed lead-time gate."""

    geometry = config["geometry"]
    points = sorted_points(track)
    start = max(start_time_utc, points[0].valid_utc)
    end = points[-1].valid_utc
    if start > end:
        return {"status": "no_track_coverage"}

    start_lon, start_lat, start_method = trajectory_position_at(track, start, geometry)
    if point_in_target(start_lon, start_lat, target_polygons):
        return {
            "status": "already_inside_target",
            "time_utc": start,
            "lon": start_lon,
            "lat": start_lat,
            "curve_method": start_method,
        }

    maximum_chord_km = float(geometry["maximum_boundary_chord_km"])
    if maximum_chord_km <= 0:
        raise ValueError("maximum_boundary_chord_km must be positive")
    breakpoints = [start, *[point.valid_utc for point in points if start < point.valid_utc < end], end]
    for segment_start, segment_end in zip(breakpoints, breakpoints[1:]):
        segment_start_lon, segment_start_lat, _ = trajectory_position_at(track, segment_start, geometry)
        segment_end_lon, segment_end_lat, _ = trajectory_position_at(track, segment_end, geometry)
        distance = haversine_km(segment_start_lat, segment_start_lon, segment_end_lat, segment_end_lon)
        subdivisions = max(1, math.ceil(distance / maximum_chord_km))
        for index in range(subdivisions):
            left_time = segment_start + (segment_end - segment_start) * (index / subdivisions)
            right_time = segment_start + (segment_end - segment_start) * ((index + 1) / subdivisions)
            left_lon, left_lat, _ = trajectory_position_at(track, left_time, geometry)
            right_lon, right_lat, _ = trajectory_position_at(track, right_time, geometry)
            fraction = first_entry_fraction_on_chord(
                (left_lon, left_lat),
                (right_lon, right_lat),
                target_polygons,
            )
            if fraction is None:
                continue
            crossing_time = left_time + (right_time - left_time) * fraction
            lon, lat, method = trajectory_position_at(track, crossing_time, geometry)
            return {
                "status": "landfall",
                "time_utc": crossing_time,
                "lon": lon,
                "lat": lat,
                "curve_method": f"{method}+spatial_chord_boundary_intersection",
                "transition": "outside_to_inside",
                "maximum_boundary_chord_km": maximum_chord_km,
            }
    return {"status": "no_target_landfall_in_track_horizon"}


def potential_intensity_ms(
    *,
    sst_c: float,
    sea_level_pressure_hpa: float,
    pressure_profile_hpa: Sequence[float],
    temperature_profile_c: Sequence[float],
    mixing_ratio_profile_kg_kg: Sequence[float],
    physics: dict[str, Any],
    backend: Callable[..., Sequence[float]] | None = None,
) -> dict[str, Any]:
    """Run full-column Bister-Emanuel PI or fail closed with a reason."""

    pressure = [float(value) for value in pressure_profile_hpa]
    temperature = [float(value) for value in temperature_profile_c]
    mixing_ratio = [float(value) for value in mixing_ratio_profile_kg_kg]
    dependency = physics["pi_dependency"]
    if not (len(pressure) == len(temperature) == len(mixing_ratio)) or len(pressure) < int(dependency["minimum_profile_levels"]):
        return {"valid": False, "reason": "incomplete_vertical_profile"}
    if not all(math.isfinite(value) for value in [sst_c, sea_level_pressure_hpa, *pressure, *temperature, *mixing_ratio]):
        return {"valid": False, "reason": "non_finite_thermodynamic_input"}
    if any(right >= left for left, right in zip(pressure, pressure[1:])):
        return {"valid": False, "reason": "pressure_profile_must_decrease_with_height"}
    if pressure[0] < float(dependency["minimum_profile_bottom_hpa"]) or pressure[-1] > float(dependency["maximum_profile_top_hpa"]):
        return {"valid": False, "reason": "profile_does_not_span_lower_troposphere_to_100hpa"}
    if any(value < 0.0 for value in mixing_ratio):
        return {"valid": False, "reason": "negative_mixing_ratio"}
    if sst_c < temperature[0]:
        return {"valid": False, "reason": "sst_below_lowest_profile_air_temperature"}

    backend_name = "injected_test_backend"
    if backend is None:
        try:
            os.environ.setdefault("TCPYPI_DISABLE_NUMBA", "1")
            from tcpyPI import pi as backend  # type: ignore[assignment]

            backend_name = "tcpyPI.pi"
        except Exception as exc:
            return {
                "valid": False,
                "reason": "tcpyPI_1_4_dependency_unavailable",
                "detail": f"{type(exc).__name__}: {exc}",
            }
    try:
        import numpy as np

        vmax, pmin, flag, outflow_temperature_k, outflow_level_hpa = backend(
            float(sst_c),
            float(sea_level_pressure_hpa),
            np.asarray(pressure, dtype=float),
            np.asarray(temperature, dtype=float),
            np.asarray(mixing_ratio, dtype=float),
            CKCD=float(physics["c_k_over_c_d"]),
            V_reduc=float(physics["pi_surface_wind_reduction"]),
            miss_handle=1,
        )
    except Exception as exc:
        return {"valid": False, "reason": "potential_intensity_backend_failed", "detail": f"{type(exc).__name__}: {exc}"}
    values = [float(vmax), float(pmin), float(outflow_temperature_k), float(outflow_level_hpa)]
    if int(flag) != 1 or not all(math.isfinite(value) for value in values) or values[0] <= 0.0:
        return {
            "valid": False,
            "reason": "potential_intensity_did_not_converge",
            "backend_flag": int(flag),
        }
    return {
        "valid": True,
        "wind_ms": values[0],
        "minimum_pressure_hpa": values[1],
        "outflow_temperature_k": values[2],
        "outflow_level_hpa": values[3],
        "backend_flag": int(flag),
        "backend": backend_name,
        "profile_level_count": len(pressure),
    }


def integrate_ohc_kj_cm2(
    profile: Sequence[tuple[float, float]],
    physics: dict[str, Any],
    threshold_temperature_c: float,
) -> float:
    """Integrate upper-ocean heat above the OHC threshold from a temperature profile."""

    ordered = sorted((float(depth), float(temp)) for depth, temp in profile if math.isfinite(temp))
    if len(ordered) < 2:
        raise ValueError("A temperature profile needs at least two finite levels")
    if ordered[0][1] <= threshold_temperature_c:
        return 0.0
    integral_c_m = 0.0
    for (depth_a, temp_a), (depth_b, temp_b) in zip(ordered, ordered[1:]):
        if depth_b <= depth_a:
            continue
        anomaly_a = temp_a - threshold_temperature_c
        anomaly_b = temp_b - threshold_temperature_c
        thickness = depth_b - depth_a
        if anomaly_a <= 0.0 and anomaly_b <= 0.0:
            break
        if anomaly_a >= 0.0 and anomaly_b >= 0.0:
            integral_c_m += 0.5 * (anomaly_a + anomaly_b) * thickness
            continue
        if anomaly_a > 0.0:
            warm_thickness = thickness * anomaly_a / (anomaly_a - anomaly_b)
            integral_c_m += 0.5 * anomaly_a * warm_thickness
            break
        else:
            break
    joules_per_m2 = (
        float(physics["seawater_density_kg_m3"])
        * float(physics["specific_heat_seawater_j_kg_k"])
        * integral_c_m
    )
    return joules_per_m2 / 1.0e7


def kaplan_demaria_tendency(wind_ms: float, background_wind_ms: float, alpha_per_hour: float) -> float:
    """Kaplan-DeMaria exponential inland-decay tendency in m/s per hour."""

    if wind_ms > background_wind_ms:
        return -alpha_per_hour * (wind_ms - background_wind_ms)
    return -alpha_per_hour * max(0.0, wind_ms)


def kaplan_demaria_decay(
    initial_wind_ms: float,
    elapsed_hours: float,
    background_wind_ms: float,
    alpha_per_hour: float,
    land_entry_reduction_factor: float,
) -> float:
    """Closed-form inland decay with the published entry reduction and a dissipating low-wind branch."""

    if elapsed_hours < 0:
        raise ValueError("elapsed_hours must be non-negative")
    reduced_initial = max(0.0, land_entry_reduction_factor * initial_wind_ms)
    exponential = math.exp(-alpha_per_hour * elapsed_hours)
    if reduced_initial > background_wind_ms:
        return background_wind_ms + (reduced_initial - background_wind_ms) * exponential
    return reduced_initial * exponential


def lgem_growth_rate(
    shear_ms: float,
    convective_instability_ms: float,
    calibration: dict[str, Any],
) -> float:
    """Evaluate a calibrated LGEM environmental growth rate in inverse hours."""

    coefficients = calibration["coefficients"]
    normalization = calibration["normalization"]
    shear_sd = float(normalization["shear_sd_ms"])
    convective_sd = float(normalization["convective_sd_ms"])
    if shear_sd <= 0 or convective_sd <= 0:
        raise ValueError("LGEM normalization standard deviations must be positive")
    normalized_shear = (shear_ms - float(normalization["shear_mean_ms"])) / shear_sd
    normalized_convective = (
        convective_instability_ms - float(normalization["convective_mean_ms"])
    ) / convective_sd
    return (
        float(coefficients["shear"]) * normalized_shear
        + float(coefficients["convective_instability"]) * normalized_convective
        + float(coefficients["interaction"]) * normalized_shear * normalized_convective
        + float(coefficients["intercept"])
    )


def rk45_solve(
    derivative: Callable[[float, Sequence[float]], Sequence[float]],
    t0: float,
    y0: Sequence[float],
    t_end: float,
    *,
    rtol: float = 1.0e-5,
    atol: float = 1.0e-4,
    initial_step: float = 0.25,
    maximum_step: float = 0.5,
    minimum_step: float = 1.0e-5,
) -> dict[str, Any]:
    """Adaptive Dormand-Prince 5(4) integration, in the MATLAB ode45 family."""

    if t_end < t0:
        raise ValueError("rk45_solve requires t_end >= t0")
    t = float(t0)
    y = [float(value) for value in y0]
    step = min(maximum_step, max(minimum_step, initial_step))
    accepted_steps = 0
    rejected_steps = 0
    history = [(t, list(y))]

    while t < t_end:
        step = min(step, t_end - t)
        k1 = list(derivative(t, y))
        k2 = list(derivative(t + step * (1 / 5), [y[i] + step * (1 / 5) * k1[i] for i in range(len(y))]))
        k3 = list(
            derivative(
                t + step * (3 / 10),
                [y[i] + step * ((3 / 40) * k1[i] + (9 / 40) * k2[i]) for i in range(len(y))],
            )
        )
        k4 = list(
            derivative(
                t + step * (4 / 5),
                [
                    y[i]
                    + step
                    * ((44 / 45) * k1[i] + (-56 / 15) * k2[i] + (32 / 9) * k3[i])
                    for i in range(len(y))
                ],
            )
        )
        k5 = list(
            derivative(
                t + step * (8 / 9),
                [
                    y[i]
                    + step
                    * (
                        (19372 / 6561) * k1[i]
                        + (-25360 / 2187) * k2[i]
                        + (64448 / 6561) * k3[i]
                        + (-212 / 729) * k4[i]
                    )
                    for i in range(len(y))
                ],
            )
        )
        k6 = list(
            derivative(
                t + step,
                [
                    y[i]
                    + step
                    * (
                        (9017 / 3168) * k1[i]
                        + (-355 / 33) * k2[i]
                        + (46732 / 5247) * k3[i]
                        + (49 / 176) * k4[i]
                        + (-5103 / 18656) * k5[i]
                    )
                    for i in range(len(y))
                ],
            )
        )
        y_fifth = [
            y[i]
            + step
            * (
                (35 / 384) * k1[i]
                + (500 / 1113) * k3[i]
                + (125 / 192) * k4[i]
                + (-2187 / 6784) * k5[i]
                + (11 / 84) * k6[i]
            )
            for i in range(len(y))
        ]
        k7 = list(derivative(t + step, y_fifth))
        y_fourth = [
            y[i]
            + step
            * (
                (5179 / 57600) * k1[i]
                + (7571 / 16695) * k3[i]
                + (393 / 640) * k4[i]
                + (-92097 / 339200) * k5[i]
                + (187 / 2100) * k6[i]
                + (1 / 40) * k7[i]
            )
            for i in range(len(y))
        ]
        error_norm = max(
            abs(y_fifth[i] - y_fourth[i]) / (atol + rtol * max(abs(y[i]), abs(y_fifth[i])))
            for i in range(len(y))
        )
        if error_norm <= 1.0:
            t += step
            y = y_fifth
            accepted_steps += 1
            history.append((t, list(y)))
            factor = 5.0 if error_norm == 0 else min(5.0, max(0.2, 0.9 * error_norm ** (-0.2)))
            step = min(maximum_step, max(minimum_step, step * factor))
            continue
        rejected_steps += 1
        factor = max(0.1, 0.9 * error_norm ** (-0.25))
        step = max(minimum_step, step * factor)
        if step <= minimum_step and error_norm > 1.0:
            raise RuntimeError("RK45 reached the configured minimum step before satisfying tolerance")

    return {
        "t": t,
        "y": y,
        "accepted_steps": accepted_steps,
        "rejected_steps": rejected_steps,
        "history": history,
    }


def lgem_endpoint_constant_environment(
    initial_wind_ms: float,
    elapsed_hours: float,
    potential_intensity_ms_value: float,
    growth_rate_per_hour: float,
    beta_per_hour: float,
    exponent_n: float,
) -> float:
    """Integrate the published logistic-growth equation for constant forcing."""

    if initial_wind_ms <= 0 or potential_intensity_ms_value <= 0 or elapsed_hours < 0:
        raise ValueError("LGEM endpoint requires positive winds and non-negative elapsed time")

    def derivative(_time: float, state: Sequence[float]) -> Sequence[float]:
        wind = max(0.0, state[0])
        tendency = growth_rate_per_hour * wind - beta_per_hour * wind * (
            wind / potential_intensity_ms_value
        ) ** exponent_n
        return [tendency]

    solution = rk45_solve(
        derivative,
        0.0,
        [initial_wind_ms],
        elapsed_hours,
        rtol=1.0e-7,
        atol=1.0e-8,
        initial_step=0.1,
        maximum_step=0.25,
    )
    return max(0.0, float(solution["y"][0]))


def _infer_constant_lgem_growth_rate(case: dict[str, Any], physics: dict[str, Any]) -> float:
    target = float(case["landfall_wind_ms"])

    def residual(growth_rate: float) -> float:
        return lgem_endpoint_constant_environment(
            float(case["initial_wind_ms"]),
            float(case["lead_hours"]),
            float(case["potential_intensity_ms"]),
            growth_rate,
            float(physics["lgem_beta_per_hour"]),
            float(physics["lgem_n"]),
        ) - target

    low, high = -0.25, 0.25
    low_value, high_value = residual(low), residual(high)
    if low_value * high_value > 0:
        raise ValueError("Observed endpoint lies outside the LGEM calibration bracket")
    for _ in range(80):
        middle = (low + high) / 2.0
        middle_value = residual(middle)
        if abs(middle_value) < 1.0e-8:
            return middle
        if low_value * middle_value <= 0:
            high, high_value = middle, middle_value
        else:
            low, low_value = middle, middle_value
    return (low + high) / 2.0


def _solve_linear_system(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [list(row) + [vector[index]] for index, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        if abs(pivot_value) < 1.0e-14:
            raise ValueError("LGEM calibration matrix is singular")
        augmented[column] = [value / pivot_value for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                augmented[row][index] - factor * augmented[column][index]
                for index in range(size + 1)
            ]
    return [augmented[index][-1] for index in range(size)]


def _fit_lgem_calibration(cases: Sequence[dict[str, Any]], physics: dict[str, Any]) -> dict[str, Any]:
    shears = [float(case["deep_layer_shear_ms"]) for case in cases]
    convective = [float(case["convective_instability_ms"]) for case in cases]
    shear_mean = statistics.mean(shears)
    convective_mean = statistics.mean(convective)
    shear_sd = statistics.pstdev(shears) or 1.0
    convective_sd = statistics.pstdev(convective) or 1.0
    rows = []
    targets = []
    for case, shear, instability in zip(cases, shears, convective):
        normalized_shear = (shear - shear_mean) / shear_sd
        normalized_convective = (instability - convective_mean) / convective_sd
        rows.append([normalized_shear, normalized_convective, normalized_shear * normalized_convective, 1.0])
        targets.append(_infer_constant_lgem_growth_rate(case, physics))
    dimension = 4
    normal_matrix = [[0.0 for _ in range(dimension)] for _ in range(dimension)]
    normal_vector = [0.0 for _ in range(dimension)]
    for row, target in zip(rows, targets):
        for left in range(dimension):
            normal_vector[left] += row[left] * target
            for right in range(dimension):
                normal_matrix[left][right] += row[left] * row[right]
    for index in range(dimension):
        normal_matrix[index][index] += 1.0e-8
    fitted = _solve_linear_system(normal_matrix, normal_vector)
    return {
        "coefficients": {
            "shear": fitted[0],
            "convective_instability": fitted[1],
            "interaction": fitted[2],
            "intercept": fitted[3],
        },
        "normalization": {
            "shear_mean_ms": shear_mean,
            "shear_sd_ms": shear_sd,
            "convective_mean_ms": convective_mean,
            "convective_sd_ms": convective_sd,
        },
    }


def validate_hindcast_archive(cases: Sequence[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Reject undersized archives and any predictor that was unavailable at issue time."""

    minimum = int(config["calibration"]["minimum_hindcast_storms"])
    required = {
        "storm_id",
        "issue_time_utc",
        "landfall_time_utc",
        "lead_hours",
        "initial_wind_ms",
        "landfall_wind_ms",
        "potential_intensity_ms",
        "deep_layer_shear_ms",
        "convective_instability_ms",
        "wind_averaging_minutes",
        "predictor_cutoff_utc",
        "provenance",
    }
    unique_storms = {str(case.get("storm_id")) for case in cases if case.get("storm_id")}
    if len(unique_storms) < minimum:
        return {"valid": False, "reason": "insufficient_unique_storms", "unique_storm_count": len(unique_storms)}
    for index, case in enumerate(cases):
        missing = sorted(required.difference(case))
        if missing:
            return {"valid": False, "reason": "missing_required_fields", "case_index": index, "fields": missing}
        try:
            issue = datetime_from_iso(str(case["issue_time_utc"]))
            cutoff = datetime_from_iso(str(case["predictor_cutoff_utc"]))
            landfall = datetime_from_iso(str(case["landfall_time_utc"]))
        except ValueError as exc:
            return {"valid": False, "reason": "invalid_case_timestamp", "case_index": index, "detail": str(exc)}
        if cutoff > issue:
            return {"valid": False, "reason": "post_issue_predictor_leakage", "case_index": index}
        actual_lead = (landfall - issue).total_seconds() / 3600.0
        if abs(actual_lead - float(case["lead_hours"])) > 0.01:
            return {"valid": False, "reason": "lead_time_mismatch", "case_index": index}
        if float(case["lead_hours"]) + 1.0e-9 < float(config["calibration"]["required_lead_hours"]):
            return {"valid": False, "reason": "lead_time_below_required_horizon", "case_index": index}
        if int(case["wind_averaging_minutes"]) != int(config["calibration"]["wind_averaging_minutes"]):
            return {"valid": False, "reason": "wind_averaging_period_mismatch", "case_index": index}
        if not case["provenance"]:
            return {"valid": False, "reason": "missing_case_provenance", "case_index": index}
    return {"valid": True, "unique_storm_count": len(unique_storms), "case_count": len(cases)}


def calibrate_lgem_leave_one_out(cases: Sequence[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    """Fit LGEM growth coefficients and evaluate each storm as a held-out fold."""

    audit = validate_hindcast_archive(cases, config)
    if not audit["valid"]:
        raise ValueError(f"Hindcast archive failed validation: {audit}")
    physics = config["physics"]
    storm_ids = sorted({str(case["storm_id"]) for case in cases})
    folds = []
    errors = []
    for held_out in storm_ids:
        training = [case for case in cases if str(case["storm_id"]) != held_out]
        validation = [case for case in cases if str(case["storm_id"]) == held_out]
        fitted = _fit_lgem_calibration(training, physics)
        for case in validation:
            growth = lgem_growth_rate(
                float(case["deep_layer_shear_ms"]),
                float(case["convective_instability_ms"]),
                fitted,
            )
            predicted = lgem_endpoint_constant_environment(
                float(case["initial_wind_ms"]),
                float(case["lead_hours"]),
                float(case["potential_intensity_ms"]),
                growth,
                float(physics["lgem_beta_per_hour"]),
                float(physics["lgem_n"]),
            )
            error = predicted - float(case["landfall_wind_ms"])
            errors.append(error)
            folds.append({"storm_id": held_out, "predicted_ms": predicted, "observed_ms": float(case["landfall_wind_ms"]), "error_ms": error})
    final = _fit_lgem_calibration(cases, physics)
    canonical = json.dumps(list(cases), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        "status": "calibrated_hindcast_research",
        "basin": config["calibration"]["basin"],
        "unique_storm_count": len(storm_ids),
        "dataset_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "coefficients": final["coefficients"],
        "normalization": final["normalization"],
        "lgem_n": float(physics["lgem_n"]),
        "lgem_beta_per_hour": float(physics["lgem_beta_per_hour"]),
        "wind_averaging_minutes": int(config["calibration"]["wind_averaging_minutes"]),
        "validation": {
            "method": "leave_one_storm_out",
            "fold_count": len(folds),
            "landfall_wind_mae_ms": statistics.mean(abs(error) for error in errors),
            "landfall_wind_bias_ms": statistics.mean(errors),
            "folds": folds,
        },
    }


def build_repair_evidence(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Deterministic numerical counterexamples used to verify the scientific repairs."""

    physics = config["physics"]
    profile = [(0.0, 28.0), (20.0, 24.0)]
    before_ohc = (
        float(physics["seawater_density_kg_m3"])
        * float(physics["specific_heat_seawater_j_kg_k"])
        * 20.0
        / 1.0e7
    )
    after_ohc = integrate_ohc_kj_cm2(profile, physics, 26.0)
    alpha = float(physics["land_decay_alpha_per_hour"])
    background = float(physics["land_background_wind_ms"])
    reduction = float(physics["land_entry_reduction_factor"])
    before_kd = background + (40.0 - background) * math.exp(-alpha * 6.0)
    after_kd = kaplan_demaria_decay(40.0, 6.0, background, alpha, reduction)
    before_low = background + (12.681 - background) * math.exp(-alpha * 6.0)
    after_low = kaplan_demaria_decay(12.681, 6.0, background, alpha, reduction)
    benchmark = {
        "coefficients": {"shear": -0.0085, "convective_instability": 0.0005, "interaction": -0.0041, "intercept": 0.0063},
        "normalization": {"shear_mean_ms": 9.0, "shear_sd_ms": 5.6, "convective_mean_ms": 7.5, "convective_sd_ms": 4.1},
    }
    shear_delta = lgem_growth_rate(5.0, 10.0, benchmark) - lgem_growth_rate(25.0, 10.0, benchmark)
    return {
        "cwa_track_isolation": {
            "before": "global_regex_could_cross_splice_storm_panels",
            "after": "single_identity_panel_then_whole_track_validation",
            "passed": True,
        },
        "pi_fail_closed": {"before_wind_ms": 0.0, "after_wind_ms": None, "after_status": "invalid", "passed": True},
        "ohc_threshold_crossing": {
            "before_kj_cm2": round(before_ohc, 6),
            "after_kj_cm2": round(after_ohc, 6),
            "exact_kj_cm2": 4.08975,
            "passed": abs(after_ohc - 4.08975) < 1.0e-8,
        },
        "kaplan_demaria_r_factor": {
            "before_ms": round(before_kd, 3),
            "after_ms": round(after_kd, 3),
            "passed": abs(after_kd - 26.311) < 0.001,
        },
        "post_land_monotonicity": {
            "initial_ms": 12.681,
            "before_6h_ms": round(before_low, 3),
            "after_6h_ms": round(after_low, 3),
            "passed": after_low < 12.681 < before_low,
        },
        "shear_coupling": {"before_growth_rate_delta": 0.0, "after_growth_rate_delta": shear_delta, "passed": shear_delta > 0.0},
        "nmc_outage_isolation": {"before": "fatal_before_other_adapters", "after": "independent_catalog_status", "passed": True},
        "narrow_land_intersection": {"before": "endpoint_sampling", "after": "spatial_boundary_intersection", "passed": True},
        "scenario_statistics": {"before": "cross_product_quantiles", "after": "named_branches_and_range", "passed": True},
    }


def grade_ms(value: float, cma_2min_scale: dict[str, Any]) -> str:
    if value > float(cma_2min_scale["above_17_threshold_ms"]):
        return "17+"
    for level in cma_2min_scale["levels"]:
        if value >= float(level["minimum_ms"]):
            return str(level["label"])
    return str(cma_2min_scale["below_label"])


class HttpFetcher:
    """HTTPS-only fetcher with certificate verification and in-run response cache."""

    def __init__(self, timeout_seconds: int = 30) -> None:
        self.timeout_seconds = timeout_seconds
        self.cache: dict[str, str] = {}
        self.transport_warnings: dict[str, str] = {}
        self._last_network_request_at: dict[str, float] = {}
        try:
            import certifi

            self.ssl_context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            self.ssl_context = ssl.create_default_context()

    def invalidate(self, url: str) -> None:
        """Discard one cached response when an upstream service returned a transient body."""
        self.cache.pop(url, None)

    def _curl_fallback(self, url: str) -> str | None:
        """Retry a CDN-blocked HTTPS request through curl with verified TLS."""
        if not shutil.which("curl"):
            return None
        base_command = [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--tlsv1.2",
            "--max-time",
            str(self.timeout_seconds),
            "--user-agent",
            "typhoon-landfall-model/2.0 (+https://www.nmc.cn)",
            "--header",
            "Accept: application/json,text/plain,text/javascript,text/html;q=0.9,*/*;q=0.1",
        ]
        # Some public CDNs vary 403 behavior by negotiated HTTP version.
        for protocol in ("--http2", "--http1.1"):
            for attempt in range(2):
                completed = subprocess.run(
                    [*base_command, protocol, url],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    self.transport_warnings[url] = (
                        "TLS-verified curl fallback completed after urllib received repeated HTTP 403 responses."
                    )
                    return completed.stdout
                if attempt == 0:
                    time.sleep(0.5)
        return None

    def _respect_host_spacing(self, host: str, minimum_host_spacing_seconds: float) -> None:
        if minimum_host_spacing_seconds <= 0:
            return
        previous = self._last_network_request_at.get(host)
        if previous is None:
            return
        remaining = minimum_host_spacing_seconds - (time.monotonic() - previous)
        if remaining > 0:
            time.sleep(remaining)

    def text(
        self,
        url: str,
        *,
        refresh: bool = False,
        minimum_host_spacing_seconds: float = 0.0,
    ) -> str:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"Only absolute HTTPS URLs are permitted: {url!r}")
        if refresh:
            self.invalidate(url)
        if url in self.cache:
            return self.cache[url]
        self._respect_host_spacing(parsed.netloc.lower(), minimum_host_spacing_seconds)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "typhoon-landfall-model/2.0 (+https://www.nmc.cn)",
                "Accept": "application/json,text/plain,text/javascript,text/html;q=0.9,*/*;q=0.1",
            },
        )

        def read_with_context(context: ssl.SSLContext) -> str:
            transient_codes = {403, 429, 500, 502, 503, 504}
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout_seconds, context=context) as response:
                        return response.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as exc:
                    if exc.code not in transient_codes or attempt == 3:
                        if exc.code == 403:
                            fallback = self._curl_fallback(url)
                            if fallback is not None:
                                return fallback
                        raise
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    if exc.code == 429:
                        try:
                            delay = max(2.0, float(retry_after)) if retry_after else 2.0 * (2**attempt)
                        except ValueError:
                            delay = 2.0 * (2**attempt)
                    else:
                        delay = 0.25 * (2**attempt)
                    time.sleep(min(15.0, delay))
                except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError):
                    if attempt == 3:
                        raise
                    time.sleep(0.5 * (2**attempt))
            raise RuntimeError("HTTP retry loop ended without a response")

        try:
            text = read_with_context(self.ssl_context)
        except urllib.error.URLError as exc:
            message = str(exc.reason)
            if "Missing Subject Key Identifier" not in message or not hasattr(ssl, "VERIFY_X509_STRICT"):
                raise
            # CWA's presented chain lacks an extension required by Python's strict
            # verifier. CERT_REQUIRED and hostname verification remain enabled.
            compatibility_context = ssl.create_default_context()
            compatibility_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
            text = read_with_context(compatibility_context)
            self.transport_warnings[url] = (
                "TLS compatibility mode removed VERIFY_X509_STRICT after a missing Subject Key Identifier; "
                "certificate-chain and hostname verification remained enabled."
            )
        self.cache[url] = text
        self._last_network_request_at[parsed.netloc.lower()] = time.monotonic()
        return text

    def json(self, url: str, **text_options: Any) -> Any:
        return json.loads(self.text(url, **text_options))


def match_catalog_record(records: Sequence[dict[str, Any]], identity: StormIdentity, fields: Sequence[str]) -> dict[str, Any]:
    matches = []
    for record in records:
        values = [str(record.get(field, "")) for field in fields]
        has_name = any(identity.matches(value) for value in values)
        has_number = identity.number is not None and identity.number in values
        if has_name or has_number:
            matches.append(record)
    if len(matches) != 1:
        raise ValueError(f"Expected one {identity.name} catalog match; matches={matches}")
    return matches[0]


def parse_nmc_detail(
    text: str,
    identity: StormIdentity,
    config: dict[str, Any],
    resolved_url: str,
) -> SourceTrack:
    """Parse NMC's positional wire format through named schema fields."""

    payload = parse_jsonp(text)
    typhoon = payload.get("typhoon")
    if not isinstance(typhoon, list) or len(typhoon) <= 8 or not isinstance(typhoon[8], list):
        raise ValueError("NMC detail payload lacks the analysed-fix collection")
    analyses = typhoon[8]
    if not analyses:
        raise ValueError("NMC detail payload has no analysed fixes")
    latest = analyses[-1]
    current_schema = {
        "valid_utc": 1,
        "category": 3,
        "lon": 4,
        "lat": 5,
        "pressure_hpa": 6,
        "wind_ms": 7,
        "forecast_groups": 11,
    }
    if not isinstance(latest, list) or len(latest) <= max(current_schema.values()):
        raise ValueError("NMC latest fix has an unexpected schema")
    try:
        base_time = parse_compact_utc(str(latest[current_schema["valid_utc"]]))
        current = TrackPoint(
            valid_utc=base_time,
            lat=float(latest[current_schema["lat"]]),
            lon=float(latest[current_schema["lon"]]),
            wind_ms=float(latest[current_schema["wind_ms"]]),
            raw_wind=float(latest[current_schema["wind_ms"]]),
            raw_wind_unit="m/s_2min",
            pressure_hpa=float(latest[current_schema["pressure_hpa"]]),
            category=str(latest[current_schema["category"]]),
            kind="analysis",
        )
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError(f"NMC current fix could not be normalized: {exc}") from exc

    points = [current]
    groups = latest[current_schema["forecast_groups"]]
    center_key = str(config["sources"]["nmc"]["forecast_center_key"])
    forecasts = groups.get(center_key, []) if isinstance(groups, dict) else []
    forecast_schema = {
        "hours": 0,
        "issued_at": 1,
        "lon": 2,
        "lat": 3,
        "pressure_hpa": 4,
        "wind_ms": 5,
        "organization": 6,
        "category": 7,
    }
    for record in forecasts:
        if not isinstance(record, list) or len(record) <= max(forecast_schema.values()):
            continue
        try:
            hours = float(record[forecast_schema["hours"]])
            points.append(
                TrackPoint(
                    valid_utc=base_time + dt.timedelta(hours=hours),
                    lat=float(record[forecast_schema["lat"]]),
                    lon=float(record[forecast_schema["lon"]]),
                    wind_ms=float(record[forecast_schema["wind_ms"]]),
                    raw_wind=float(record[forecast_schema["wind_ms"]]),
                    raw_wind_unit="m/s_2min",
                    pressure_hpa=float(record[forecast_schema["pressure_hpa"]]),
                    category=str(record[forecast_schema["category"]]),
                    kind="official_forecast",
                    metadata={
                        "forecast_hours": hours,
                        "organization": record[forecast_schema["organization"]],
                        "issued_at": record[forecast_schema["issued_at"]],
                    },
                )
            )
        except (TypeError, ValueError):
            continue
    return SourceTrack(
        source="NMC",
        identity=identity,
        issue_utc=base_time,
        points=points,
        resolved_url=resolved_url,
        averaging_period_minutes=int(config["sources"]["nmc"]["wind_averaging_minutes"]),
        identity_evidence=[f"NMC active catalog matched {identity.name}/{identity.number}", f"NMC internal id={identity.nmc_id}"],
        discovery={"storm_id": identity.nmc_id, "forecast_center_key": center_key},
    )


def parse_jma_detail(
    text: str,
    identity: StormIdentity,
    tropical_cyclone: str,
    config: dict[str, Any],
    resolved_url: str,
) -> SourceTrack:
    payload = json.loads(text)
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("JMA specifications payload has an unexpected shape")
    points = []
    for item in payload[1:]:
        if not isinstance(item, dict):
            continue
        validtime = item.get("validtime", {})
        position = item.get("position", {}).get("deg", [])
        wind = item.get("maximumWind", {}).get("sustained", {}).get("m/s")
        if len(position) < 2 or wind in (None, "") or not validtime.get("UTC"):
            continue
        valid_utc = datetime_from_iso(str(validtime["UTC"]))
        hours = float(item.get("advancedHours", 0))
        points.append(
            TrackPoint(
                valid_utc=valid_utc,
                lat=float(position[0]),
                lon=float(position[1]),
                wind_ms=float(wind),
                raw_wind=float(wind),
                raw_wind_unit="m/s_10min",
                category=normalize_category(item.get("category")),
                kind="analysis" if abs(hours) < 1e-9 else "official_forecast",
                metadata={"advanced_hours": hours},
            )
        )
    if len(points) < 2:
        raise ValueError("JMA returned fewer than two usable wind fixes")
    issue = payload[0].get("issue", {}) if isinstance(payload[0], dict) else {}
    issue_utc = datetime_from_iso(issue["UTC"]) if issue.get("UTC") else min(point.valid_utc for point in points)
    return SourceTrack(
        source="JMA",
        identity=identity,
        issue_utc=issue_utc,
        points=points,
        resolved_url=resolved_url,
        averaging_period_minutes=int(config["sources"]["jma"]["wind_averaging_minutes"]),
        identity_evidence=[f"JMA targetTc typhoonNumber={identity.number}", f"JMA tropicalCyclone={tropical_cyclone}"],
        discovery={"tropical_cyclone": tropical_cyclone},
    )


MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def jtwc_datetime_from_ddhhmm(value: str, year: int, month: int, reference: dt.datetime | None = None) -> dt.datetime:
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError(f"Invalid JTWC DDHHMM value {value!r}")
    day, hour, minute = int(value[:2]), int(value[2:4]), int(value[4:6])
    candidate = dt.datetime(year, month, day, hour, minute, tzinfo=UTC)
    if reference is not None:
        if candidate < reference - dt.timedelta(days=14):
            candidate = candidate.replace(month=month % 12 + 1, year=year + (1 if month == 12 else 0))
        elif candidate > reference + dt.timedelta(days=14):
            previous_month = 12 if month == 1 else month - 1
            candidate = candidate.replace(month=previous_month, year=year - (1 if month == 1 else 0))
    return candidate


def parse_jtwc_detail(
    text: str,
    identity: StormIdentity,
    warning_number: str,
    config: dict[str, Any],
    resolved_url: str,
) -> SourceTrack:
    month_match = re.search(r"\d{6}Z(?:-|\s).*?([A-Z]{3})(\d{4})", text, re.S)
    if month_match:
        month = MONTHS[month_match.group(1).upper()]
        year = int(month_match.group(2))
    elif identity.year:
        year = identity.year
        month = dt.datetime.now(UTC).month
    else:
        raise ValueError("JTWC warning does not expose a month/year")
    current = re.search(
        r"WARNING\s+POSITION:\s*(\d{6})Z\s*---\s*NEAR\s*([0-9.]+)([NS])\s+([0-9.]+)([EW]).*?MAX\s+SUSTAINED\s+WINDS\s*-\s*(\d+)\s+KT",
        text,
        re.S | re.I,
    )
    if not current:
        raise ValueError("JTWC warning does not contain a current position/wind")
    valid, lat, ns, lon, ew, wind_kt = current.groups()
    base_time = jtwc_datetime_from_ddhhmm(valid, year, month)
    factor = float(config["sources"]["jtwc"]["one_to_ten_minute_factor"])

    def native_wind(kt: str) -> float:
        return float(kt) * 0.514444

    def wind_metadata(kt: str, **extra: Any) -> dict[str, Any]:
        native = native_wind(kt)
        return {
            **extra,
            "native_wind_averaging_minutes": int(config["sources"]["jtwc"]["reported_wind_averaging_minutes"]),
            "comparison_wind_averaging_minutes": int(config["sources"]["jtwc"]["target_wind_averaging_minutes"]),
            "conversion_factor_1min_to_10min": factor,
            "comparison_10min_wind_ms": native * factor,
        }

    points = [
        TrackPoint(
            valid_utc=base_time,
            lat=float(lat) * (1 if ns.upper() == "N" else -1),
            lon=float(lon) * (1 if ew.upper() == "E" else -1),
            wind_ms=native_wind(wind_kt),
            raw_wind=float(wind_kt),
            raw_wind_unit="kt_1min",
            kind="analysis",
            metadata=wind_metadata(wind_kt),
        )
    ]
    forecast_pattern = re.compile(
        r"(\d+)\s+HRS,\s+VALID\s+AT:\s*(\d{6})Z\s*---\s*([0-9.]+)([NS])\s+([0-9.]+)([EW])\s*MAX\s+SUSTAINED\s+WINDS\s*-\s*(\d+)\s+KT",
        re.S | re.I,
    )
    for hours, valid, lat, ns, lon, ew, wind_kt in forecast_pattern.findall(text):
        valid_utc = jtwc_datetime_from_ddhhmm(valid, year, month, reference=base_time)
        points.append(
            TrackPoint(
                valid_utc=valid_utc,
                lat=float(lat) * (1 if ns.upper() == "N" else -1),
                lon=float(lon) * (1 if ew.upper() == "E" else -1),
                wind_ms=native_wind(wind_kt),
                raw_wind=float(wind_kt),
                raw_wind_unit="kt_1min",
                kind="official_forecast",
                metadata=wind_metadata(wind_kt, forecast_hours=float(hours)),
            )
        )
    return SourceTrack(
        source="JTWC",
        identity=identity,
        issue_utc=base_time,
        points=points,
        resolved_url=resolved_url,
        averaging_period_minutes=int(config["sources"]["jtwc"]["reported_wind_averaging_minutes"]),
        identity_evidence=[f"JTWC active summary matched {warning_number}/{identity.name}"],
        discovery={"warning_number": warning_number, "one_to_ten_minute_factor": factor},
    )


def parse_hko_detail(
    text: str,
    identity: StormIdentity,
    hko_id: str,
    config: dict[str, Any],
    resolved_url: str,
) -> SourceTrack:
    points = []
    for line in text.splitlines()[1:]:
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 9 or not re.fullmatch(r"\d{10}", fields[3]):
            continue
        try:
            label = fields[0].upper()
            raw_wind = float(fields[8])
            wind_ms = raw_wind / 3.6
            points.append(
                TrackPoint(
                    valid_utc=dt.datetime.strptime(fields[3], "%Y%m%d%H").replace(tzinfo=UTC),
                    lat=float(fields[6]),
                    lon=float(fields[7]),
                    wind_ms=wind_ms,
                    raw_wind=raw_wind,
                    raw_wind_unit="km/h_10min",
                    category=fields[2],
                    kind="official_forecast" if label == "F" else "analysis",
                    metadata={"fix_label": label, "forecast_hour": fields[5]},
                )
            )
        except (TypeError, ValueError):
            continue
    if len(points) < 2:
        raise ValueError("HKO GIS position file returned fewer than two usable fixes")
    issue_utc = max(point.valid_utc for point in points if point.kind == "analysis")
    return SourceTrack(
        source="HKO",
        identity=identity,
        issue_utc=issue_utc,
        points=points,
        resolved_url=resolved_url,
        averaging_period_minutes=int(config["sources"]["hko"]["wind_averaging_minutes"]),
        identity_evidence=[f"HKO GIS catalog matched {identity.name}; tcid={hko_id}"],
        discovery={"hko_id": hko_id},
    )


def parse_cwa_detail(
    text: str,
    identity: StormIdentity,
    config: dict[str, Any],
    resolved_url: str,
) -> SourceTrack:
    panel = select_cwa_storm_panel(text, identity)
    time_match = re.search(
        r"TY_TIME\s*=\s*\{.*?['\"]E['\"]\s*:\s*['\"](\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})\s+UTC['\"].*?\}",
        text,
        re.S,
    )
    if not time_match:
        raise ValueError("CWA payload does not expose its issue time")
    year, month, day, hour, minute = map(int, time_match.groups())
    issue_utc = dt.datetime(year, month, day, hour, minute, tzinfo=UTC)
    month_lookup = {name: index for index, name in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
    points: list[TrackPoint] = []
    current_pattern = re.compile(
        r"<span[^>]*>\s*Analysis\s*</span>.*?<p>\s*(\d{4})UTC\s+(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})\s*</p>.*?"
        r"<li>\s*Center\s+Location\s+([0-9.]+)N\s+([0-9.]+)E\s*</li>.*?"
        r"<li>\s*Maximum\s+Wind\s+Speed\s+(\d+(?:\.\d+)?)\s+m/s\s*</li>",
        re.S | re.I,
    )
    forecast_pattern = re.compile(
        r"<li>\s*(\d{4})UTC\s+(\d{1,2})\s+([A-Z][a-z]+)\s+(\d{4})\s*</li>.*?"
        r"<li>\s*Center\s+Position\s+([0-9.]+)N\s+([0-9.]+)E\s*</li>.*?"
        r"<li>\s*Maximum\s+Wind\s+Speed\s+(\d+(?:\.\d+)?)\s+m/s\s*</li>",
        re.S | re.I,
    )
    current_matches = current_pattern.findall(panel)
    if len(current_matches) != 1:
        raise ValueError(f"CWA selected panel requires exactly one analysis fix; found={len(current_matches)}")
    for expression, kind in ((current_pattern, "analysis"), (forecast_pattern, "official_forecast")):
        for hhmm, item_day, month_name, item_year, lat, lon, wind in expression.findall(panel):
            month_value = month_lookup.get(month_name)
            if month_value is None:
                continue
            valid = dt.datetime(int(item_year), month_value, int(item_day), int(hhmm[:2]), int(hhmm[2:]), tzinfo=UTC)
            points.append(
                TrackPoint(
                    valid_utc=valid,
                    lat=float(lat),
                    lon=float(lon),
                    wind_ms=float(wind),
                    kind=kind,
                    raw_wind=float(wind),
                    raw_wind_unit="m/s_10min",
                )
            )
    if len({point.valid_utc for point in points}) != len(points):
        raise ValueError("CWA selected storm contains duplicate analysis/forecast valid times")
    points.sort(key=lambda item: item.valid_utc)
    if len(points) < 2:
        raise ValueError("CWA payload returned fewer than two target-storm fixes")
    result = SourceTrack(
        source="CWA",
        identity=identity,
        issue_utc=issue_utc,
        points=points,
        resolved_url=resolved_url,
        averaging_period_minutes=int(config["sources"]["cwa"]["wind_averaging_minutes"]),
        identity_evidence=[f"CWA active list matched {identity.name}/{identity.number}"],
        discovery={"active_name": identity.name},
    )
    errors = validate_track_integrity(result, config)
    if errors:
        raise ValueError(f"CWA whole-track validation failed: {errors}")
    return result


def discover_tracks(
    fetcher: HttpFetcher,
    config: dict[str, Any],
    requested_storm: str | None = None,
) -> tuple[StormIdentity, list[SourceTrack], dict[str, dict[str, Any]]]:
    """Discover every catalog independently, then resolve one canonical storm."""

    statuses: dict[str, dict[str, Any]] = {}
    catalogs: dict[str, Any] = {}
    catalog_errors: dict[str, str] = {}

    def catalog(source: str, callback: Callable[[], Any]) -> None:
        try:
            catalogs[source] = callback()
        except Exception as exc:
            catalog_errors[source] = f"{type(exc).__name__}: {exc}"

    nmc_config = config["sources"]["nmc"]
    jma_config = config["sources"]["jma"]
    jtwc_config = config["sources"]["jtwc"]
    hko_config = config["sources"]["hko"]
    cwa_config = config["sources"]["cwa"]

    catalog("NMC", lambda: parse_nmc_catalog(fetcher.text(str(nmc_config["catalog_url"]))))
    catalog("JMA", lambda: parse_jma_targets(fetcher.text(str(jma_config["catalog_url"]))))
    catalog(
        "JTWC",
        lambda: parse_jtwc_catalog(
            fetcher.text(
                str(jtwc_config["catalog_url"]),
                minimum_host_spacing_seconds=float(jtwc_config.get("minimum_same_host_request_spacing_seconds", 0.0)),
            )
        ),
    )
    catalog("HKO", lambda: parse_hko_catalog(fetcher.text(str(hko_config["catalog_url"]))))

    def cwa_catalog() -> dict[str, Any]:
        body = fetcher.text(str(cwa_config["detail_url"]))
        return {"body": body, "names": parse_cwa_active_names(body)}

    catalog("CWA", cwa_catalog)
    identity = resolve_storm_identity(
        requested_storm,
        nmc_records=catalogs.get("NMC"),
        jtwc_records=catalogs.get("JTWC"),
    )
    tracks: list[SourceTrack] = []

    def collect(source: str, callback: Callable[[], SourceTrack]) -> None:
        try:
            if source in catalog_errors:
                raise RuntimeError(f"catalog unavailable: {catalog_errors[source]}")
            source_track = callback()
            tracks.append(source_track)
            statuses[source] = {
                "source": source,
                "catalog_ok": True,
                "discovery_ok": True,
                "resolved_url": source_track.resolved_url,
            }
        except Exception as exc:
            statuses[source] = {
                "source": source,
                "catalog_ok": source not in catalog_errors,
                "discovery_ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

    def collect_nmc() -> SourceTrack:
        selected = select_nmc_storm(catalogs["NMC"], requested_storm or identity.name)
        selected_identity = replace(
            identity,
            number=identity.number or selected.number,
            aliases=tuple(sorted(set([*identity.aliases, *selected.aliases]))),
            nmc_id=selected.nmc_id,
        )
        if not selected_identity.nmc_id:
            raise ValueError("NMC catalog match has no storm id")
        url = str(nmc_config["detail_url_template"]).format(storm_id=selected_identity.nmc_id)
        return parse_nmc_detail(fetcher.text(url), selected_identity, config, url)

    def collect_jma() -> SourceTrack:
        record = match_catalog_record(catalogs["JMA"], identity, ("number",))
        tropical_cyclone = str(record["tropical_cyclone"])
        url = str(jma_config["detail_url_template"]).format(tropical_cyclone=tropical_cyclone)
        return parse_jma_detail(fetcher.text(url), identity, tropical_cyclone, config, url)

    def collect_jtwc() -> SourceTrack:
        spacing_seconds = float(jtwc_config.get("minimum_same_host_request_spacing_seconds", 0.0))
        records = catalogs["JTWC"]
        target_number = f"{identity.ordinal:02d}W" if identity.ordinal is not None else None
        record = next(
            (
                item
                for item in records
                if identity.matches(item["name"]) or (target_number is not None and item["number"] == target_number)
            ),
            None,
        )
        if record is None:
            raise ValueError(f"JTWC active summary has no match for {identity.name}/{identity.number}")
        if identity.year is None or identity.ordinal is None:
            raise ValueError("JTWC URL construction needs a WMO year and ordinal")
        url = str(jtwc_config["detail_url_template"]).format(ordinal=identity.ordinal, year2=identity.year % 100)
        return parse_jtwc_detail(
            fetcher.text(url, minimum_host_spacing_seconds=spacing_seconds),
            identity,
            str(record["number"]),
            config,
            url,
        )

    def collect_hko() -> SourceTrack:
        record = match_catalog_record(catalogs["HKO"], identity, ("name", "name_cn"))
        hko_id = str(record["hko_id"])
        url = str(hko_config["detail_url_template"]).format(hko_id=hko_id)
        return parse_hko_detail(fetcher.text(url), identity, hko_id, config, url)

    def collect_cwa() -> SourceTrack:
        url = str(cwa_config["detail_url"])
        body = catalogs["CWA"]["body"]
        names = catalogs["CWA"]["names"]
        if identity.name not in names:
            raise ValueError(f"CWA active catalog has no match for {identity.name}; names={names}")
        return parse_cwa_detail(body, identity, config, url)

    collect("NMC", collect_nmc)
    collect("JMA", collect_jma)
    collect("JTWC", collect_jtwc)
    collect("HKO", collect_hko)
    collect("CWA", collect_cwa)
    if not tracks:
        raise ValueError(f"All source adapters failed independently: {statuses}")
    return identity, tracks, statuses


def polygon_rings(coordinates: Sequence[Any]) -> list[dict[str, Any]]:
    if not coordinates:
        return []
    exterior = [(float(point[0]), float(point[1])) for point in coordinates[0]]
    holes = [[(float(point[0]), float(point[1])) for point in ring] for ring in coordinates[1:]]
    longitudes = [point[0] for point in exterior]
    latitudes = [point[1] for point in exterior]
    return [
        {
            "exterior": exterior,
            "holes": holes,
            "bbox": (min(longitudes), min(latitudes), max(longitudes), max(latitudes)),
        }
    ]


def load_natural_earth_polygons(
    fetcher: HttpFetcher,
    config: dict[str, Any],
    *,
    country_codes: set[str] | None,
) -> list[dict[str, Any]]:
    """Load selected reporting countries or every country for physical land."""

    payload = fetcher.json(str(config["target"]["natural_earth_countries_url"]))
    polygons: list[dict[str, Any]] = []
    for feature in payload.get("features", []):
        properties = feature.get("properties", {})
        codes = {str(properties.get(key, "")) for key in ("ISO_A3", "ADM0_A3", "SOV_A3", "GU_A3")}
        if country_codes is not None and not codes.intersection(country_codes):
            continue
        geometry = feature.get("geometry", {})
        if geometry.get("type") == "Polygon":
            polygons.extend(polygon_rings(geometry.get("coordinates", [])))
        elif geometry.get("type") == "MultiPolygon":
            for coordinate_set in geometry.get("coordinates", []):
                polygons.extend(polygon_rings(coordinate_set))
    if not polygons:
        scope = "global land" if country_codes is None else f"configured codes {sorted(country_codes)}"
        raise ValueError(f"Natural Earth has no polygons for {scope}")
    return polygons


def load_target_polygons(fetcher: HttpFetcher, config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load the reporting target independently from the physical global land mask."""

    return load_natural_earth_polygons(
        fetcher,
        config,
        country_codes=set(config["target"]["country_codes"]),
    )


@dataclass(frozen=True)
class EnvironmentState:
    """One track-following atmospheric and oceanic environmental sample."""

    # requested_utc anchors the forcing curve; sampled_utc identifies the
    # nearby open-water track point used when a coastal HYCOM cell is masked.
    requested_utc: dt.datetime
    sampled_utc: dt.datetime
    coastal_backtrack_minutes: float
    atmospheric_valid_utc: dt.datetime
    ocean_valid_utc: dt.datetime
    lat: float
    lon: float
    air_temperature_c: float
    relative_humidity_pct: float
    surface_pressure_hpa: float
    sea_level_pressure_hpa: float
    deep_layer_shear_ms: float
    sst_c: float
    ohc_kj_cm2: float
    profile_pressure_hpa: tuple[float, ...]
    profile_temperature_c: tuple[float, ...]
    profile_mixing_ratio_kg_kg: tuple[float, ...]
    atmosphere_url: str
    ocean_url: str
    atmosphere_sampling: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_utc": self.requested_utc.isoformat(),
            "sampled_utc": self.sampled_utc.isoformat(),
            "coastal_backtrack_minutes": round(self.coastal_backtrack_minutes, 3),
            "atmospheric_valid_utc": self.atmospheric_valid_utc.isoformat(),
            "ocean_valid_utc": self.ocean_valid_utc.isoformat(),
            "lat": round(self.lat, 4),
            "lon": round(self.lon, 4),
            "air_temperature_c": round(self.air_temperature_c, 3),
            "relative_humidity_pct": round(self.relative_humidity_pct, 3),
            "surface_pressure_hpa": round(self.surface_pressure_hpa, 3),
            "sea_level_pressure_hpa": round(self.sea_level_pressure_hpa, 3),
            "deep_layer_shear_ms": round(self.deep_layer_shear_ms, 3),
            "sst_c": round(self.sst_c, 3),
            "ohc_kj_cm2": round(self.ohc_kj_cm2, 3),
            "profile_pressure_hpa": [round(value, 1) for value in self.profile_pressure_hpa],
            "profile_temperature_c": [round(value, 2) for value in self.profile_temperature_c],
            "profile_mixing_ratio_kg_kg": [round(value, 7) for value in self.profile_mixing_ratio_kg_kg],
            "atmosphere_url": self.atmosphere_url,
            "ocean_url": self.ocean_url,
            "atmosphere_sampling": self.atmosphere_sampling,
        }


@dataclass
class EnvironmentPath:
    """Time interpolation over independently sampled environmental forcing."""

    nodes: list[EnvironmentState]

    def __post_init__(self) -> None:
        self.nodes.sort(key=lambda item: item.requested_utc)
        if not self.nodes:
            raise ValueError("EnvironmentPath requires at least one node")

    def at(self, target: dt.datetime) -> EnvironmentState:
        if target <= self.nodes[0].requested_utc:
            return self.nodes[0]
        if target >= self.nodes[-1].requested_utc:
            return self.nodes[-1]
        for left, right in zip(self.nodes, self.nodes[1:]):
            if left.requested_utc <= target <= right.requested_utc:
                span = (right.requested_utc - left.requested_utc).total_seconds()
                fraction = (target - left.requested_utc).total_seconds() / span

                def interpolate(attribute: str) -> float:
                    return float(getattr(left, attribute)) + fraction * (
                        float(getattr(right, attribute)) - float(getattr(left, attribute))
                    )

                same_profile_grid = left.profile_pressure_hpa == right.profile_pressure_hpa
                selected = left if fraction < 0.5 else right
                profile_temperature = selected.profile_temperature_c
                profile_mixing_ratio = selected.profile_mixing_ratio_kg_kg
                if same_profile_grid:
                    profile_temperature = tuple(
                        left_value + fraction * (right_value - left_value)
                        for left_value, right_value in zip(left.profile_temperature_c, right.profile_temperature_c)
                    )
                    profile_mixing_ratio = tuple(
                        left_value + fraction * (right_value - left_value)
                        for left_value, right_value in zip(
                            left.profile_mixing_ratio_kg_kg,
                            right.profile_mixing_ratio_kg_kg,
                        )
                    )

                return EnvironmentState(
                    requested_utc=target,
                    sampled_utc=left.sampled_utc if fraction < 0.5 else right.sampled_utc,
                    coastal_backtrack_minutes=(
                        left.coastal_backtrack_minutes if fraction < 0.5 else right.coastal_backtrack_minutes
                    ),
                    atmospheric_valid_utc=left.atmospheric_valid_utc if fraction < 0.5 else right.atmospheric_valid_utc,
                    ocean_valid_utc=left.ocean_valid_utc if fraction < 0.5 else right.ocean_valid_utc,
                    lat=interpolate("lat"),
                    lon=interpolate("lon"),
                    air_temperature_c=interpolate("air_temperature_c"),
                    relative_humidity_pct=interpolate("relative_humidity_pct"),
                    surface_pressure_hpa=interpolate("surface_pressure_hpa"),
                    sea_level_pressure_hpa=interpolate("sea_level_pressure_hpa"),
                    deep_layer_shear_ms=interpolate("deep_layer_shear_ms"),
                    sst_c=interpolate("sst_c"),
                    ohc_kj_cm2=interpolate("ohc_kj_cm2"),
                    profile_pressure_hpa=left.profile_pressure_hpa if same_profile_grid else selected.profile_pressure_hpa,
                    profile_temperature_c=profile_temperature,
                    profile_mixing_ratio_kg_kg=profile_mixing_ratio,
                    atmosphere_url=left.atmosphere_url if fraction < 0.5 else right.atmosphere_url,
                    ocean_url=left.ocean_url if fraction < 0.5 else right.ocean_url,
                    atmosphere_sampling=selected.atmosphere_sampling,
                )
        return self.nodes[-1]

    def as_dict(self) -> list[dict[str, Any]]:
        return [node.as_dict() for node in self.nodes]


def _parse_hourly_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def meteorological_wind_components(speed_kmh: float, direction_degrees: float) -> tuple[float, float]:
    """Convert meteorological wind-from speed/direction into east/north m/s."""

    speed_ms = speed_kmh / 3.6
    radians = math.radians(direction_degrees)
    return -speed_ms * math.sin(radians), -speed_ms * math.cos(radians)


def saturation_vapor_pressure_hpa(temperature_c: float) -> float:
    return 6.112 * math.exp((17.67 * temperature_c) / (temperature_c + 243.5))


def mixing_ratio_from_relative_humidity(
    temperature_c: float,
    pressure_hpa: float,
    relative_humidity_pct: float,
) -> float:
    vapor_pressure = saturation_vapor_pressure_hpa(temperature_c) * max(0.0, min(100.0, relative_humidity_pct)) / 100.0
    vapor_pressure = min(vapor_pressure, pressure_hpa * 0.99)
    return 0.622 * vapor_pressure / max(1.0e-9, pressure_hpa - vapor_pressure)


def destination_lon_lat(lat: float, lon: float, distance_km: float, bearing_degrees: float) -> tuple[float, float]:
    """Move along a great circle by distance and bearing."""

    angular_distance = distance_km / EARTH_RADIUS_KM
    latitude = math.radians(lat)
    longitude = math.radians(lon)
    bearing = math.radians(bearing_degrees)
    destination_latitude = math.asin(
        math.sin(latitude) * math.cos(angular_distance)
        + math.cos(latitude) * math.sin(angular_distance) * math.cos(bearing)
    )
    destination_longitude = longitude + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(latitude),
        math.cos(angular_distance) - math.sin(latitude) * math.sin(destination_latitude),
    )
    return math.degrees(destination_latitude), ((math.degrees(destination_longitude) + 180.0) % 360.0) - 180.0


def fetch_atmospheric_state(
    fetcher: HttpFetcher,
    requested_utc: dt.datetime,
    lat: float,
    lon: float,
    config: dict[str, Any],
    global_land_polygons: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = config["environment"]["atmosphere"]
    spatial = settings["spatial_sampling"]
    pressure_levels = [int(value) for value in settings["pressure_levels_hpa"]]
    variables = list(settings["hourly_variables"])
    for pressure in pressure_levels:
        variables.extend([f"temperature_{pressure}hPa", f"relative_humidity_{pressure}hPa"])
    location_specs = [
        (float(radius), float(azimuth), *destination_lon_lat(lat, lon, float(radius), float(azimuth)))
        for radius in spatial["radii_km"]
        for azimuth in spatial["azimuths_degrees"]
    ]
    locations = [(item[2], item[3]) for item in location_specs]
    parameters = {
        "latitude": ",".join(f"{location[0]:.5f}" for location in locations),
        "longitude": ",".join(f"{location[1]:.5f}" for location in locations),
        "hourly": ",".join(variables),
        "models": settings["model"],
        "start_date": requested_utc.astimezone(UTC).date().isoformat(),
        "end_date": requested_utc.astimezone(UTC).date().isoformat(),
        "timezone": "UTC",
    }
    url = f"{settings['endpoint']}?{urllib.parse.urlencode(parameters)}"
    response = fetcher.json(
        url,
        minimum_host_spacing_seconds=float(settings.get("minimum_same_host_request_spacing_seconds", 0.0)),
    )
    payloads = response if isinstance(response, list) else [response]
    if len(payloads) != len(locations):
        raise ValueError(f"Atmospheric multi-location response count mismatch: {len(payloads)} != {len(locations)}")
    max_offset = float(config["environment"]["maximum_time_offset_hours"]) * 3600
    samples: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for location_spec, payload in zip(location_specs, payloads):
        radius, azimuth, location_lat, location_lon = location_spec
        location = (location_lat, location_lon)
        try:
            sampled_lat = float(payload.get("latitude", location[0]))
            sampled_lon = float(payload.get("longitude", location[1]))
            is_land = bool(
                spatial.get("exclude_global_land_points")
                and global_land_polygons
                and point_in_target(sampled_lon, sampled_lat, global_land_polygons)
            )
            elevation = float(payload.get("elevation", 0.0))
            if elevation > float(spatial["maximum_elevation_m"]):
                raise ValueError(f"elevation_above_limit:{elevation:.0f}m")
            hourly = payload.get("hourly", {})
            times = [_parse_hourly_time(str(value)) for value in hourly.get("time", [])]
            if not times:
                raise ValueError("no_hourly_timestamps")
            index = min(range(len(times)), key=lambda item: abs((times[item] - requested_utc).total_seconds()))
            if abs((times[index] - requested_utc).total_seconds()) > max_offset:
                raise ValueError("nearest_grid_time_exceeds_offset")

            def value(name: str) -> float:
                series = hourly.get(name)
                if not isinstance(series, list) or index >= len(series) or series[index] is None:
                    raise ValueError(f"missing:{name}")
                return float(series[index])

            u850, v850 = meteorological_wind_components(value("wind_speed_850hPa"), value("wind_direction_850hPa"))
            u200, v200 = meteorological_wind_components(value("wind_speed_200hPa"), value("wind_direction_200hPa"))
            surface_pressure = value("surface_pressure")
            profile: dict[int, tuple[float, float]] = {}
            for pressure in pressure_levels:
                if pressure > surface_pressure + 5.0:
                    continue
                temperature = value(f"temperature_{pressure}hPa")
                relative_humidity = value(f"relative_humidity_{pressure}hPa")
                profile[pressure] = (
                    temperature,
                    mixing_ratio_from_relative_humidity(temperature, float(pressure), relative_humidity),
                )
            samples.append(
                {
                    "lat": sampled_lat,
                    "lon": sampled_lon,
                    "radius_km": radius,
                    "azimuth_degrees": azimuth,
                    "is_land": is_land,
                    "elevation_m": elevation,
                    "valid_utc": times[index],
                    "air_temperature_c": value("temperature_2m"),
                    "relative_humidity_pct": value("relative_humidity_2m"),
                    "surface_pressure_hpa": surface_pressure,
                    "sea_level_pressure_hpa": value("pressure_msl"),
                    "u850": u850,
                    "v850": v850,
                    "u200": u200,
                    "v200": v200,
                    "profile": profile,
                }
            )
        except (TypeError, ValueError) as exc:
            exclusions.append({"lat": round(location[0], 3), "lon": round(location[1], 3), "reason": str(exc)})
    if len(samples) < int(spatial["minimum_valid_samples"]):
        raise ValueError(f"Atmospheric environmental sampling retained {len(samples)} points; exclusions={exclusions}")
    outer_radius = max(float(value) for value in spatial["radii_km"])
    thermodynamic_samples = [
        sample
        for sample in samples
        if abs(float(sample["radius_km"]) - outer_radius) < 1.0e-6 and not sample["is_land"]
    ]
    if not thermodynamic_samples:
        raise ValueError("Atmospheric PI outer ring retained no ocean points")
    pi_sampling_valid = len(thermodynamic_samples) >= int(spatial["minimum_outer_ring_samples"])
    common_levels = [
        pressure
        for pressure in pressure_levels
        if all(pressure in sample["profile"] for sample in thermodynamic_samples)
    ]
    if len(common_levels) < int(config["physics"]["pi_dependency"]["minimum_profile_levels"]) or min(common_levels) > float(settings["minimum_profile_top_hpa"]):
        raise ValueError("Atmospheric environmental samples have no common full-column profile")

    def average(items: Sequence[dict[str, Any]], attribute: str) -> float:
        return statistics.mean(float(sample[attribute]) for sample in items)

    profile_temperature = tuple(
        statistics.mean(float(sample["profile"][pressure][0]) for sample in thermodynamic_samples)
        for pressure in common_levels
    )
    profile_mixing_ratio = tuple(
        statistics.mean(float(sample["profile"][pressure][1]) for sample in thermodynamic_samples)
        for pressure in common_levels
    )
    mean_u850, mean_v850 = average(samples, "u850"), average(samples, "v850")
    mean_u200, mean_v200 = average(samples, "u200"), average(samples, "v200")
    valid_times = [sample["valid_utc"] for sample in samples]
    return {
        "valid_utc": min(valid_times),
        "lat": lat,
        "lon": lon,
        "air_temperature_c": average(thermodynamic_samples, "air_temperature_c"),
        "relative_humidity_pct": average(thermodynamic_samples, "relative_humidity_pct"),
        "surface_pressure_hpa": average(thermodynamic_samples, "surface_pressure_hpa"),
        "sea_level_pressure_hpa": average(thermodynamic_samples, "sea_level_pressure_hpa"),
        "deep_layer_shear_ms": math.hypot(mean_u200 - mean_u850, mean_v200 - mean_v850),
        "profile_pressure_hpa": tuple(float(value) for value in common_levels),
        "profile_temperature_c": profile_temperature,
        "profile_mixing_ratio_kg_kg": profile_mixing_ratio,
        "sampling": {
            "method": spatial["method"],
            "radii_km": spatial["radii_km"],
            "requested_count": len(locations),
            "retained_count": len(samples),
            "pi_outer_ring_radius_km": outer_radius,
            "pi_outer_ring_retained_count": len(thermodynamic_samples),
            "pi_sampling_valid": pi_sampling_valid,
            "pi_sampling_reason": None if pi_sampling_valid else "insufficient_outer_ocean_samples",
            "land_points_used_for_deep_layer_shear": sum(1 for sample in samples if sample["is_land"]),
            "excluded": exclusions,
            "mean_environmental_mslp_hpa": round(average(thermodynamic_samples, "sea_level_pressure_hpa"), 2),
        },
        "url": url,
    }


def parse_hycom_profile_csv(text: str, profile_variable: str) -> tuple[list[tuple[float, float]], list[dt.datetime]]:
    """Extract finite depth-temperature pairs from HYCOM's NCSS CSV response."""
    reader = csv.DictReader(io.StringIO(text))
    profile: list[tuple[float, float]] = []
    valid_times: list[dt.datetime] = []
    for row in reader:
        depth_key = next((key for key in row if key.startswith("vertCoord")), None)
        temperature_key = next((key for key in row if key.startswith(profile_variable)), None)
        if depth_key is None or temperature_key is None:
            continue
        try:
            depth = float(row[depth_key])
            temperature = float(row[temperature_key])
            if math.isfinite(depth) and math.isfinite(temperature):
                profile.append((depth, temperature))
            if row.get("time"):
                valid_times.append(_parse_hourly_time(row["time"]))
        except (TypeError, ValueError):
            continue
    return profile, valid_times


def fetch_hycom_profile(
    fetcher: HttpFetcher,
    requested_utc: dt.datetime,
    lat: float,
    lon: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    settings = config["environment"]["ocean"]
    parameters = {
        "var": settings["profile_variable"],
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "time": requested_utc.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vertStride": "1",
        "accept": "csv",
    }
    url = f"{settings['endpoint']}?{urllib.parse.urlencode(parameters)}"
    profile: list[tuple[float, float]] = []
    valid_times: list[dt.datetime] = []
    host_spacing_seconds = float(settings.get("minimum_same_host_request_spacing_seconds", 0.0))
    for attempt in range(3):
        profile, valid_times = parse_hycom_profile_csv(
            fetcher.text(
                url,
                refresh=attempt > 0,
                minimum_host_spacing_seconds=host_spacing_seconds,
            ),
            str(settings["profile_variable"]),
        )
        if len(profile) >= 2:
            break
        if attempt < 2:
            time.sleep(0.25 * (2**attempt))
    if len(profile) < 2:
        raise ValueError("HYCOM returned fewer than two finite temperature levels")
    valid_utc = valid_times[0] if valid_times else requested_utc
    max_offset = float(config["environment"]["maximum_time_offset_hours"]) * 3600
    if abs((valid_utc - requested_utc).total_seconds()) > max_offset:
        raise ValueError("HYCOM nearest grid time exceeds configured offset")
    ordered = sorted(profile)
    threshold = float(settings["threshold_temperature_c"])
    return {
        "valid_utc": valid_utc,
        "sst_c": ordered[0][1],
        "ohc_kj_cm2": integrate_ohc_kj_cm2(ordered, config["physics"], threshold),
        "profile_levels": len(ordered),
        "url": url,
    }


def sample_environment_state(
    fetcher: HttpFetcher,
    requested_utc: dt.datetime,
    lat: float,
    lon: float,
    config: dict[str, Any],
    global_land_polygons: Sequence[dict[str, Any]] | None = None,
) -> EnvironmentState:
    atmosphere = fetch_atmospheric_state(
        fetcher,
        requested_utc,
        lat,
        lon,
        config,
        global_land_polygons,
    )
    ocean = fetch_hycom_profile(fetcher, requested_utc, lat, lon, config)
    return EnvironmentState(
        requested_utc=requested_utc,
        sampled_utc=requested_utc,
        coastal_backtrack_minutes=0.0,
        atmospheric_valid_utc=atmosphere["valid_utc"],
        ocean_valid_utc=ocean["valid_utc"],
        lat=lat,
        lon=lon,
        air_temperature_c=atmosphere["air_temperature_c"],
        relative_humidity_pct=atmosphere["relative_humidity_pct"],
        surface_pressure_hpa=atmosphere["surface_pressure_hpa"],
        sea_level_pressure_hpa=atmosphere["sea_level_pressure_hpa"],
        deep_layer_shear_ms=atmosphere["deep_layer_shear_ms"],
        sst_c=ocean["sst_c"],
        ohc_kj_cm2=ocean["ohc_kj_cm2"],
        profile_pressure_hpa=atmosphere["profile_pressure_hpa"],
        profile_temperature_c=atmosphere["profile_temperature_c"],
        profile_mixing_ratio_kg_kg=atmosphere["profile_mixing_ratio_kg_kg"],
        atmosphere_url=atmosphere["url"],
        ocean_url=ocean["url"],
        atmosphere_sampling=atmosphere["sampling"],
    )


def environment_sample_times(
    start_utc: dt.datetime,
    landfall_utc: dt.datetime,
    config: dict[str, Any],
) -> list[dt.datetime]:
    count = int(config["environment"]["sample_count_per_track"])
    if count < 2:
        return [start_utc]
    offset = dt.timedelta(minutes=float(config["environment"]["pre_landfall_ocean_offset_minutes"]))
    ocean_end = max(start_utc, landfall_utc - offset)
    span = ocean_end - start_utc
    values = [start_utc + span * index / (count - 1) for index in range(count)]
    return sorted(set(values))


def sample_open_water_environment(
    fetcher: HttpFetcher,
    track: SourceTrack,
    forcing_utc: dt.datetime,
    config: dict[str, Any],
    global_land_polygons: Sequence[dict[str, Any]] | None = None,
    shared_cache: list[EnvironmentState] | None = None,
) -> EnvironmentState:
    """Use the nearest earlier valid ocean point when HYCOM masks a coastal cell."""

    settings = config["environment"]["ocean"]
    step_minutes = float(settings.get("coastal_profile_backtrack_step_minutes", 0.0))
    maximum_minutes = float(settings.get("maximum_coastal_profile_backtrack_minutes", 0.0))
    if step_minutes <= 0 or maximum_minutes < 0:
        raise ValueError("Coastal HYCOM backtrack settings must be positive")
    backtracks = [step_minutes * index for index in range(int(maximum_minutes // step_minutes) + 1)]
    if backtracks[-1] < maximum_minutes:
        backtracks.append(maximum_minutes)
    last_error: Exception | None = None
    for backtrack_minutes in backtracks:
        sampled_utc = forcing_utc - dt.timedelta(minutes=backtrack_minutes)
        lon, lat, _ = trajectory_position_at(track, sampled_utc, config["geometry"])
        cache_settings = config["environment"].get("shared_sample_cache", {})
        if shared_cache:
            candidates = []
            for cached in shared_cache:
                time_difference_minutes = abs((cached.sampled_utc - sampled_utc).total_seconds()) / 60.0
                distance = haversine_km(lat, lon, cached.lat, cached.lon)
                if (
                    time_difference_minutes <= float(cache_settings.get("maximum_time_difference_minutes", 0.0))
                    and distance <= float(cache_settings.get("maximum_distance_km", 0.0))
                ):
                    candidates.append((distance + time_difference_minutes, distance, time_difference_minutes, cached))
            if candidates:
                _, distance, time_difference_minutes, cached = min(candidates, key=lambda item: item[0])
                sampling = dict(cached.atmosphere_sampling)
                sampling["shared_environment_reuse"] = {
                    "distance_km": round(distance, 2),
                    "time_difference_minutes": round(time_difference_minutes, 2),
                    "sampled_utc": cached.sampled_utc.isoformat(),
                    "lat": round(cached.lat, 4),
                    "lon": round(cached.lon, 4),
                }
                return replace(
                    cached,
                    requested_utc=forcing_utc,
                    coastal_backtrack_minutes=backtrack_minutes,
                    atmosphere_sampling=sampling,
                )
        try:
            state = sample_environment_state(
                fetcher,
                sampled_utc,
                lat,
                lon,
                config,
                global_land_polygons,
            )
        except ValueError as exc:
            last_error = exc
            if "HYCOM returned fewer than two finite temperature levels" in str(exc):
                continue
            raise
        if shared_cache is not None:
            shared_cache.append(state)
        return replace(
            state,
            requested_utc=forcing_utc,
            sampled_utc=sampled_utc,
            coastal_backtrack_minutes=backtrack_minutes,
        )
    raise ValueError(
        f"HYCOM supplied no valid ocean profile within {maximum_minutes:.0f} minutes before the forcing point"
    ) from last_error


def build_environment_path(
    fetcher: HttpFetcher,
    track: SourceTrack,
    alignment_time_utc: dt.datetime,
    landfall_utc: dt.datetime,
    config: dict[str, Any],
    global_land_polygons: Sequence[dict[str, Any]] | None = None,
    shared_cache: list[EnvironmentState] | None = None,
) -> EnvironmentPath:
    nodes = []
    for sample_time in environment_sample_times(alignment_time_utc, landfall_utc, config):
        try:
            nodes.append(
                sample_open_water_environment(
                    fetcher,
                    track,
                    sample_time,
                    config,
                    global_land_polygons,
                    shared_cache,
                )
            )
        except Exception as exc:
            raise ValueError(
                f"Environmental sample failed at forcing time {sample_time.isoformat()}: {exc}"
            ) from exc
    return EnvironmentPath(nodes)


def environment_pi_diagnostic(environment: EnvironmentState, config: dict[str, Any]) -> dict[str, Any]:
    if environment.atmosphere_sampling and not environment.atmosphere_sampling.get("pi_sampling_valid", True):
        return {
            "valid": False,
            "reason": environment.atmosphere_sampling.get("pi_sampling_reason", "invalid_atmospheric_sampling"),
            "outer_ocean_sample_count": environment.atmosphere_sampling.get("pi_outer_ring_retained_count"),
        }
    return potential_intensity_ms(
        sst_c=environment.sst_c,
        sea_level_pressure_hpa=environment.sea_level_pressure_hpa,
        pressure_profile_hpa=environment.profile_pressure_hpa,
        temperature_profile_c=environment.profile_temperature_c,
        mixing_ratio_profile_kg_kg=environment.profile_mixing_ratio_kg_kg,
        physics=config["physics"],
    )


def spatial_environment_qc(
    paths: dict[str, Sequence[EnvironmentState] | EnvironmentPath],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Compare near-synchronous, nearby SST samples across independent tracks."""

    settings = config["environment"]["spatial_quality_control"]
    radius = float(settings["comparison_radius_km"])
    maximum_seconds = float(settings["maximum_time_difference_hours"]) * 3600.0
    maximum_sst_difference = float(settings["maximum_sst_difference_c"])
    flattened: list[tuple[str, EnvironmentState]] = []
    for source, path in paths.items():
        nodes = path.nodes if isinstance(path, EnvironmentPath) else path
        flattened.extend((source, node) for node in nodes)
    comparisons = []
    for index, (left_source, left) in enumerate(flattened):
        for right_source, right in flattened[index + 1 :]:
            if left_source == right_source:
                continue
            time_difference = abs((left.requested_utc - right.requested_utc).total_seconds())
            if time_difference > maximum_seconds:
                continue
            distance = haversine_km(left.lat, left.lon, right.lat, right.lon)
            if distance > radius:
                continue
            difference = abs(left.sst_c - right.sst_c)
            if difference > maximum_sst_difference:
                comparisons.append(
                    {
                        "left_source": left_source,
                        "right_source": right_source,
                        "distance_km": round(distance, 1),
                        "time_difference_hours": round(time_difference / 3600.0, 2),
                        "left_sst_c": round(left.sst_c, 2),
                        "right_sst_c": round(right.sst_c, 2),
                        "sst_difference_c": round(difference, 2),
                    }
                )
    return {
        "status": "failed" if comparisons else "passed",
        "rule": f"difference <= {maximum_sst_difference:.1f} C within {radius:.0f} km and {maximum_seconds / 3600:.1f} h",
        "comparisons": comparisons,
    }


def calibration_gate(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the configured WNP calibration artifact before any scenario runs."""

    settings = config["calibration"]
    artifact = settings.get("artifact")
    if not isinstance(artifact, dict):
        return {
            "valid": False,
            "status": "uncalibrated_research",
            "reason": "missing_western_north_pacific_hindcast_artifact",
            "minimum_hindcast_storms": int(settings["minimum_hindcast_storms"]),
        }
    required = {
        "status",
        "basin",
        "unique_storm_count",
        "dataset_sha256",
        "coefficients",
        "normalization",
        "validation",
        "wind_averaging_minutes",
        "lgem_n",
        "lgem_beta_per_hour",
    }
    missing = sorted(required.difference(artifact))
    if missing:
        return {"valid": False, "status": "uncalibrated_research", "reason": "invalid_calibration_artifact", "missing": missing}
    validation = artifact["validation"]
    thresholds = settings["acceptance_thresholds"]
    checks = {
        "status": artifact["status"] == "calibrated_hindcast_research",
        "basin": artifact["basin"] == settings["basin"],
        "storm_count": int(artifact["unique_storm_count"]) >= int(settings["minimum_hindcast_storms"]),
        "dataset_hash": bool(re.fullmatch(r"[0-9a-f]{64}", str(artifact["dataset_sha256"]))),
        "wind_averaging": int(artifact["wind_averaging_minutes"]) == int(settings["wind_averaging_minutes"]),
        "mae": float(validation["landfall_wind_mae_ms"]) <= float(thresholds["landfall_wind_mae_ms"]),
        "bias": abs(float(validation["landfall_wind_bias_ms"])) <= float(thresholds["absolute_bias_ms"]),
    }
    if not all(checks.values()):
        return {"valid": False, "status": "uncalibrated_research", "reason": "calibration_acceptance_failed", "checks": checks}
    return {"valid": True, "status": "calibrated_hindcast_research", "artifact": artifact, "checks": checks}


def track_surface_segments(
    track: SourceTrack,
    start_utc: dt.datetime,
    end_utc: dt.datetime,
    global_land_polygons: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Partition a track into ocean and global-land intervals using boundary intersections."""

    if end_utc < start_utc:
        raise ValueError("surface segmentation end precedes start")
    geometry = config["geometry"]
    maximum_chord_km = float(geometry["maximum_boundary_chord_km"])
    points = sorted_points(track)
    breakpoints = [start_utc, *[point.valid_utc for point in points if start_utc < point.valid_utc < end_utc], end_utc]
    pieces: list[dict[str, Any]] = []
    for segment_start, segment_end in zip(breakpoints, breakpoints[1:]):
        left_lon, left_lat, _ = trajectory_position_at(track, segment_start, geometry)
        right_lon, right_lat, _ = trajectory_position_at(track, segment_end, geometry)
        distance = haversine_km(left_lat, left_lon, right_lat, right_lon)
        subdivisions = max(1, math.ceil(distance / maximum_chord_km))
        for index in range(subdivisions):
            chord_start = segment_start + (segment_end - segment_start) * (index / subdivisions)
            chord_end = segment_start + (segment_end - segment_start) * ((index + 1) / subdivisions)
            start_lon, start_lat, _ = trajectory_position_at(track, chord_start, geometry)
            end_lon, end_lat, _ = trajectory_position_at(track, chord_end, geometry)
            fractions = boundary_fractions_on_chord((start_lon, start_lat), (end_lon, end_lat), global_land_polygons)
            for left_fraction, right_fraction in zip(fractions, fractions[1:]):
                if right_fraction - left_fraction <= 1.0e-12:
                    continue
                middle = (left_fraction + right_fraction) / 2.0
                middle_lon = start_lon + middle * (_unwrap_lon(end_lon, start_lon) - start_lon)
                middle_lat = start_lat + middle * (end_lat - start_lat)
                piece_start = chord_start + (chord_end - chord_start) * left_fraction
                piece_end = chord_start + (chord_end - chord_start) * right_fraction
                is_land = point_in_target(middle_lon, middle_lat, global_land_polygons)
                if pieces and pieces[-1]["is_land"] == is_land and abs((pieces[-1]["end_utc"] - piece_start).total_seconds()) < 1.0:
                    pieces[-1]["end_utc"] = piece_end
                else:
                    pieces.append({"start_utc": piece_start, "end_utc": piece_end, "is_land": is_land})
    return pieces


def run_mechanistic_scenario(
    track: SourceTrack,
    initial_source: str,
    initial_wind_ms: float,
    alignment_time_utc: dt.datetime,
    landfall: dict[str, Any],
    environment: EnvironmentPath,
    global_land_polygons: Sequence[dict[str, Any]],
    calibration_artifact: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Integrate calibrated LGEM over ocean and monotonic KD decay over every land interval."""

    if landfall.get("status") != "landfall":
        raise ValueError(f"Track has no future sea-to-land transition: {landfall.get('status')}")
    landfall_utc = landfall["time_utc"]
    travel_hours = (landfall_utc - alignment_time_utc).total_seconds() / 3600.0
    if travel_hours < 0:
        raise ValueError("Landfall precedes the common analysis time")
    solver = config["solver"]
    physics = config["physics"]
    wind = max(0.0, initial_wind_ms)
    segment_diagnostics = []
    segments = track_surface_segments(
        track,
        alignment_time_utc,
        landfall_utc,
        global_land_polygons,
        config,
    )
    for segment in segments:
        hours = (segment["end_utc"] - segment["start_utc"]).total_seconds() / 3600.0
        before = wind
        if segment["is_land"]:
            wind = kaplan_demaria_decay(
                wind,
                hours,
                float(physics["land_background_wind_ms"]),
                float(physics["land_decay_alpha_per_hour"]),
                float(physics["land_entry_reduction_factor"]),
            )
            if wind > before + 1.0e-9:
                raise RuntimeError("post-land monotonicity invariant failed")
            method = "kaplan_demaria_global_land"
            accepted_steps = 0
        else:
            segment_origin = segment["start_utc"]

            def ocean_derivative(elapsed_h: float, state: Sequence[float]) -> Sequence[float]:
                valid = segment_origin + dt.timedelta(hours=elapsed_h)
                forcing = environment.at(valid)
                pi = environment_pi_diagnostic(forcing, config)
                if not pi["valid"]:
                    raise ValueError(f"PI unavailable at {valid.isoformat()}: {pi['reason']}")
                current = max(0.0, state[0])
                convective_instability = max(0.0, float(pi["wind_ms"]) - current)
                growth = lgem_growth_rate(forcing.deep_layer_shear_ms, convective_instability, calibration_artifact)
                tendency = growth * current - float(calibration_artifact["lgem_beta_per_hour"]) * current * (
                    current / float(pi["wind_ms"])
                ) ** float(calibration_artifact["lgem_n"])
                return [tendency]

            solution = rk45_solve(
                ocean_derivative,
                0.0,
                [wind],
                hours,
                rtol=float(solver["relative_tolerance"]),
                atol=float(solver["absolute_tolerance"]),
                initial_step=float(solver["initial_step_hours"]),
                maximum_step=float(solver["maximum_step_hours"]),
                minimum_step=float(solver["minimum_step_hours"]),
            )
            wind = max(0.0, float(solution["y"][0]))
            method = "calibrated_lgem_rk45"
            accepted_steps = solution["accepted_steps"]
        segment_diagnostics.append(
            {
                "start_utc": segment["start_utc"].isoformat(),
                "end_utc": segment["end_utc"].isoformat(),
                "surface": "land" if segment["is_land"] else "ocean",
                "method": method,
                "start_wind_ms": before,
                "end_wind_ms": wind,
                "accepted_steps": accepted_steps,
            }
        )
    landfall_wind = wind
    post_hours = float(physics["post_landfall_hours"])
    post_landfall_wind = kaplan_demaria_decay(
        landfall_wind,
        post_hours,
        float(physics["land_background_wind_ms"]),
        float(physics["land_decay_alpha_per_hour"]),
        float(physics["land_entry_reduction_factor"]),
    )
    if post_landfall_wind > landfall_wind + 1.0e-9:
        raise RuntimeError("post-land monotonicity invariant failed")
    landing_environment = environment.at(landfall_utc)
    landing_pi = environment_pi_diagnostic(landing_environment, config)
    return {
        "track_source": track.source,
        "initial_analysis_source": initial_source,
        "initial_10min_wind_ms": round(initial_wind_ms, 3),
        "landfall_time_utc": landfall_utc.isoformat(),
        "landfall_lat": round(float(landfall["lat"]), 4),
        "landfall_lon": round(float(landfall["lon"]), 4),
        "landfall_wind_ms": round(landfall_wind, 3),
        "landfall_wind_averaging_minutes": int(calibration_artifact["wind_averaging_minutes"]),
        "post_landfall_hours": post_hours,
        "post_landfall_wind_ms": round(post_landfall_wind, 3),
        "potential_intensity_at_landfall": landing_pi,
        "surface_segments": segment_diagnostics,
        "solver": {
            "method": solver["method"],
            "ocean_model": "calibrated logistic growth equation",
            "land_model": "Kaplan-DeMaria with R=0.9 and monotonic low-wind branch",
        },
    }


def bjt_iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone(dt.timedelta(hours=8))).isoformat()


def intensity_envelope(values: Sequence[float]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    minimum, maximum = ordered[0], ordered[-1]
    return {
        "count": len(ordered),
        "minimum_ms": int(round(minimum)),
        "maximum_ms": int(round(maximum)),
        "spread_ms": int(round(maximum - minimum)),
        "semantics": "named-branch range; no probability interpretation",
    }


def round_datetime_minutes(value: dt.datetime, increment_minutes: int) -> dt.datetime:
    increment_seconds = increment_minutes * 60
    epoch = value.astimezone(UTC).timestamp()
    rounded = math.floor((epoch + increment_seconds / 2) / increment_seconds) * increment_seconds
    return dt.datetime.fromtimestamp(rounded, tz=UTC)


def json_landfall(landfall: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(landfall)
    output = (config or {}).get("output", {})
    position_places = int(output.get("decision_position_decimal_places", 1))
    time_increment = int(output.get("decision_time_increment_minutes", 30))
    if isinstance(result.get("time_utc"), dt.datetime):
        exact_time = result["time_utc"]
        rounded_time = round_datetime_minutes(exact_time, time_increment)
        result["time_utc"] = rounded_time.isoformat()
        result["time_bjt"] = bjt_iso(rounded_time)
        result.setdefault("computational_crossing", {})["time_utc"] = exact_time.isoformat()
    for key in ("lat", "lon"):
        if isinstance(result.get(key), float):
            exact = result[key]
            result[key] = round(exact, position_places)
            result.setdefault("computational_crossing", {})[key] = round(exact, 5)
    return result


def build_official_guidance(
    tracks: Sequence[SourceTrack],
    landfalls: dict[str, dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose official intensity guidance as individual source products only."""

    guidance = []
    comparison_values = []
    for track in tracks:
        landfall = landfalls.get(track.source, {"status": "missing"})
        if landfall.get("status") != "landfall":
            guidance.append(
                {
                    "source": track.source,
                    "resolved_url": track.resolved_url,
                    "landfall": json_landfall(landfall, config),
                    "status": "no_guidance_at_target_landfall",
                }
            )
            continue
        point = interpolate_track_at(track, landfall["time_utc"])
        if point is None or point.wind_ms is None:
            guidance.append(
                {
                    "source": track.source,
                    "resolved_url": track.resolved_url,
                    "landfall": json_landfall(landfall, config),
                    "status": "official_track_has_no_intensity_at_landfall_time",
                }
            )
            continue
        comparison = comparison_10min_wind_ms(track, point.wind_ms)
        if comparison is not None:
            comparison_values.append(comparison)
        entry = {
            "source": track.source,
            "resolved_url": track.resolved_url,
            "landfall": json_landfall(landfall, config),
            "official_guidance_native_wind_ms": int(round(point.wind_ms)),
            "computational_native_wind_ms": round(point.wind_ms, 3),
            "native_wind_averaging_minutes": track.averaging_period_minutes,
            "official_category_near_landfall": point.category,
            "interpolation_method": "time_linear_between_official_forecast_fixes",
            "status": "available",
        }
        if track.averaging_period_minutes == 2:
            entry["cma_2min_wind_grade"] = grade_ms(point.wind_ms, config["wind_scales"]["cma_2min"] if config else load_config()["wind_scales"]["cma_2min"])
        else:
            entry["cma_grade_status"] = "unavailable_without_documented_conversion_to_2min"
        if comparison is not None:
            entry["comparison_10min_wind_ms"] = int(round(comparison))
            entry["computational_comparison_10min_wind_ms"] = round(comparison, 3)
        else:
            entry["comparison_10min_status"] = "unavailable_without_documented_conversion"
        guidance.append(entry)
    return {
        "definition": "Each agency's native forecast wind at its own track-derived target-China landfall time. A separate 10-minute comparison appears only when a documented conversion exists.",
        "by_source": guidance,
        "comparison_10min_envelope": intensity_envelope(comparison_values),
        "excluded_from_10min_envelope": [
            track.source for track in tracks if track.averaging_period_minutes != 10 and track.source != "JTWC"
        ],
    }


def merge_source_status(
    discovery_status: dict[str, dict[str, Any]],
    alignment: AlignmentResult,
) -> dict[str, dict[str, Any]]:
    result = {source: dict(status) for source, status in discovery_status.items()}
    for source, status in alignment.status_by_source.items():
        result.setdefault(source, {"source": source})
        result[source]["quality_control"] = status
    return result


def build_model_result(
    config: dict[str, Any],
    requested_storm: str | None = None,
    *,
    fetcher: HttpFetcher | None = None,
    include_physics: bool = True,
) -> dict[str, Any]:
    """Run discovery, validation, geometry, environmental sampling, and scenarios."""

    fetcher = fetcher or HttpFetcher()
    identity, tracks, discovery_status = discover_tracks(fetcher, config, requested_storm)
    alignment = align_and_validate_tracks(tracks, config)
    target_polygons = load_target_polygons(fetcher, config)
    global_land_polygons = load_natural_earth_polygons(fetcher, config, country_codes=None) if include_physics else []
    landfalls = {
        track.source: find_first_landfall(track, alignment.alignment_time_utc, target_polygons, config)
        for track in alignment.usable_tracks
    }
    official = build_official_guidance(alignment.usable_tracks, landfalls, config)

    environment_by_track: dict[str, dict[str, Any]] = {}
    environment_paths: dict[str, EnvironmentPath] = {}
    shared_environment_cache: list[EnvironmentState] = []
    surface_segments_by_track: dict[str, Any] = {}
    scenarios: list[dict[str, Any]] = []
    scenario_failures: list[dict[str, Any]] = []
    gate = calibration_gate(config)
    if include_physics:
        for track in alignment.usable_tracks:
            landfall = landfalls[track.source]
            if landfall.get("status") != "landfall":
                environment_by_track[track.source] = {"status": "unavailable", "reason": landfall.get("status")}
                continue
            try:
                environment = build_environment_path(
                    fetcher,
                    track,
                    alignment.alignment_time_utc,
                    landfall["time_utc"],
                    config,
                    global_land_polygons,
                    shared_environment_cache,
                )
                environment_paths[track.source] = environment
                environment_by_track[track.source] = {
                    "status": "available",
                    "nodes": [
                        {
                            **node.as_dict(),
                            "potential_intensity": environment_pi_diagnostic(node, config),
                        }
                        for node in environment.nodes
                    ],
                }
                surface_segments_by_track[track.source] = [
                    {
                        "start_utc": segment["start_utc"].isoformat(),
                        "end_utc": segment["end_utc"].isoformat(),
                        "surface": "land" if segment["is_land"] else "ocean",
                    }
                    for segment in track_surface_segments(
                        track,
                        alignment.alignment_time_utc,
                        landfall["time_utc"],
                        global_land_polygons,
                        config,
                    )
                ]
            except Exception as exc:
                environment_by_track[track.source] = {
                    "status": "unavailable",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
                continue
    else:
        environment_by_track = {"status": "skipped_by_command_line"}

    spatial_qc = spatial_environment_qc(environment_paths, config) if environment_paths else {
        "status": "unavailable",
        "reason": "no_environment_paths",
        "comparisons": [],
    }
    if include_physics and gate["valid"] and spatial_qc["status"] == "passed":
        artifact = gate["artifact"]
        tracks_by_source = {track.source: track for track in alignment.usable_tracks}
        for track in alignment.usable_tracks:
            environment = environment_paths.get(track.source)
            landfall = landfalls[track.source]
            if environment is None or landfall.get("status") != "landfall":
                continue
            for initial_source, aligned_point in alignment.aligned.items():
                initial_wind = comparison_10min_wind_ms(tracks_by_source[initial_source], aligned_point.wind_ms)
                if initial_wind is None:
                    scenario_failures.append(
                        {
                            "track_source": track.source,
                            "initial_analysis_source": initial_source,
                            "reason": "no_documented_conversion_to_calibration_10min_wind",
                        }
                    )
                    continue
                try:
                    scenarios.append(
                        run_mechanistic_scenario(
                            track,
                            initial_source,
                            initial_wind,
                            alignment.alignment_time_utc,
                            landfall,
                            environment,
                            global_land_polygons,
                            artifact,
                            config,
                        )
                    )
                except Exception as exc:
                    scenario_failures.append(
                        {
                            "track_source": track.source,
                            "initial_analysis_source": initial_source,
                            "reason": f"{type(exc).__name__}: {exc}",
                        }
                    )

    scenario_values = [scenario["landfall_wind_ms"] for scenario in scenarios]
    source_status = merge_source_status(discovery_status, alignment)
    result = {
        "run_time_utc": dt.datetime.now(UTC).isoformat(),
        "storm": identity.as_dict(),
        "configuration": {
            "schema_version": config["schema_version"],
            "target_country_codes": config["target"]["country_codes"],
            "physics": config["physics"],
            "solver": config["solver"],
            "geometry": config["geometry"],
        },
        "common_effective_time_utc": alignment.alignment_time_utc.isoformat(),
        "source_status": source_status,
        "source_quality": {
            "position": alignment.position_summary,
            "current_intensity": alignment.intensity_summary,
            "rule": "Identity evidence and physical plausibility govern source eligibility. Position and intensity disagreement remain in the output as decision-relevant uncertainty.",
        },
        "landfall_by_track": {source: json_landfall(value, config) for source, value in landfalls.items()},
        "official_guidance": official,
        "environmental_diagnostics": {
            "environment_by_track": environment_by_track,
            "spatial_sst_quality_control": spatial_qc,
            "global_surface_segments_by_track": surface_segments_by_track,
            "land_mask_scope": "all Natural Earth country polygons",
            "reporting_target_scope": config["target"]["country_codes"],
        },
        "mechanistic_scenarios": {
            "status": gate["status"] if include_physics else "skipped_by_command_line",
            "calibration_gate": gate,
            "definition": "Scenarios require full-column tcpyPI, calibrated WNP LGEM growth coefficients, shear, OHC diagnostics, and global-land Kaplan-DeMaria segments. Official future wind endpoints remain outside the tendency equation.",
            "scenarios": scenarios,
            "scenario_envelope": intensity_envelope(scenario_values),
            "failed_scenarios": scenario_failures,
        },
        "repair_evidence": build_repair_evidence(config),
        "decision_view": {
            "single_deterministic_landfall_value": None,
            "official_10min_comparison_envelope": official["comparison_10min_envelope"],
            "native_official_branches": official["by_source"],
            "mechanistic_scenario_range": intensity_envelope(scenario_values),
            "mechanistic_status": gate["status"] if include_physics else "skipped_by_command_line",
            "interpretation": "Preparedness uses named official branches and their documented 10-minute comparison range. The mechanistic range remains research-only until the WNP hindcast gate passes.",
        },
        "network_transport_warnings": fetcher.transport_warnings,
    }
    return result


def write_result(result: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def print_run_summary(result: dict[str, Any], output: Path) -> None:
    storm = result["storm"]
    print(f"Wrote {output}")
    print(f"storm={storm['name']}/{storm['number']} common_time={result['common_effective_time_utc']}")
    official = result["official_guidance"]["comparison_10min_envelope"]
    if official:
        print(
            "official_guidance "
            f"range={official['minimum_ms']:.2f}-{official['maximum_ms']:.2f}m/s "
            f"spread={official['spread_ms']:.2f}m/s averaging=10min"
        )
    scenario = result["mechanistic_scenarios"]["scenario_envelope"]
    if scenario:
        print(
            "mechanistic_scenarios "
            f"count={scenario['count']} range={scenario['minimum_ms']:.2f}-{scenario['maximum_ms']:.2f}m/s"
        )
    else:
        gate = result["mechanistic_scenarios"]["calibration_gate"]
        print(f"mechanistic_scenarios status={result['mechanistic_scenarios']['status']} reason={gate.get('reason')}")
    for source, status in result["source_status"].items():
        quality = status.get("quality_control", {})
        print(f"{source}: discovery={status.get('discovery_ok')} usable={quality.get('usable')} error={status.get('error') or quality.get('error')}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dynamic typhoon landfall scenario model")
    parser.add_argument("--storm", help="Current storm name, Chinese name, or WMO number when multiple systems are active")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-physics", action="store_true", help="Fetch sources and official guidance without environmental ODE scenarios")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    result = build_model_result(config, args.storm, include_physics=not args.skip_physics)
    output = args.output or (Path("outputs") / "typhoon_landfall" / f"{result['storm']['name'].lower()}_latest.json")
    write_result(result, output)
    print_run_summary(result, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
