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
    dyc2_latitude: float | None = None
    dyc2_longitude: float | None = None
    dyc2_error_km: float | None = None

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


def spherical_consensus(
    positions: Sequence[tuple[float, float]],
    weights: Sequence[float] | None = None,
) -> tuple[float, float]:
    if not positions:
        raise ValueError("spherical consensus requires at least one position")
    if weights is None:
        weights = [1.0] * len(positions)
    if len(weights) != len(positions):
        raise ValueError("weights and positions must have the same length")
    if any(weight < 0.0 for weight in weights) or sum(weights) <= 0.0:
        raise ValueError("spherical consensus weights must be nonnegative and nonzero")

    x = y = z = 0.0
    for (latitude, longitude), weight in zip(positions, weights):
        phi = math.radians(latitude)
        lam = math.radians(longitude)
        x += weight * math.cos(phi) * math.cos(lam)
        y += weight * math.cos(phi) * math.sin(lam)
        z += weight * math.sin(phi)
    norm = math.sqrt(x * x + y * y + z * z)
    if norm < 1e-12:
        raise DataConflictError("spherical consensus vector is geometrically singular")
    x, y, z = x / norm, y / norm, z / norm
    latitude = math.degrees(math.atan2(z, math.hypot(x, y)))
    longitude = ((math.degrees(math.atan2(y, x)) + 180.0) % 360.0) - 180.0
    return latitude, longitude


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
        dyc2_latitude, dyc2_longitude = spherical_consensus(
            [(cmc.latitude, cmc.longitude), (ngx.latitude, ngx.longitude)],
            [0.5, 0.5],
        )
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
                dyc2_latitude=dyc2_latitude,
                dyc2_longitude=dyc2_longitude,
                dyc2_error_km=track_error_km(
                    dyc2_latitude,
                    dyc2_longitude,
                    target.latitude,
                    target.longitude,
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


def _require_dyc2_error(row: PairedTrackRow) -> float:
    if row.dyc2_error_km is None:
        raise ValueError("DYC2 error is missing")
    return row.dyc2_error_km


def summarize_rows_v2(
    rows: Sequence[PairedTrackRow],
    leads: Sequence[int],
    replicates: int,
    seed: int,
) -> list[dict[str, object]]:
    rng = random.Random(seed)
    summary: list[dict[str, object]] = []
    getters: tuple[tuple[str, Callable[[PairedTrackRow], float]], ...] = (
        ("CMC", lambda row: row.cmc_error_km),
        ("NGX", lambda row: row.ngx_error_km),
        ("DYC2", _require_dyc2_error),
    )
    for lead in leads:
        lead_rows = [row for row in rows if row.lead_hours == lead]
        if not lead_rows:
            continue
        for aid, getter in getters:
            values = [getter(row) for row in lead_rows]
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

        for label, difference in (
            (
                "DYC2_MINUS_CMC",
                lambda row: _require_dyc2_error(row) - row.cmc_error_km,
            ),
            (
                "DYC2_MINUS_NGX",
                lambda row: _require_dyc2_error(row) - row.ngx_error_km,
            ),
        ):
            values = [difference(row) for row in lead_rows]
            difference_ci = _cluster_bootstrap(
                lead_rows, difference, statistics.fmean, replicates, rng
            )
            summary.append(
                {
                    "lead_hours": lead,
                    "aid": label,
                    "record_count": len(values),
                    "storm_count": len({row.atcf_id for row in lead_rows}),
                    "kish_effective_storm_count": _kish_storm_count(lead_rows),
                    "mean_error_difference_km": statistics.fmean(values),
                    "mean_error_difference_ci95_km": list(difference_ci),
                }
            )
    return summary


def _pearson(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("correlation requires paired data")
    x_mean = statistics.fmean(x)
    y_mean = statistics.fmean(y)
    x_centered = [value - x_mean for value in x]
    y_centered = [value - y_mean for value in y]
    numerator = sum(a * b for a, b in zip(x_centered, y_centered))
    denominator = math.sqrt(
        sum(value * value for value in x_centered)
        * sum(value * value for value in y_centered)
    )
    if denominator == 0.0:
        raise ValueError("correlation is undefined for a constant series")
    return max(-1.0, min(1.0, numerator / denominator))


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        for original_index, _value in ordered[index:end]:
            ranks[original_index] = average_rank
        index = end
    return ranks


def _correlation(
    rows: Sequence[PairedTrackRow],
    method: str,
    lead_centered: bool,
) -> float:
    x = [row.cmc_error_km for row in rows]
    y = [row.ngx_error_km for row in rows]
    if lead_centered:
        x_means: dict[int, float] = {}
        y_means: dict[int, float] = {}
        for lead in {row.lead_hours for row in rows}:
            lead_rows = [row for row in rows if row.lead_hours == lead]
            x_means[lead] = statistics.fmean(row.cmc_error_km for row in lead_rows)
            y_means[lead] = statistics.fmean(row.ngx_error_km for row in lead_rows)
        x = [value - x_means[row.lead_hours] for value, row in zip(x, rows)]
        y = [value - y_means[row.lead_hours] for value, row in zip(y, rows)]
    if method == "pearson":
        return _pearson(x, y)
    if method == "spearman":
        return _pearson(_average_ranks(x), _average_ranks(y))
    raise ValueError(f"unknown correlation method: {method}")


def _bootstrap_row_statistic(
    rows: Sequence[PairedTrackRow],
    statistic: Callable[[Sequence[PairedTrackRow]], float],
    replicates: int,
    rng: random.Random,
) -> list[float]:
    groups: dict[str, list[PairedTrackRow]] = defaultdict(list)
    for row in rows:
        groups[row.atcf_id].append(row)
    storm_ids = sorted(groups)
    estimates: list[float] = []
    for _ in range(replicates):
        sample_ids = [rng.choice(storm_ids) for _ in storm_ids]
        sample = [row for storm_id in sample_ids for row in groups[storm_id]]
        try:
            estimates.append(float(statistic(sample)))
        except ValueError:
            continue
    if len(estimates) < max(100, replicates // 2):
        raise ValueError("too few defined cluster-bootstrap correlation estimates")
    return estimates


def _neff_two(rho: float) -> float:
    denominator = 1.0 + rho
    return math.inf if denominator <= 0.0 else 2.0 / denominator


def correlation_diagnostics(
    rows: Sequence[PairedTrackRow],
    leads: Sequence[int],
    replicates: int,
    seed: int,
) -> dict[str, object]:
    rng = random.Random(seed)

    def calculate(
        selected: Sequence[PairedTrackRow], method: str, lead_centered: bool
    ) -> dict[str, object]:
        statistic = lambda sample: _correlation(sample, method, lead_centered)
        estimate = statistic(selected)
        bootstrap = _bootstrap_row_statistic(selected, statistic, replicates, rng)
        result: dict[str, object] = {
            "method": method,
            "lead_centered": lead_centered,
            "rho": estimate,
            "rho_ci95": [
                _percentile(bootstrap, 0.025),
                _percentile(bootstrap, 0.975),
            ],
            "record_count": len(selected),
            "storm_count": len({row.atcf_id for row in selected}),
        }
        if method == "pearson":
            neff_bootstrap = [_neff_two(value) for value in bootstrap]
            result["neff"] = _neff_two(estimate)
            result["neff_ci95"] = [
                _percentile(neff_bootstrap, 0.025),
                _percentile(neff_bootstrap, 0.975),
            ]
        return result

    by_lead = {}
    for lead in leads:
        selected = [row for row in rows if row.lead_hours == lead]
        if selected:
            by_lead[str(lead)] = calculate(selected, "pearson", False)
    return {
        "assumption": "exchangeable two-source correlation; neff=2/(1+rho)",
        "scope_disclaimer": (
            "n_eff measures agreement of paired radial track errors, not model "
            "dynamical independence or forecast accuracy."
        ),
        "primary": calculate(rows, "pearson", True),
        "raw_pearson_sensitivity": calculate(rows, "pearson", False),
        "lead_centered_spearman_sensitivity": calculate(rows, "spearman", True),
        "raw_pearson_by_lead": by_lead,
    }


def leave_one_storm_out_intervals(
    rows: Sequence[PairedTrackRow],
    leads: Sequence[int],
    quantiles: Sequence[float],
    minimum_training_storms: int,
    replicates: int,
    seed: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rng = random.Random(seed)
    summaries: list[dict[str, object]] = []
    evaluation_rows: list[dict[str, object]] = []
    for lead in leads:
        lead_rows = [row for row in rows if row.lead_hours == lead]
        storm_ids = sorted({row.atcf_id for row in lead_rows})
        if len(storm_ids) - 1 < minimum_training_storms:
            summaries.append(
                {
                    "lead_hours": lead,
                    "status": "insufficient-training-storms",
                    "available_storms": len(storm_ids),
                    "minimum_training_storms": minimum_training_storms,
                }
            )
            continue
        for held_out in storm_ids:
            training = [row for row in lead_rows if row.atcf_id != held_out]
            held_rows = [row for row in lead_rows if row.atcf_id == held_out]
            training_errors = [_require_dyc2_error(row) for row in training]
            for quantile in quantiles:
                radius = _percentile(training_errors, quantile)
                for row in held_rows:
                    error = _require_dyc2_error(row)
                    evaluation_rows.append(
                        {
                            "atcf_id": row.atcf_id,
                            "storm_name": row.storm_name,
                            "lead_hours": lead,
                            "quantile": quantile,
                            "training_storm_count": len(storm_ids) - 1,
                            "radius_km": radius,
                            "dyc2_error_km": error,
                            "covered": error <= radius,
                        }
                    )

        for quantile in quantiles:
            selected = [
                row
                for row in evaluation_rows
                if row["lead_hours"] == lead and row["quantile"] == quantile
            ]
            groups: dict[str, list[dict[str, object]]] = defaultdict(list)
            for row in selected:
                groups[str(row["atcf_id"])].append(row)
            estimates: list[float] = []
            for _ in range(replicates):
                sample_ids = [rng.choice(storm_ids) for _ in storm_ids]
                covered = [
                    float(bool(row["covered"]))
                    for storm_id in sample_ids
                    for row in groups[storm_id]
                ]
                estimates.append(statistics.fmean(covered))
            radii = [float(row["radius_km"]) for row in selected]
            coverage = statistics.fmean(float(bool(row["covered"])) for row in selected)
            summaries.append(
                {
                    "lead_hours": lead,
                    "status": "estimated",
                    "quantile": quantile,
                    "record_count": len(selected),
                    "storm_count": len(storm_ids),
                    "training_storm_count_per_fold": len(storm_ids) - 1,
                    "median_radius_km": statistics.median(radii),
                    "radius_range_km": [min(radii), max(radii)],
                    "coverage": coverage,
                    "coverage_ci95": [
                        _percentile(estimates, 0.025),
                        _percentile(estimates, 0.975),
                    ],
                }
            )
    return summaries, evaluation_rows
