#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from path_benchmark.core import parse_adeck  # noqa: E402
from path_benchmark.eligibility import (  # noqa: E402
    KNOT_TO_MS,
    common_cycle_count_at_lead,
    read_cma_peak_candidates,
)


SEASONS = {2022, 2023, 2024}
AIDS = ("CMC", "NGX")
REQUIRED_LEAD_HOURS = 72
CMA_STRONG_TYPHOON_THRESHOLD_MS = 41.5
IBTRACS_URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-"
    "stewardship-ibtracs/v04r01/access/csv/ibtracs.WP.list.v04r01.csv"
)
ADECK_TEMPLATE = (
    "https://hurricanes.ral.ucar.edu/repository/data/adecks_open/{year}/"
    "a{atcf_id_lower}.dat"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, destination: Path, fallback: Path | None = None) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_mode = "cached"
    if not destination.exists():
        if fallback is not None and fallback.exists():
            shutil.copyfile(fallback, destination)
            source_mode = "shared-local-copy"
        else:
            request = urllib.request.Request(
                url, headers={"User-Agent": "typhoon-path-benchmark/2.0"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                destination.write_bytes(response.read())
            source_mode = "download"
    return {
        "url": url,
        "local_path": str(destination.relative_to(PROJECT_ROOT)),
        "source_mode": source_mode,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def main() -> None:
    raw_dir = PROJECT_ROOT / "data" / "raw"
    ibtracs_path = raw_dir / "ibtracs.WP.list.v04r01.csv"
    shared_ibtracs = (
        PROJECT_ROOT
        / "../ibtracs-agency-disagreement/data/raw/ibtracs.WP.list.v04r01.csv"
    ).resolve()
    ibtracs_source = fetch(IBTRACS_URL, ibtracs_path, shared_ibtracs)

    all_storms = read_cma_peak_candidates(ibtracs_path, SEASONS)
    intensity_candidates = [
        storm
        for storm in all_storms
        if storm.peak_cma_wind_ms is not None
        and storm.peak_cma_wind_ms >= CMA_STRONG_TYPHOON_THRESHOLD_MS
    ]

    audit_rows: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    adeck_sources: dict[str, dict[str, Any]] = {}
    for storm in intensity_candidates:
        url = ADECK_TEMPLATE.format(
            year=storm.season, atcf_id_lower=storm.atcf_id.lower()
        )
        destination = raw_dir / Path(url).name
        source = fetch(url, destination)
        adeck_sources[storm.atcf_id] = source
        points = parse_adeck(
            destination.read_text(encoding="utf-8"),
            storm.atcf_id,
            set(AIDS),
            {REQUIRED_LEAD_HOURS},
        )
        common_cycles, cycles_by_aid = common_cycle_count_at_lead(
            points, AIDS, REQUIRED_LEAD_HOURS
        )
        is_qualified = common_cycles > 0
        row = {
            "atcf_id": storm.atcf_id,
            "season": storm.season,
            "name": storm.name,
            "peak_cma_wind_kt": storm.peak_cma_wind_kt,
            "peak_cma_wind_ms": storm.peak_cma_wind_ms,
            "cma_peak_threshold_ms": CMA_STRONG_TYPHOON_THRESHOLD_MS,
            "common_cycle_count_at_72h": common_cycles,
            "cycle_count_at_72h_by_aid": cycles_by_aid,
            "qualified": is_qualified,
            "exclusion_reason": None if is_qualified else "no_common_CMC_NGX_72h_cycle",
            "adeck_url": url,
            "adeck_sha256": source["sha256"],
        }
        audit_rows.append(row)
        if is_qualified:
            qualified.append(
                {
                    "atcf_id": storm.atcf_id,
                    "name": storm.name,
                    "adeck_url": url,
                }
            )

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "eligibility-frozen-before-round-v2-errors",
        "selection_rule": {
            "seasons": sorted(SEASONS),
            "basin": "WP",
            "atcf_serial_range": "01-49",
            "track_type": "main",
            "peak_field": "CMA_WIND",
            "peak_native_window": "2-minute",
            "knot_to_ms": KNOT_TO_MS,
            "strong_typhoon_threshold_ms": CMA_STRONG_TYPHOON_THRESHOLD_MS,
            "coverage_aids": list(AIDS),
            "required_common_lead_hours": REQUIRED_LEAD_HOURS,
            "coverage_requires_same_cycle": True,
            "reads_track_error": False,
        },
        "counts": {
            "wp_atcf_storms_2022_2024": len(all_storms),
            "missing_cma_peak": sum(
                storm.peak_cma_wind_ms is None for storm in all_storms
            ),
            "strong_typhoon_intensity_candidates": len(intensity_candidates),
            "qualified_storms": len(qualified),
        },
        "sources": {"IBTrACS": ibtracs_source, "adecks": adeck_sources},
        "candidate_audit": audit_rows,
        "qualified_storms": qualified,
    }
    config = {
        "status": "prospective-expanded-sample-learning-reproduction",
        "selection_manifest": "config/round_v2_eligibility_manifest.json",
        "storms": qualified,
        "aids": {
            "CMC": {"tech_variant": "raw-late-cycle", "interpolated": False},
            "NGX": {"tech_variant": "raw-late-cycle", "interpolated": False},
        },
        "lead_hours": [24, 48, 72, 96, 120],
        "bootstrap": {"replicates": 2000, "seed": 20260715, "cluster": "storm"},
        "ibtracs": {
            "url": IBTRACS_URL,
            "shared_local_path": (
                "../ibtracs-agency-disagreement/data/raw/ibtracs.WP.list.v04r01.csv"
            ),
            "fields": [
                "USA_ATCF_ID",
                "ISO_TIME",
                "USA_LAT",
                "USA_LON",
                "CMA_WIND",
            ],
        },
    }
    manifest_path = PROJECT_ROOT / "config" / "round_v2_eligibility_manifest.json"
    config_path = PROJECT_ROOT / "config" / "round_v2.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Frozen {len(qualified)} qualified storms from "
        f"{len(intensity_candidates)} intensity candidates."
    )


if __name__ == "__main__":
    main()
