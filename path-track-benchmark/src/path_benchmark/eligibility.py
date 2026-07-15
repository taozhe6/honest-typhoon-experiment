from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .core import ForecastPoint


KNOT_TO_MS = 0.5144444444444445
ATCF_ID_PATTERN = re.compile(r"^WP(0[1-9]|[1-4][0-9])\d{4}$")


@dataclass(frozen=True)
class StormCandidate:
    atcf_id: str
    season: int
    name: str
    peak_cma_wind_kt: float | None

    @property
    def peak_cma_wind_ms(self) -> float | None:
        if self.peak_cma_wind_kt is None:
            return None
        return self.peak_cma_wind_kt * KNOT_TO_MS


def read_cma_peak_candidates(
    path: Path,
    seasons: set[int],
) -> list[StormCandidate]:
    winds: dict[str, list[float]] = defaultdict(list)
    names: dict[str, Counter[str]] = defaultdict(Counter)
    observed_ids: set[str] = set()

    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            atcf_id = (row.get("USA_ATCF_ID") or "").strip().upper()
            if not ATCF_ID_PATTERN.fullmatch(atcf_id):
                continue
            try:
                season = int((row.get("SEASON") or "").strip())
            except ValueError:
                continue
            if season not in seasons or (row.get("TRACK_TYPE") or "").strip() != "main":
                continue
            observed_ids.add(atcf_id)
            name = (row.get("NAME") or "").strip().upper()
            if name:
                names[atcf_id][name] += 1
            token = (row.get("CMA_WIND") or "").strip()
            if token:
                try:
                    winds[atcf_id].append(float(token))
                except ValueError:
                    continue

    candidates: list[StormCandidate] = []
    for atcf_id in sorted(observed_ids):
        name = names[atcf_id].most_common(1)[0][0] if names[atcf_id] else "UNNAMED"
        candidates.append(
            StormCandidate(
                atcf_id=atcf_id,
                season=int(atcf_id[-4:]),
                name=name,
                peak_cma_wind_kt=max(winds[atcf_id]) if winds[atcf_id] else None,
            )
        )
    return candidates


def common_cycle_count_at_lead(
    points: Iterable[ForecastPoint],
    aids: tuple[str, str],
    lead_hours: int,
) -> tuple[int, dict[str, int]]:
    cycles = {aid: set() for aid in aids}
    for point in points:
        if point.aid in cycles and point.lead_hours == lead_hours:
            cycles[point.aid].add(point.cycle_utc)
    common = cycles[aids[0]] & cycles[aids[1]]
    return len(common), {aid: len(cycles[aid]) for aid in aids}
