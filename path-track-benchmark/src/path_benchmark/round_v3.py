from __future__ import annotations

import math
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Sequence

from .core import (
    ForecastPoint,
    TruthPoint,
    _average_ranks,
    _neff_two,
    _pearson,
    _percentile,
    spherical_consensus,
    track_error_km,
)


STREAMS = ("CMC", "NGX", "UKM", "LOCAL_EQ2_CMC_NGX")
REQUIRED_AIDS = frozenset(("CMC", "NGX", "UKM"))


@dataclass(frozen=True)
class TripleTrackRow:
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
    ukm_latitude: float
    ukm_longitude: float
    local_eq2_latitude: float
    local_eq2_longitude: float
    cmc_error_km: float
    ngx_error_km: float
    ukm_error_km: float
    local_eq2_error_km: float

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["cycle_utc"] = self.cycle_utc.isoformat().replace("+00:00", "Z")
        result["valid_time_utc"] = self.valid_time_utc.isoformat().replace(
            "+00:00", "Z"
        )
        return result


def strict_triple_pair(
    forecasts: Iterable[ForecastPoint],
    truth: dict[tuple[str, datetime], TruthPoint],
    storm_names: dict[str, str],
) -> list[TripleTrackRow]:
    by_key: dict[tuple[str, datetime, int], dict[str, ForecastPoint]] = defaultdict(dict)
    for point in forecasts:
        if point.aid in REQUIRED_AIDS:
            by_key[(point.atcf_id, point.cycle_utc, point.lead_hours)][point.aid] = point

    rows: list[TripleTrackRow] = []
    for (atcf_id, cycle, lead), aids in sorted(by_key.items()):
        if frozenset(aids) != REQUIRED_AIDS:
            continue
        valid_time = cycle + timedelta(hours=lead)
        target = truth.get((atcf_id, valid_time))
        if target is None:
            continue
        cmc, ngx, ukm = aids["CMC"], aids["NGX"], aids["UKM"]
        local_latitude, local_longitude = spherical_consensus(
            [(cmc.latitude, cmc.longitude), (ngx.latitude, ngx.longitude)],
            [0.5, 0.5],
        )
        rows.append(
            TripleTrackRow(
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
                ukm_latitude=ukm.latitude,
                ukm_longitude=ukm.longitude,
                local_eq2_latitude=local_latitude,
                local_eq2_longitude=local_longitude,
                cmc_error_km=track_error_km(
                    cmc.latitude, cmc.longitude, target.latitude, target.longitude
                ),
                ngx_error_km=track_error_km(
                    ngx.latitude, ngx.longitude, target.latitude, target.longitude
                ),
                ukm_error_km=track_error_km(
                    ukm.latitude, ukm.longitude, target.latitude, target.longitude
                ),
                local_eq2_error_km=track_error_km(
                    local_latitude,
                    local_longitude,
                    target.latitude,
                    target.longitude,
                ),
            )
        )
    return rows


def error_value(row: TripleTrackRow, stream: str) -> float:
    attributes = {
        "CMC": "cmc_error_km",
        "NGX": "ngx_error_km",
        "UKM": "ukm_error_km",
        "LOCAL_EQ2_CMC_NGX": "local_eq2_error_km",
    }
    try:
        return float(getattr(row, attributes[stream]))
    except KeyError as error:
        raise ValueError(f"unknown error stream: {stream}") from error


def _kish_storm_count(rows: Sequence[TripleTrackRow]) -> float:
    weights = list(Counter(row.atcf_id for row in rows).values())
    return sum(weights) ** 2 / sum(weight * weight for weight in weights)


def _sample_storm_blocks(
    groups: dict[str, list[TripleTrackRow]],
    storm_ids: Sequence[str],
    rng: random.Random,
) -> list[TripleTrackRow]:
    sampled_ids = [rng.choice(storm_ids) for _ in storm_ids]
    return [row for storm_id in sampled_ids for row in groups[storm_id]]


def _cluster_interval(
    rows: Sequence[TripleTrackRow],
    statistic: Callable[[Sequence[TripleTrackRow]], float],
    replicates: int,
    rng: random.Random,
) -> list[float]:
    groups: dict[str, list[TripleTrackRow]] = defaultdict(list)
    for row in rows:
        groups[row.atcf_id].append(row)
    storm_ids = sorted(groups)
    estimates = [
        float(statistic(_sample_storm_blocks(groups, storm_ids, rng)))
        for _ in range(replicates)
    ]
    return [_percentile(estimates, 0.025), _percentile(estimates, 0.975)]


def summarize_triple_rows(
    rows: Sequence[TripleTrackRow],
    leads: Sequence[int],
    replicates: int,
    seed: int,
) -> list[dict[str, object]]:
    rng = random.Random(seed)
    summary: list[dict[str, object]] = []
    for lead in leads:
        selected = [row for row in rows if row.lead_hours == lead]
        if not selected:
            continue
        for stream in STREAMS:
            values = [error_value(row, stream) for row in selected]
            mean_statistic = lambda sample, name=stream: statistics.fmean(
                error_value(row, name) for row in sample
            )
            median_statistic = lambda sample, name=stream: statistics.median(
                error_value(row, name) for row in sample
            )
            summary.append(
                {
                    "lead_hours": lead,
                    "stream": stream,
                    "record_count": len(selected),
                    "storm_count": len({row.atcf_id for row in selected}),
                    "kish_effective_storm_count": _kish_storm_count(selected),
                    "mean_error_km": statistics.fmean(values),
                    "mean_error_ci95_km": _cluster_interval(
                        selected, mean_statistic, replicates, rng
                    ),
                    "median_error_km": statistics.median(values),
                    "median_error_ci95_km": _cluster_interval(
                        selected, median_statistic, replicates, rng
                    ),
                    "p80_error_km": _percentile(values, 0.8),
                }
            )

        difference = lambda row: (
            row.local_eq2_error_km - row.ukm_error_km
        )
        difference_statistic = lambda sample: statistics.fmean(
            difference(row) for row in sample
        )
        summary.append(
            {
                "lead_hours": lead,
                "stream": "LOCAL_EQ2_MINUS_UKM",
                "record_count": len(selected),
                "storm_count": len({row.atcf_id for row in selected}),
                "kish_effective_storm_count": _kish_storm_count(selected),
                "mean_error_difference_km": difference_statistic(selected),
                "mean_error_difference_ci95_km": _cluster_interval(
                    selected, difference_statistic, replicates, rng
                ),
            }
        )
    return summary


def _paired_correlation(
    rows: Sequence[TripleTrackRow],
    first: str,
    second: str,
    method: str,
    lead_centered: bool,
) -> float:
    x = [error_value(row, first) for row in rows]
    y = [error_value(row, second) for row in rows]
    if lead_centered:
        x_means: dict[int, float] = {}
        y_means: dict[int, float] = {}
        for lead in {row.lead_hours for row in rows}:
            selected = [row for row in rows if row.lead_hours == lead]
            x_means[lead] = statistics.fmean(error_value(row, first) for row in selected)
            y_means[lead] = statistics.fmean(error_value(row, second) for row in selected)
        x = [value - x_means[row.lead_hours] for value, row in zip(x, rows)]
        y = [value - y_means[row.lead_hours] for value, row in zip(y, rows)]
    if method == "pearson":
        return _pearson(x, y)
    if method == "spearman":
        return _pearson(_average_ranks(x), _average_ranks(y))
    raise ValueError(f"unknown correlation method: {method}")


def _diagnostic_estimates(
    rows: Sequence[TripleTrackRow],
    method: str,
    lead_centered: bool,
) -> dict[str, float]:
    cmc_ngx = _paired_correlation(
        rows, "CMC", "NGX", method=method, lead_centered=lead_centered
    )
    local_ukm = _paired_correlation(
        rows,
        "LOCAL_EQ2_CMC_NGX",
        "UKM",
        method=method,
        lead_centered=lead_centered,
    )
    result = {
        "rho_cmc_ngx": cmc_ngx,
        "rho_local_eq2_ukm": local_ukm,
    }
    if method == "pearson":
        baseline_neff = _neff_two(cmc_ngx)
        independent_neff = _neff_two(local_ukm)
        result.update(
            {
                "neff_cmc_ngx": baseline_neff,
                "neff_local_eq2_ukm": independent_neff,
                "delta_neff": independent_neff - baseline_neff,
            }
        )
    return result


def independence_diagnostics(
    rows: Sequence[TripleTrackRow],
    replicates: int,
    seed: int,
) -> dict[str, object]:
    if len({row.atcf_id for row in rows}) < 2:
        raise ValueError("independence diagnostics require at least two storms")
    groups: dict[str, list[TripleTrackRow]] = defaultdict(list)
    for row in rows:
        groups[row.atcf_id].append(row)
    storm_ids = sorted(groups)
    rng = random.Random(seed)

    def calculate(method: str, lead_centered: bool) -> dict[str, object]:
        estimate = _diagnostic_estimates(rows, method, lead_centered)
        bootstrap: list[dict[str, float]] = []
        for _ in range(replicates):
            sample = _sample_storm_blocks(groups, storm_ids, rng)
            try:
                bootstrap.append(_diagnostic_estimates(sample, method, lead_centered))
            except ValueError:
                continue
        if len(bootstrap) < max(100, replicates // 2):
            raise ValueError("too few defined storm-bootstrap diagnostics")
        result: dict[str, object] = {
            "method": method,
            "lead_centered": lead_centered,
            "record_count": len(rows),
            "storm_count": len(storm_ids),
        }
        for name, value in estimate.items():
            values = [item[name] for item in bootstrap]
            result[name] = value
            result[f"{name}_ci95"] = [
                _percentile(values, 0.025),
                _percentile(values, 0.975),
            ]
        return result

    return {
        "assumption": "exchangeable two-stream errors; n_eff=2/(1+rho)",
        "scope_disclaimer": (
            "n_eff measures agreement of two radial track-error streams; it does "
            "not measure accuracy or prove complete dynamical independence."
        ),
        "bootstrap_cluster": "atcf_id",
        "bootstrap_replicates": replicates,
        "primary": calculate("pearson", True),
        "raw_pearson_sensitivity": calculate("pearson", False),
        "lead_centered_spearman_sensitivity": calculate("spearman", True),
    }

