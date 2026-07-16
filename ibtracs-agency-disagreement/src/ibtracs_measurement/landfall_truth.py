from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


EARTH_RADIUS_KM = 6371.0088
SEARCH_RADIUS_KM = 250.0
SEARCH_WINDOW_HOURS = 12.0
SCORE_WINDOW_HOURS = 6.0
BOOTSTRAP_SEED = 20260715

TRUTH_COLUMNS = (
    "SID",
    "NAME",
    "crossing_time_utc",
    "crossing_lat",
    "crossing_lon",
    "source_id",
    "source_name",
    "station_id",
    "station_name",
    "station_lat",
    "station_lon",
    "station_elevation_m",
    "distance_to_landfall_km",
    "observation_time_utc",
    "observation_period_start_utc",
    "observation_period_end_utc",
    "time_offset_hours",
    "time_precision",
    "observed_wind_native",
    "native_unit",
    "observed_wind_ms",
    "wind_kind",
    "averaging_window_minutes",
    "comparable_10min_ms",
    "conversion_method",
    "report_type",
    "quality_code",
    "quality_passed",
    "grade",
    "scoreable",
    "grade_reason",
    "max_wind_region_evidence",
    "max_wind_region_evidence_url",
    "independent_measurement",
    "business_information_overlap",
    "source_url",
    "retrieved_at_utc",
    "raw_sha256",
    "evidence_label",
)


@dataclass(frozen=True)
class Event:
    sid: str
    name: str
    crossing_time: pd.Timestamp
    latitude: float
    longitude: float


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def haversine_km(
    latitude_a: float | np.ndarray,
    longitude_a: float | np.ndarray,
    latitude_b: float | np.ndarray,
    longitude_b: float | np.ndarray,
) -> np.ndarray:
    lat_a = np.radians(np.asarray(latitude_a, dtype=float))
    lon_a = np.radians(np.asarray(longitude_a, dtype=float))
    lat_b = np.radians(np.asarray(latitude_b, dtype=float))
    lon_b = np.radians(np.asarray(longitude_b, dtype=float))
    dlat = lat_b - lat_a
    dlon = lon_b - lon_a
    value = np.sin(dlat / 2.0) ** 2 + np.cos(lat_a) * np.cos(lat_b) * np.sin(
        dlon / 2.0
    ) ** 2
    value = np.clip(value, 0.0, 1.0)
    return EARTH_RADIUS_KM * 2.0 * np.arctan2(np.sqrt(value), np.sqrt(1.0 - value))


def load_events(path: Path) -> tuple[pd.DataFrame, list[Event]]:
    frame = pd.read_csv(path)
    required = {
        "SID",
        "NAME",
        "crossing_time",
        "crossing_lat",
        "crossing_lon",
        "JTWC",
        "JMA",
        "CMA",
        "HKO",
        "KMA",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Frozen landfall file is missing columns: {sorted(missing)}")
    if len(frame) != 108 or frame["SID"].duplicated().any():
        raise ValueError("Frozen external-truth universe must contain 108 unique SIDs")
    frame["crossing_time"] = pd.to_datetime(frame["crossing_time"], utc=True)
    frame = frame.sort_values("SID").reset_index(drop=True)
    events = [
        Event(
            sid=str(row.SID),
            name=str(row.NAME),
            crossing_time=pd.Timestamp(row.crossing_time),
            latitude=float(row.crossing_lat),
            longitude=float(row.crossing_lon),
        )
        for row in frame.itertuples(index=False)
    ]
    return frame, events


def base_record(event: Event) -> dict[str, Any]:
    return {
        "SID": event.sid,
        "NAME": event.name,
        "crossing_time_utc": event.crossing_time.isoformat(),
        "crossing_lat": event.latitude,
        "crossing_lon": event.longitude,
        "max_wind_region_evidence": "none",
        "max_wind_region_evidence_url": "",
        "scoreable": False,
        "grade": "B",
        "independent_measurement": True,
        "comparable_10min_ms": np.nan,
        "conversion_method": "none",
        "evidence_label": "[验证过]",
    }


def finalize_records(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    for column in TRUTH_COLUMNS:
        if column not in frame:
            frame[column] = np.nan
    frame = frame.loc[:, TRUTH_COLUMNS]
    if frame.empty:
        return frame
    frame["scoreable"] = frame["scoreable"].astype(bool)
    frame["independent_measurement"] = frame["independent_measurement"].astype(bool)
    frame["quality_passed"] = frame["quality_passed"].astype(bool)
    frame = frame.sort_values(
        ["SID", "grade", "source_id", "station_id", "wind_kind"],
        kind="stable",
    ).reset_index(drop=True)
    return frame


def parse_isd_wnd(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str):
        return None
    fields = value.split(",")
    if len(fields) != 5:
        return None
    direction, direction_qc, wind_type, speed_text, speed_qc = fields
    try:
        speed_tenths = int(speed_text)
    except ValueError:
        return None
    if speed_tenths == 9999:
        return None
    averaging = {"H": 5.0, "R": 60.0, "T": 180.0}.get(wind_type)
    passed = speed_qc in {"0", "1", "4", "5", "9"}
    return {
        "direction_degrees": None if direction == "999" else int(direction),
        "direction_quality": direction_qc,
        "wind_type": wind_type,
        "speed_ms": speed_tenths / 10.0,
        "speed_quality": speed_qc,
        "quality_passed": passed,
        "averaging_window_minutes": averaging,
    }


def select_isd_event_records(
    event: Event,
    station: pd.Series,
    observations: pd.DataFrame,
    *,
    source_url: str,
    retrieved_at: str,
    raw_sha256: str,
) -> list[dict[str, Any]]:
    if observations.empty or "DATE" not in observations or "WND" not in observations:
        return []
    times = pd.to_datetime(observations["DATE"], utc=True, errors="coerce")
    offset = (times - event.crossing_time).dt.total_seconds() / 3600.0
    subset = observations.loc[offset.abs() <= SEARCH_WINDOW_HOURS].copy()
    if subset.empty:
        return []
    subset["_time"] = times.loc[subset.index]
    subset["_offset"] = offset.loc[subset.index]
    parsed = subset["WND"].map(parse_isd_wnd)
    subset = subset.loc[parsed.notna()].copy()
    if subset.empty:
        return []
    parsed = parsed.loc[subset.index]
    subset["_speed"] = parsed.map(lambda item: item["speed_ms"])
    subset["_passed"] = parsed.map(lambda item: item["quality_passed"])
    subset = subset.loc[subset["_passed"]].copy()
    if subset.empty:
        return []
    index = subset["_speed"].idxmax()
    row = subset.loc[index]
    wind = parse_isd_wnd(row["WND"])
    if wind is None:
        return []
    latitude = float(station["LAT"])
    longitude = float(station["LON"])
    record = base_record(event)
    record.update(
        {
            "source_id": "NOAA_ISD",
            "source_name": "NOAA/NCEI Global Hourly (ISD/GTS)",
            "station_id": f"{station['USAF']}{station['WBAN']}",
            "station_name": station.get("STATION NAME", ""),
            "station_lat": latitude,
            "station_lon": longitude,
            "station_elevation_m": station.get("ELEV(M)", np.nan),
            "distance_to_landfall_km": float(
                haversine_km(event.latitude, event.longitude, latitude, longitude)
            ),
            "observation_time_utc": row["_time"].isoformat(),
            "observation_period_start_utc": "",
            "observation_period_end_utc": "",
            "time_offset_hours": float(row["_offset"]),
            "time_precision": "report_valid_time",
            "observed_wind_native": wind["speed_ms"],
            "native_unit": "m/s",
            "observed_wind_ms": wind["speed_ms"],
            "wind_kind": "sustained_or_report_mean",
            "averaging_window_minutes": wind["averaging_window_minutes"],
            "report_type": row.get("REPORT_TYPE", ""),
            "quality_code": wind["speed_quality"],
            "quality_passed": True,
            "grade_reason": (
                "ISD WND is an instrument/GTS report, but type N does not encode a universal "
                "averaging window and the station is not proven to sample the cyclone maximum-wind region"
            ),
            "business_information_overlap": (
                "GTS/SYNOP or aviation reports may enter one or more agency operational analyses"
            ),
            "source_url": source_url,
            "retrieved_at_utc": retrieved_at,
            "raw_sha256": raw_sha256,
        }
    )
    return [record]


def cwa_station_type(station_attribute: str, station_id: str) -> str:
    if station_attribute != "auto":
        return station_attribute
    prefix = station_id[:2]
    if prefix in {"C0", "CA"}:
        return "auto_C0"
    if prefix == "C1":
        return "auto_C1"
    return "auto_unknown"


def _nested_value(item: dict[str, Any], *path: str) -> Any:
    current: Any = item
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def select_cwa_station_records(
    event: Event,
    station: dict[str, Any],
    dts: Sequence[dict[str, Any]],
    *,
    station_type: str,
    source_url: str,
    retrieved_at: str,
    raw_sha256: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    local_tz = timezone(timedelta(hours=8))
    prepared: list[tuple[dict[str, Any], pd.Timestamp, float]] = []
    for item in dts:
        time_text = item.get("DataTime")
        if not time_text:
            continue
        local_time = pd.Timestamp(time_text).tz_localize(local_tz)
        utc_time = local_time.tz_convert("UTC")
        offset = (utc_time - event.crossing_time).total_seconds() / 3600.0
        if abs(offset) <= SEARCH_WINDOW_HOURS + 1.0:
            prepared.append((item, utc_time, offset))
    if not prepared:
        return []

    candidates = (
        (
            "ten_minute_sustained_maximum",
            ("WindSpeed", "TenMinutelyMaximum"),
            ("WindSpeed", "TenMinutelyMaximumf"),
            10.0,
        ),
        ("reported_mean", ("WindSpeed", "Mean"), ("WindSpeed", "Meanf"), np.nan),
        ("peak_gust", ("PeakGust", "Maximum"), ("PeakGust", "Maximumf"), 0.0),
    )
    latitude = float(station["latitude"])
    longitude = float(station["longitude"])
    for wind_kind, value_path, flag_path, window in candidates:
        valid: list[tuple[float, Any, dict[str, Any], pd.Timestamp, float]] = []
        for item, utc_time, offset in prepared:
            value = _nested_value(item, *value_path)
            flag = _nested_value(item, *flag_path)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(numeric) or numeric < 0:
                continue
            quality_passed = flag in (None, 0, "0", "")
            if quality_passed:
                valid.append((numeric, flag, item, utc_time, offset))
        if not valid:
            continue
        value, flag, _, utc_time, offset = max(valid, key=lambda item: item[0])
        record = base_record(event)
        comparable = value if wind_kind == "ten_minute_sustained_maximum" else np.nan
        reason = {
            "ten_minute_sustained_maximum": (
                "Native 10-minute instrument maximum is aligned in time window, while the station's "
                "representation of the cyclone maximum-wind region is unproven"
            ),
            "reported_mean": (
                "Instrument mean wind has an ambiguous archive-window interpretation and no maximum-wind-region evidence"
            ),
            "peak_gust": "Instrument gust is a different observation operator from sustained Vmax",
        }[wind_kind]
        record.update(
            {
                "source_id": "CWA_CODIS",
                "source_name": "Taiwan CWA CODiS station archive",
                "station_id": station["stationID"],
                "station_name": station["stationName"],
                "station_lat": latitude,
                "station_lon": longitude,
                "station_elevation_m": station.get("altitude", np.nan),
                "distance_to_landfall_km": float(
                    haversine_km(event.latitude, event.longitude, latitude, longitude)
                ),
                "observation_time_utc": utc_time.isoformat(),
                "observation_period_start_utc": (utc_time - pd.Timedelta(1, unit="h")).isoformat(),
                "observation_period_end_utc": utc_time.isoformat(),
                "time_offset_hours": float(offset),
                "time_precision": "hour_bin_end; peak minute unavailable",
                "observed_wind_native": value,
                "native_unit": "m/s",
                "observed_wind_ms": value,
                "wind_kind": wind_kind,
                "averaging_window_minutes": window,
                "comparable_10min_ms": comparable,
                "conversion_method": "native_10min" if np.isfinite(comparable) else "none",
                "report_type": station_type,
                "quality_code": "null=pass" if flag is None else str(flag),
                "quality_passed": True,
                "grade_reason": reason,
                "business_information_overlap": (
                    "CWA station observations can enter CWA and exchanged operational analyses; CMA home-region overlap is possible"
                ),
                "source_url": source_url,
                "retrieved_at_utc": retrieved_at,
                "raw_sha256": raw_sha256,
            }
        )
        rows.append(record)
    return rows


JMA_STATION_PATTERN = re.compile(
    r'class="station(?:\s+stmark)?[^\"]*"\s+title="(?P<title>.*?)"[^>]*>'
    r'.*?name="stid"\s+value="(?P<station_id>[^"]+)"'
    r'.*?name="stname"\s+value="(?P<station_name>[^"]+)"'
    r'.*?name="prid"\s+value="(?P<prefecture_id>[^"]+)"'
    r'.*?name="kansoku"\s+value="(?P<elements>[^"]+)"',
    flags=re.DOTALL,
)


def _jma_coordinate(title: str, label: str) -> float | None:
    match = re.search(rf"{label}：(\d+)度([0-9.]+)分", title)
    if not match:
        return None
    return float(match.group(1)) + float(match.group(2)) / 60.0


def parse_jma_station_html(html: str) -> list[dict[str, Any]]:
    stations: dict[str, dict[str, Any]] = {}
    for match in JMA_STATION_PATTERN.finditer(html):
        station_id = match.group("station_id")
        elements = match.group("elements")
        latitude = _jma_coordinate(match.group("title"), "北緯")
        longitude = _jma_coordinate(match.group("title"), "東経")
        elevation_match = re.search(r"標高：([0-9.]+)m", match.group("title"))
        if latitude is None or longitude is None:
            continue
        stations[station_id] = {
            "station_id": station_id,
            "station_name": match.group("station_name"),
            "prefecture_id": match.group("prefecture_id"),
            "elements": elements,
            "wind_available": len(elements) > 1 and elements[1] in {"1", "2"},
            "latitude": latitude,
            "longitude": longitude,
            "elevation_m": (
                float(elevation_match.group(1)) if elevation_match else np.nan
            ),
        }
    return list(stations.values())


def parse_jma_hourly_csv(
    payload: bytes,
    requested_stations: Sequence[dict[str, Any]],
) -> pd.DataFrame:
    text = payload.decode("cp932", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 7:
        return pd.DataFrame()
    station_header = rows[2]
    data_rows = rows[5:]
    expected_columns = 1 + 5 * len(requested_stations)
    if len(station_header) < expected_columns:
        return pd.DataFrame()
    parsed: list[dict[str, Any]] = []
    for station_index, station in enumerate(requested_stations):
        start = 1 + station_index * 5
        header_name = station_header[start].strip()
        if header_name and header_name != station["station_name"]:
            raise ValueError(
                f"JMA station response order changed: expected {station['station_name']}, got {header_name}"
            )
        for row in data_rows:
            if len(row) < start + 5 or not row[0].strip():
                continue
            try:
                wind = float(row[start])
                quality = int(float(row[start + 1]))
            except (TypeError, ValueError):
                continue
            parsed.append(
                {
                    "station_id": station["station_id"],
                    "station_name": station["station_name"],
                    "local_time": row[0].strip(),
                    "wind_ms": wind,
                    "quality": quality,
                    "direction": row[start + 2].strip(),
                }
            )
    return pd.DataFrame(parsed)


def select_jma_station_records(
    event: Event,
    station: dict[str, Any],
    observations: pd.DataFrame,
    *,
    source_url: str,
    retrieved_at: str,
    raw_sha256: str,
) -> list[dict[str, Any]]:
    subset = observations.loc[observations["station_id"] == station["station_id"]].copy()
    if subset.empty:
        return []
    local_tz = timezone(timedelta(hours=9))
    subset["_time"] = pd.to_datetime(subset["local_time"], errors="coerce").dt.tz_localize(
        local_tz
    ).dt.tz_convert("UTC")
    subset["_offset"] = (
        subset["_time"] - event.crossing_time
    ).dt.total_seconds() / 3600.0
    subset = subset.loc[
        subset["_offset"].abs().le(SEARCH_WINDOW_HOURS)
        & subset["quality"].eq(8)
        & subset["wind_ms"].ge(0)
    ]
    if subset.empty:
        return []
    row = subset.loc[subset["wind_ms"].idxmax()]
    latitude = float(station["latitude"])
    longitude = float(station["longitude"])
    record = base_record(event)
    record.update(
        {
            "source_id": "JMA_AMEDAS",
            "source_name": "JMA historical AMeDAS/surface observations",
            "station_id": station["station_id"],
            "station_name": station["station_name"],
            "station_lat": latitude,
            "station_lon": longitude,
            "station_elevation_m": station.get("elevation_m", np.nan),
            "distance_to_landfall_km": float(
                haversine_km(event.latitude, event.longitude, latitude, longitude)
            ),
            "observation_time_utc": row["_time"].isoformat(),
            "observation_period_start_utc": (row["_time"] - pd.Timedelta(10, unit="min")).isoformat(),
            "observation_period_end_utc": row["_time"].isoformat(),
            "time_offset_hours": float(row["_offset"]),
            "time_precision": "hourly sample of preceding 10-minute mean",
            "observed_wind_native": float(row["wind_ms"]),
            "native_unit": "m/s",
            "observed_wind_ms": float(row["wind_ms"]),
            "wind_kind": "10_minute_mean_hourly_sample",
            "averaging_window_minutes": 10.0,
            "comparable_10min_ms": float(row["wind_ms"]),
            "conversion_method": "native_10min",
            "report_type": "JMA hourly historical download",
            "quality_code": "8",
            "quality_passed": True,
            "grade_reason": (
                "Native 10-minute mean is sampled only on the hour and the station is not proven to be in the cyclone maximum-wind region"
            ),
            "business_information_overlap": "JMA observations can enter JMA and exchanged operational analyses",
            "source_url": source_url,
            "retrieved_at_utc": retrieved_at,
            "raw_sha256": raw_sha256,
        }
    )
    return [record]


def parse_dms(value: Any) -> float | None:
    match = re.search(r"(\d+)°(\d+)'(\d+)\"", str(value))
    if not match:
        return None
    degree, minute, second = map(float, match.groups())
    return degree + minute / 60.0 + second / 3600.0


def hko_station_metadata(tables: Sequence[pd.DataFrame]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for table in tables:
        if not isinstance(table.columns, pd.MultiIndex) or table.shape[1] < 3:
            continue
        first = table.columns[0]
        lat_col = next((col for col in table.columns if col[-1] == "Latitude N"), None)
        lon_col = next((col for col in table.columns if col[-1] == "Longitude E"), None)
        if lat_col is None or lon_col is None:
            continue
        for _, row in table.iterrows():
            label = str(row[first])
            latitude = parse_dms(row[lat_col])
            longitude = parse_dms(row[lon_col])
            if latitude is None or longitude is None:
                continue
            name = re.split(r"\s+\([A-Z0-9]+\)\s+\(", label, maxsplit=1)[0]
            code_match = re.search(r"\(([A-Z0-9]+)\)\s+\(", label)
            item = {
                "station_name": name.strip(),
                "station_id": code_match.group(1) if code_match else name.strip(),
                "latitude": latitude,
                "longitude": longitude,
            }
            aliases = {
                name.strip(),
                name.replace("New ", "").strip(),
                name.replace("Hong Kong International Airport", "Chek Lap Kok").strip(),
            }
            for alias in aliases:
                result[alias.casefold()] = item
    return result


def match_hko_station(
    name: str, metadata: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    normalized = name.replace("\n", " ").strip()
    aliases = {
        "central pier/central (before 20-12-2005)": "central pier",
        "star ferry  (kowloon)": "star ferry(kowloon)",
        "tap mun east /tap mun (before 06-07-2017)": "tap mun east",
        "tsing yi": "new tsing yi station",
        "chek lap kok": "hong kong international airport",
    }
    key = aliases.get(normalized.casefold(), normalized.casefold())
    if key in metadata:
        return metadata[key]
    for candidate, item in metadata.items():
        if key in candidate or candidate in key:
            return item
    return None


def hko_impact_records(
    events: Sequence[Event],
    workbook: Path,
    station_tables: Sequence[pd.DataFrame],
    *,
    source_url: str,
    retrieved_at: str,
    raw_sha256: str,
) -> list[dict[str, Any]]:
    signal = pd.read_excel(workbook, sheet_name="Signal Data", header=None)
    wind = pd.read_excel(workbook, sheet_name="Wind", header=None)
    metadata = hko_station_metadata(station_tables)
    signal_rows = signal.iloc[5:].copy()
    wind_rows = wind.iloc[4:].copy()
    records: list[dict[str, Any]] = []

    for event in events:
        year = event.crossing_time.year
        names = {event.name.casefold(), event.name.replace("-", " ").casefold()}
        signal_match = signal_rows.loc[
            pd.to_numeric(signal_rows[0], errors="coerce").eq(year)
            & signal_rows[1].astype(str).str.replace("-", " ").str.casefold().isin(names)
        ]
        wind_match = wind_rows.loc[
            pd.to_numeric(wind_rows[2], errors="coerce").eq(year)
            & wind_rows[3].astype(str).str.replace("-", " ").str.casefold().isin(names)
        ]
        if signal_match.empty or wind_match.empty:
            continue
        signal_row = signal_match.iloc[0]
        start = pd.Timestamp(signal_row[6], tz="Asia/Hong_Kong")
        end = pd.Timestamp(signal_row[7], tz="Asia/Hong_Kong") + pd.Timedelta(1, unit="D")
        crossing_local = event.crossing_time.tz_convert("Asia/Hong_Kong")
        if (
            crossing_local < start - pd.Timedelta(1, unit="D")
            or crossing_local > end + pd.Timedelta(1, unit="D")
        ):
            continue
        wind_row = wind_match.iloc[0]
        for column in range(7, wind.shape[1], 2):
            raw_station_name = str(wind.iloc[1, column]).strip()
            station = match_hko_station(raw_station_name, metadata)
            if station is None:
                continue
            station_name = re.sub(r"\s+", " ", raw_station_name).strip()
            for value_column, wind_kind, window in (
                (column, "peak_gust", 0.0),
                (column + 1, "60_minute_mean_passage_maximum", 60.0),
            ):
                if value_column >= wind.shape[1]:
                    continue
                value = pd.to_numeric(wind_row[value_column], errors="coerce")
                if pd.isna(value):
                    continue
                speed_ms = float(value) / 3.6
                record = base_record(event)
                record.update(
                    {
                        "source_id": "HKO_TC_IMPACT",
                        "source_name": "HKO Tropical Cyclone Impact Dataset",
                        "station_id": station["station_id"],
                        "station_name": station_name,
                        "station_lat": station["latitude"],
                        "station_lon": station["longitude"],
                        "station_elevation_m": np.nan,
                        "distance_to_landfall_km": float(
                            haversine_km(
                                event.latitude,
                                event.longitude,
                                station["latitude"],
                                station["longitude"],
                            )
                        ),
                        "observation_time_utc": "",
                        "observation_period_start_utc": start.tz_convert("UTC").isoformat(),
                        "observation_period_end_utc": end.tz_convert("UTC").isoformat(),
                        "time_offset_hours": np.nan,
                        "time_precision": "maximum during HKO warning passage; exact time absent in workbook",
                        "observed_wind_native": float(value),
                        "native_unit": "km/h",
                        "observed_wind_ms": speed_ms,
                        "wind_kind": wind_kind,
                        "averaging_window_minutes": window,
                        "report_type": "official consolidated passage maximum",
                        "quality_code": "published",
                        "quality_passed": True,
                        "grade_reason": (
                            "Official station measurement has a gust or 60-minute window and lacks an exact observation time and maximum-wind-region proof"
                        ),
                        "business_information_overlap": "HKO station observations can enter HKO and exchanged operational analyses",
                        "source_url": source_url,
                        "retrieved_at_utc": retrieved_at,
                        "raw_sha256": raw_sha256,
                    }
                )
                records.append(record)
    return records


def event_truth_from_grade_a(truth: pd.DataFrame) -> pd.DataFrame:
    grade_a = truth.loc[
        truth["grade"].eq("A")
        & truth["scoreable"]
        & pd.to_numeric(truth["comparable_10min_ms"], errors="coerce").notna()
    ].copy()
    if grade_a.empty:
        return pd.DataFrame(
            columns=[
                "SID",
                "truth_10min_ms",
                "truth_source_id",
                "truth_station_id",
                "truth_observation_time_utc",
            ]
        )
    grade_a["comparable_10min_ms"] = pd.to_numeric(
        grade_a["comparable_10min_ms"], errors="coerce"
    )
    indices = grade_a.groupby("SID")["comparable_10min_ms"].idxmax()
    selected = grade_a.loc[indices].copy()
    return selected.rename(
        columns={
            "comparable_10min_ms": "truth_10min_ms",
            "source_id": "truth_source_id",
            "station_id": "truth_station_id",
            "observation_time_utc": "truth_observation_time_utc",
        }
    )[
        [
            "SID",
            "truth_10min_ms",
            "truth_source_id",
            "truth_station_id",
            "truth_observation_time_utc",
        ]
    ]


def error_metrics(truth: np.ndarray, estimate: np.ndarray) -> dict[str, float]:
    error = np.asarray(estimate, dtype=float) - np.asarray(truth, dtype=float)
    return {
        "bias_ms": float(np.mean(error)),
        "mae_ms": float(np.mean(np.abs(error))),
        "rmse_ms": float(np.sqrt(np.mean(error**2))),
        "error_sd_ms": float(np.std(error, ddof=1)) if len(error) > 1 else np.nan,
    }


def bootstrap_error_metrics(
    truth: np.ndarray,
    estimate: np.ndarray,
    *,
    replicates: int = 2000,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, list[float]]:
    truth = np.asarray(truth, dtype=float)
    estimate = np.asarray(estimate, dtype=float)
    if len(truth) == 0:
        return {key: [np.nan, np.nan] for key in ("bias_ms", "mae_ms", "rmse_ms", "error_sd_ms")}
    rng = np.random.default_rng(seed)
    values = {key: [] for key in ("bias_ms", "mae_ms", "rmse_ms", "error_sd_ms")}
    for _ in range(replicates):
        index = rng.integers(0, len(truth), len(truth))
        metrics = error_metrics(truth[index], estimate[index])
        for key, value in metrics.items():
            values[key].append(value)
    result: dict[str, list[float]] = {}
    for key, samples in values.items():
        finite = np.asarray(samples, dtype=float)
        finite = finite[np.isfinite(finite)]
        result[key] = (
            [float(x) for x in np.percentile(finite, [2.5, 97.5])]
            if len(finite)
            else [np.nan, np.nan]
        )
    return result


def bootstrap_error_correlations(
    error_rows: pd.DataFrame,
    agencies: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = BOOTSTRAP_SEED,
) -> pd.DataFrame:
    """Bootstrap agency-error correlations with the typhoon as the block."""
    columns = [f"{agency}_error_ms" for agency in agencies]
    missing = [column for column in ("SID", *columns) if column not in error_rows]
    if missing:
        raise ValueError(f"error rows lack required columns: {missing}")
    if error_rows["SID"].duplicated().any():
        raise ValueError("agency error rows must contain one row per typhoon")

    values = error_rows[columns].to_numpy(float)
    point = (
        np.corrcoef(values, rowvar=False)
        if len(values) >= 3
        else np.full((len(agencies), len(agencies)), np.nan)
    )
    samples = np.full((replicates, len(agencies), len(agencies)), np.nan)
    if len(values) >= 3:
        rng = np.random.default_rng(seed)
        for replicate in range(replicates):
            index = rng.integers(0, len(values), len(values))
            sampled = values[index]
            if np.unique(sampled, axis=0).shape[0] < 2:
                continue
            with np.errstate(divide="ignore", invalid="ignore"):
                samples[replicate] = np.corrcoef(sampled, rowvar=False)

    rows: list[dict[str, Any]] = []
    for row_index, row_agency in enumerate(agencies):
        for column_index, column_agency in enumerate(agencies):
            finite = samples[:, row_index, column_index]
            finite = finite[np.isfinite(finite)]
            if len(finite):
                low, high = np.percentile(finite, [2.5, 97.5])
            else:
                low, high = np.nan, np.nan
            rows.append(
                {
                    "agency_i": row_agency,
                    "agency_j": column_agency,
                    "correlation": float(point[row_index, column_index]),
                    "correlation_95ci_low": float(low),
                    "correlation_95ci_high": float(high),
                    "valid_bootstrap_replicates": int(len(finite)),
                    "requested_bootstrap_replicates": int(replicates),
                    "cluster_unit": "SID",
                    "matched_grade_a_events": int(len(values)),
                }
            )
    return pd.DataFrame(rows)


def score_agencies(
    landfalls: pd.DataFrame,
    truth: pd.DataFrame,
    agencies: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    event_truth = event_truth_from_grade_a(truth)
    rows = landfalls.merge(event_truth, on="SID", how="inner")
    for agency in agencies:
        if not rows.empty:
            rows[f"{agency}_error_ms"] = rows[agency] - rows["truth_10min_ms"]
    summaries: list[dict[str, Any]] = []
    for offset, agency in enumerate(agencies):
        if rows.empty:
            summaries.append(
                {
                    "agency": agency,
                    "landfall_events": len(landfalls),
                    "matched_independent_truth_events": 0,
                    "truth_coverage_fraction": 0.0,
                    "bias_ms": np.nan,
                    "bias_95ci_low": np.nan,
                    "bias_95ci_high": np.nan,
                    "mae_ms": np.nan,
                    "mae_95ci_low": np.nan,
                    "mae_95ci_high": np.nan,
                    "rmse_ms": np.nan,
                    "rmse_95ci_low": np.nan,
                    "rmse_95ci_high": np.nan,
                    "error_sd_ms": np.nan,
                    "error_sd_95ci_low": np.nan,
                    "error_sd_95ci_high": np.nan,
                    "status": "unidentifiable_zero_grade_a_coverage",
                    "cma_home_advantage": agency == "CMA",
                }
            )
            continue
        point = error_metrics(rows["truth_10min_ms"].to_numpy(float), rows[agency].to_numpy(float))
        interval = bootstrap_error_metrics(
            rows["truth_10min_ms"].to_numpy(float),
            rows[agency].to_numpy(float),
            replicates=replicates,
            seed=seed + offset,
        )
        summaries.append(
            {
                "agency": agency,
                "landfall_events": len(landfalls),
                "matched_independent_truth_events": len(rows),
                "truth_coverage_fraction": len(rows) / len(landfalls),
                **point,
                "bias_95ci_low": interval["bias_ms"][0],
                "bias_95ci_high": interval["bias_ms"][1],
                "mae_95ci_low": interval["mae_ms"][0],
                "mae_95ci_high": interval["mae_ms"][1],
                "rmse_95ci_low": interval["rmse_ms"][0],
                "rmse_95ci_high": interval["rmse_ms"][1],
                "error_sd_95ci_low": interval["error_sd_ms"][0],
                "error_sd_95ci_high": interval["error_sd_ms"][1],
                "status": "measured_against_external_grade_a_truth",
                "cma_home_advantage": agency == "CMA",
            }
        )
    if len(rows) >= 3:
        error_columns = [f"{agency}_error_ms" for agency in agencies]
        correlation = rows[error_columns].corr()
        correlation.index = agencies
        correlation.columns = agencies
    else:
        correlation = pd.DataFrame(np.nan, index=agencies, columns=agencies)
    return rows, pd.DataFrame(summaries), correlation


def coverage_table(
    events: Sequence[Event],
    truth: pd.DataFrame,
    source_ids: Sequence[str],
    *,
    event_context: Mapping[str, Mapping[str, Any]] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in events:
        subset = truth.loc[truth["SID"] == event.sid]
        row: dict[str, Any] = {
            "SID": event.sid,
            "NAME": event.name,
            "crossing_time_utc": event.crossing_time.isoformat(),
            "grade_a_records": int(subset["grade"].eq("A").sum()),
            "grade_b_records": int(subset["grade"].eq("B").sum()),
            "grade_a_event": bool(subset["grade"].eq("A").any()),
            "grade_a_or_b_event": bool(len(subset)),
            "final_status": (
                "grade_a_observation_found"
                if subset["grade"].eq("A").any()
                else "grade_b_observation_found"
                if len(subset)
                else "no_public_observation_found"
            ),
        }
        if event_context and event.sid in event_context:
            row.update(event_context[event.sid])
        for source_id in source_ids:
            source_subset = subset.loc[subset["source_id"] == source_id]
            row[f"{source_id}_records"] = len(source_subset)
            row[f"{source_id}_status"] = (
                "observation_found" if len(source_subset) else "no_matched_observation"
            )
        rows.append(row)
    return pd.DataFrame(rows)


def source_event_status_table(
    events: Sequence[Event],
    truth: pd.DataFrame,
    source_catalog: Sequence[Mapping[str, Any]],
    *,
    collected_source_ids: Sequence[str],
    absent_status: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    collected = set(collected_source_ids)
    rows: list[dict[str, Any]] = []
    for event in events:
        event_rows = truth.loc[truth["SID"] == event.sid]
        for source in source_catalog:
            source_id = str(source["source_id"])
            subset = event_rows.loc[event_rows["source_id"] == source_id]
            if len(subset):
                status = "observation_found"
                reason = "At least one independently measured grade-A/B record matched this frozen event"
                searched_for_event = True
            elif source_id in collected:
                status = "no_matched_observation"
                reason = "The enumerable public archive was queried and returned no retained record for this event"
                searched_for_event = True
            else:
                fallback = absent_status[source_id]
                status = str(fallback["status"])
                reason = str(fallback["reason"])
                searched_for_event = bool(fallback["searched_for_event"])
            rows.append(
                {
                    "SID": event.sid,
                    "NAME": event.name,
                    "crossing_time_utc": event.crossing_time.isoformat(),
                    "source_id": source_id,
                    "source_name": source["source"],
                    "availability": source["availability"],
                    "searched_for_event": searched_for_event,
                    "record_count": len(subset),
                    "grade_a_records": int(subset["grade"].eq("A").sum()),
                    "grade_b_records": int(subset["grade"].eq("B").sum()),
                    "status": status,
                    "status_reason": reason,
                    "access": source["access"],
                    "source_url": source["url"],
                    "evidence": source["evidence"],
                }
            )
    return pd.DataFrame(rows)


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_ready(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
