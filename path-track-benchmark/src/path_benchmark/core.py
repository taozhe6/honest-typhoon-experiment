from __future__ import annotations

import csv
import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from pyproj import Geod


WGS84 = Geod(ellps="WGS84")


class DataConflictError(ValueError):
    """Raised when one forecast or truth key has conflicting positions."""


@dataclass(frozen=True)
class ForecastPoint:
    atcf_id: str
    cycle_utc: datetime
    aid: str
    lead_hours: int
    latitude: float
    longitude: float

    @property
    def valid_time_utc(self) -> datetime:
        return self.cycle_utc + timedelta(hours=self.lead_hours)


@dataclass(frozen=True)
class TruthPoint:
    atcf_id: str
    valid_time_utc: datetime
    latitude: float
    longitude: float


@dataclass(frozen=True)
class PairedTrackRow:
    atcf_id: str
    storm_name: str
    cycle_utc: datetime
    valid_time_utc: datetime
    lead_hours: int
    truth_latitude: float
    truth_longitude: float
    cmc_latitude: float
    cmc_longitude: float
    ngx_latitude: float
    ngx_longitude: float
    cmc_error_km: float
    ngx_error_km: float

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["cycle_utc"] = self.cycle_utc.isoformat().replace("+00:00", "Z")
        result["valid_time_utc"] = self.valid_time_utc.isoformat().replace(
            "+00:00", "Z"
        )
        return result


def parse_atcf_coordinate(value: str) -> float:
    token = value.strip().upper()
    if len(token) < 2 or token[-1] not in "NSEW":
        raise ValueError(f"invalid ATCF coordinate: {value!r}")
    magnitude = float(token[:-1]) / 10.0
    if token[-1] in "SW":
        magnitude = -magnitude
    if token[-1] in "NS" and not -90.0 <= magnitude <= 90.0:
        raise ValueError(f"latitude out of range: {value!r}")
    if token[-1] in "EW" and not -180.0 <= magnitude <= 180.0:
        raise ValueError(f"longitude out of range: {value!r}")
    if token[-1] in "EW":
        magnitude = ((magnitude + 180.0) % 360.0) - 180.0
    return magnitude


def parse_adeck(
    text: str,
    expected_atcf_id: str,
    aids: set[str],
    lead_hours: set[int],
) -> list[ForecastPoint]:
    expected_basin = expected_atcf_id[:2]
    expected_number = expected_atcf_id[2:4]
    expected_year = expected_atcf_id[4:]
    unique: dict[tuple[str, datetime, str, int], ForecastPoint] = {}

    for raw in csv.reader(text.splitlines()):
        if len(raw) < 8:
            continue
        fields = [item.strip() for item in raw]
        basin, number, dtg, aid, tau = (
            fields[0].upper(),
            fields[1].zfill(2),
            fields[2],
            fields[4].upper(),
            fields[5],
        )
        if basin != expected_basin or number != expected_number or aid not in aids:
            continue
        if not dtg.startswith(expected_year):
            continue
        try:
            lead = int(tau)
        except ValueError:
            continue
        if lead not in lead_hours:
            continue
        cycle = datetime.strptime(dtg, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        point = ForecastPoint(
            atcf_id=expected_atcf_id,
            cycle_utc=cycle,
            aid=aid,
            lead_hours=lead,
            latitude=parse_atcf_coordinate(fields[6]),
            longitude=parse_atcf_coordinate(fields[7]),
        )
        key = (expected_atcf_id, cycle, aid, lead)
        previous = unique.get(key)
        if previous is not None and (
            previous.latitude != point.latitude
            or previous.longitude != point.longitude
        ):
            raise DataConflictError(
                f"conflicting a-deck positions for {key}: {previous} vs {point}"
            )
        unique[key] = point

    return sorted(
        unique.values(),
        key=lambda item: (item.cycle_utc, item.lead_hours, item.aid),
    )


def read_ibtracs_truth(
    path: Path, wanted_atcf_ids: set[str]
) -> dict[tuple[str, datetime], TruthPoint]:
    truth: dict[tuple[str, datetime], TruthPoint] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = csv.DictReader(handle)
        for row in rows:
            atcf_id = (row.get("USA_ATCF_ID") or "").strip().upper()
            if atcf_id not in wanted_atcf_ids:
                continue
            lat_token = (row.get("USA_LAT") or "").strip()
            lon_token = (row.get("USA_LON") or "").strip()
            time_token = (row.get("ISO_TIME") or "").strip()
            if not lat_token or not lon_token or not time_token:
                continue
            valid_time = datetime.strptime(time_token, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            point = TruthPoint(
                atcf_id=atcf_id,
                valid_time_utc=valid_time,
                latitude=float(lat_token),
                longitude=((float(lon_token) + 180.0) % 360.0) - 180.0,
            )
            key = (atcf_id, valid_time)
            previous = truth.get(key)
            if previous is not None and (
                previous.latitude != point.latitude
                or previous.longitude != point.longitude
            ):
                raise DataConflictError(
                    f"conflicting best-track positions for {key}: {previous} vs {point}"
                )
            truth[key] = point
    return truth


def track_error_km(
    forecast_latitude: float,
    forecast_longitude: float,
    truth_latitude: float,
    truth_longitude: float,
) -> float:
    _az12, _az21, distance_m = WGS84.inv(
        forecast_longitude,
        forecast_latitude,
        truth_longitude,
        truth_latitude,
    )
    return float(distance_m) / 1000.0


def strict_pair(
    forecasts: Iterable[ForecastPoint],
    truth: dict[tuple[str, datetime], TruthPoint],
    storm_names: dict[str, str],
) -> list[PairedTrackRow]:
    by_key: dict[tuple[str, datetime, int], dict[str, ForecastPoint]] = defaultdict(dict)
    for point in forecasts:
        by_key[(point.atcf_id, point.cycle_utc, point.lead_hours)][point.aid] = point

    paired: list[PairedTrackRow] = []
    for (atcf_id, cycle, lead), aids in sorted(by_key.items()):
        if set(aids) != {"CMC", "NGX"}:
            continue
        valid_time = cycle + timedelta(hours=lead)
        target = truth.get((atcf_id, valid_time))
        if target is None:
            continue
        cmc, ngx = aids["CMC"], aids["NGX"]
        paired.append(
            PairedTrackRow(
                atcf_id=atcf_id,
                storm_name=storm_names[atcf_id],
                cycle_utc=cycle,
                valid_time_utc=valid_time,
                lead_hours=lead,
                truth_latitude=target.latitude,
                truth_longitude=target.longitude,
                cmc_latitude=cmc.latitude,
                cmc_longitude=cmc.longitude,
                ngx_latitude=ngx.latitude,
                ngx_longitude=ngx.longitude,
                cmc_error_km=track_error_km(
                    cmc.latitude, cmc.longitude, target.latitude, target.longitude
                ),
                ngx_error_km=track_error_km(
                    ngx.latitude, ngx.longitude, target.latitude, target.longitude
                ),
            )
        )
    return paired


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires data")
    ordered = sorted(float(value) for value in values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _kish_storm_count(rows: Sequence[PairedTrackRow]) -> float:
    counts = Counter(row.atcf_id for row in rows)
    weights = list(counts.values())
    return sum(weights) ** 2 / sum(weight * weight for weight in weights)


def _cluster_bootstrap(
    rows: Sequence[PairedTrackRow],
    value: Callable[[PairedTrackRow], float],
    statistic: Callable[[Sequence[float]], float],
    replicates: int,
    rng: random.Random,
) -> tuple[float, float]:
    groups: dict[str, list[PairedTrackRow]] = defaultdict(list)
    for row in rows:
        groups[row.atcf_id].append(row)
    storm_ids = sorted(groups)
    if not storm_ids:
        raise ValueError("bootstrap requires at least one storm")
    estimates: list[float] = []
    for _ in range(replicates):
        sample_ids = [rng.choice(storm_ids) for _ in storm_ids]
        values = [value(row) for storm_id in sample_ids for row in groups[storm_id]]
        estimates.append(float(statistic(values)))
    return _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def summarize_rows(
    rows: Sequence[PairedTrackRow],
    leads: Sequence[int],
    replicates: int,
    seed: int,
) -> list[dict[str, object]]:
    rng = random.Random(seed)
    summary: list[dict[str, object]] = []
    for lead in leads:
        lead_rows = [row for row in rows if row.lead_hours == lead]
        for aid, getter in (
            ("CMC", lambda row: row.cmc_error_km),
            ("NGX", lambda row: row.ngx_error_km),
        ):
            values = [getter(row) for row in lead_rows]
            if not values:
                continue
            mean_ci = _cluster_bootstrap(
                lead_rows, getter, statistics.fmean, replicates, rng
            )
            median_ci = _cluster_bootstrap(
                lead_rows, getter, statistics.median, replicates, rng
            )
            summary.append(
                {
                    "lead_hours": lead,
                    "aid": aid,
                    "record_count": len(values),
                    "storm_count": len({row.atcf_id for row in lead_rows}),
                    "kish_effective_storm_count": _kish_storm_count(lead_rows),
                    "mean_error_km": statistics.fmean(values),
                    "mean_error_ci95_km": list(mean_ci),
                    "median_error_km": statistics.median(values),
                    "median_error_ci95_km": list(median_ci),
                    "p80_error_km": _percentile(values, 0.8),
                }
            )

        if lead_rows:
            difference = lambda row: row.cmc_error_km - row.ngx_error_km
            values = [difference(row) for row in lead_rows]
            difference_ci = _cluster_bootstrap(
                lead_rows, difference, statistics.fmean, replicates, rng
            )
            summary.append(
                {
                    "lead_hours": lead,
                    "aid": "CMC_MINUS_NGX",
                    "record_count": len(values),
                    "storm_count": len({row.atcf_id for row in lead_rows}),
                    "kish_effective_storm_count": _kish_storm_count(lead_rows),
                    "mean_error_difference_km": statistics.fmean(values),
                    "mean_error_difference_ci95_km": list(difference_ci),
                }
            )
    return summary

