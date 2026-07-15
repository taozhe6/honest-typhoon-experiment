#!/usr/bin/env python3
"""Inventory official ERC observation sources without downloading science arrays."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_CONFIG = ROOT / "config" / "erc_observation_sources.json"
S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def fetch_bytes(url: str, timeout_seconds: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "typhoon-markov-source-audit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def list_s3_keys(
    bucket_url: str,
    prefix: str,
    timeout_seconds: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    keys: list[str] = []
    evidence: list[dict[str, Any]] = []
    continuation_token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if continuation_token:
            params["continuation-token"] = continuation_token
        url = f"{bucket_url}/?{urllib.parse.urlencode(params)}"
        payload = fetch_bytes(url, timeout_seconds)
        evidence.append(
            {
                "url": url,
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
        )
        root = ET.fromstring(payload)
        keys.extend(
            node.text or ""
            for node in root.findall("s3:Contents/s3:Key", S3_NAMESPACE)
        )
        truncated = (
            root.findtext(
                "s3:IsTruncated", default="false", namespaces=S3_NAMESPACE
            )
            == "true"
        )
        if not truncated:
            break
        continuation_token = root.findtext(
            "s3:NextContinuationToken", namespaces=S3_NAMESPACE
        )
        if not continuation_token:
            raise ValueError("truncated S3 response omitted continuation token")
    return keys, evidence


def list_s3_common_prefixes(
    bucket_url: str,
    prefix: str,
    timeout_seconds: float,
) -> tuple[list[str], dict[str, Any]]:
    params = {
        "list-type": "2",
        "prefix": prefix,
        "delimiter": "/",
        "max-keys": "1000",
    }
    url = f"{bucket_url}/?{urllib.parse.urlencode(params)}"
    payload = fetch_bytes(url, timeout_seconds)
    root = ET.fromstring(payload)
    prefixes = [
        node.text or ""
        for node in root.findall(
            "s3:CommonPrefixes/s3:Prefix", S3_NAMESPACE
        )
    ]
    return prefixes, {
        "url": url,
        "bytes": len(payload),
        "sha256": sha256_bytes(payload),
    }


def summarize_tcprimed_keys(keys: list[str]) -> dict[str, Any]:
    storms: set[str] = set()
    sensors: Counter[str] = Counter()
    environment_files = 0
    for key in keys:
        parts = key.split("/")
        if len(parts) < 6:
            continue
        storms.add(parts[4])
        filename = parts[-1]
        if "_env_" in filename:
            environment_files += 1
            continue
        filename_parts = filename.split("_")
        if len(filename_parts) >= 4:
            sensors[filename_parts[3]] += 1
    return {
        "storm_count": len(storms),
        "overpass_file_count": sum(sensors.values()),
        "environment_file_count": environment_files,
        "overpass_count_by_sensor": dict(sorted(sensors.items())),
    }


def parse_cyclobs_rows(payload: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))


def summarize_cyclobs_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "acquisition_count": len(rows),
        "storm_count": len({row["sid"] for row in rows}),
        "eye_in_acquisition_count": sum(
            row.get("eye_in_acq", "").lower() == "true" for row in rows
        ),
        "analysis_vmax_available_count": sum(
            bool(row.get("analysis_vmax", "")) for row in rows
        ),
        "acquisition_count_by_mission": dict(
            sorted(Counter(row.get("mission", "") for row in rows).items())
        ),
    }


def archer_west_pacific_ids(payload: bytes, season: int) -> list[str]:
    pattern = re.compile(rf"{season}_[0-9]{{2}}W/")
    return sorted({match.rstrip("/") for match in pattern.findall(payload.decode())})


def cyclobs_query_url(
    api_url: str,
    *,
    start_date: str,
    stop_date: str,
    basin: str | None = None,
    sid: str | None = None,
) -> str:
    params = {
        "acquisition_start_time": start_date,
        "acquisition_stop_time": stop_date,
        "include_cols": (
            "sid,mission,acquisition_start_time,eye_in_acq,"
            "analysis_vmax,analysis_rmax"
        ),
    }
    if basin:
        params["basin"] = basin
    if sid:
        params["sid"] = sid
    return f"{api_url}?{urllib.parse.urlencode(params)}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="+", type=int, required=True)
    parser.add_argument("--basin", default="WP")
    parser.add_argument("--cyclobs-basin", default="NWP")
    parser.add_argument("--current-season", type=int, required=True)
    parser.add_argument("--current-sid", required=True)
    parser.add_argument("--source-config", type=Path, default=DEFAULT_SOURCE_CONFIG)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = json.loads(args.source_config.read_text(encoding="utf-8"))
    sources = config["sources"]
    bucket_url = sources["tc_primed"]["bucket_url"].rstrip("/")
    cyclobs_api = sources["cyclobs_sar"]["api_url"]
    archer_archive = sources["archer_mperc"]["archive_url"]

    tcprimed_final: dict[str, Any] = {}
    source_evidence: dict[str, Any] = {}
    for year in args.years:
        prefix = f"v01r01/final/{year}/{args.basin}/"
        keys, evidence = list_s3_keys(bucket_url, prefix, args.timeout_seconds)
        tcprimed_final[str(year)] = summarize_tcprimed_keys(keys)
        source_evidence[f"tcprimed_final_{year}"] = evidence

    preliminary_prefix = (
        f"v01r01/preliminary/{args.current_season}/{args.basin}/"
    )
    preliminary, preliminary_evidence = list_s3_common_prefixes(
        bucket_url, preliminary_prefix, args.timeout_seconds
    )
    source_evidence["tcprimed_current_preliminary"] = preliminary_evidence

    start_date = f"{min(args.years)}-01-01"
    stop_date = f"{max(args.years) + 1}-01-01"
    sealed_url = cyclobs_query_url(
        cyclobs_api,
        start_date=start_date,
        stop_date=stop_date,
        basin=args.cyclobs_basin,
    )
    sealed_payload = fetch_bytes(sealed_url, args.timeout_seconds)
    sealed_rows = parse_cyclobs_rows(sealed_payload)
    by_year: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in sealed_rows:
        by_year[row["acquisition_start_time"][:4]].append(row)

    current_url = cyclobs_query_url(
        cyclobs_api,
        start_date=f"{args.current_season}-01-01",
        stop_date=f"{args.current_season + 1}-01-01",
        sid=args.current_sid,
    )
    current_payload = fetch_bytes(current_url, args.timeout_seconds)
    current_rows = parse_cyclobs_rows(current_payload)

    archer_payload = fetch_bytes(archer_archive, args.timeout_seconds)
    source_evidence["cyclobs_sealed_period"] = {
        "url": sealed_url,
        "bytes": len(sealed_payload),
        "sha256": sha256_bytes(sealed_payload),
    }
    source_evidence["cyclobs_current_storm"] = {
        "url": current_url,
        "bytes": len(current_payload),
        "sha256": sha256_bytes(current_payload),
    }
    source_evidence["archer_archive"] = {
        "url": archer_archive,
        "bytes": len(archer_payload),
        "sha256": sha256_bytes(archer_payload),
    }

    report = {
        "report_id": "erc-observation-source-availability",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "implementation": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_bytes(Path(__file__).resolve().read_bytes()),
        },
        "source_registry": str(args.source_config.resolve()),
        "tc_primed_final": tcprimed_final,
        "tc_primed_current_preliminary": {
            "season": args.current_season,
            "basin": args.basin,
            "available_annual_numbers": [
                prefix.rstrip("/").split("/")[-1] for prefix in preliminary
            ],
        },
        "cyclobs_sealed_period": {
            year: summarize_cyclobs_rows(by_year.get(str(year), []))
            for year in args.years
        },
        "cyclobs_current_storm": {
            "sid": args.current_sid,
            **summarize_cyclobs_rows(current_rows),
        },
        "archer_current_west_pacific": {
            "season": args.current_season,
            "storm_ids": archer_west_pacific_ids(
                archer_payload, args.current_season
            ),
        },
        "source_evidence": source_evidence,
        "interpretation_limits": [
            "TC PRIMED file counts measure inventory; per-pass inner-core coverage requires NetCDF quality inspection.",
            "CyclObs acquisition counts measure sparse direct-wind opportunities and remain mission-planning dependent.",
            "ARCHER public directory presence measures exposed public products at retrieval time.",
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
