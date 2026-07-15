#!/usr/bin/env python3
"""Audit primary microwave coverage around secondary Bavi ERC narrative windows."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from typhoon_markov.coverage import (
    classify_narrative_windows,
    evidence_tally,
    extract_cyclobs_passes,
    observed_dual_ring_intervals,
    parse_utc,
)


S3_NAMESPACE = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
DEFAULT_CONFIG = ROOT / "config" / "bavi_erc_coverage_audit.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "c_coverage_correction"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def fetch_tcprimed_keys(config: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    source = config["tc_primed"]
    params = {
        "list-type": "2",
        "prefix": source["storm_prefix"],
        "max-keys": "1000",
    }
    url = f"{source['bucket_url'].rstrip('/')}/?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "typhoon-markov-coverage-audit/1.0"}
    )
    with urllib.request.urlopen(request, timeout=90.0) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    keys = [
        node.text or "" for node in root.findall("s3:Contents/s3:Key", S3_NAMESPACE)
    ]
    return keys, {
        "url": url,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "response_bytes": len(payload),
        "response_sha256": sha256_bytes(payload),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized.append(
            {
                key: json.dumps(value, ensure_ascii=True)
                if isinstance(value, (list, dict))
                else value
                for key, value in row.items()
            }
        )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(normalized[0]))
        writer.writeheader()
        writer.writerows(normalized)


def plot_coverage(
    passes: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    tcprimed_file_count: int,
    output_path: Path,
) -> None:
    fig, axis = plt.subplots(figsize=(11.2, 5.2))
    fig.subplots_adjust(bottom=0.25, top=0.88, left=0.09, right=0.98)
    colors = ["#dedede", "#e7dcc8", "#d8e3df"]
    for color, window in zip(colors, windows):
        start = parse_utc(window["start_utc"])
        end = parse_utc(window["end_utc"])
        axis.axvspan(start, end, color=color, alpha=0.75, zorder=0)
        midpoint = start + (end - start) / 2
        axis.text(
            midpoint,
            1.32,
            window["id"].replace("narrative_window_", "secondary lead "),
            ha="center",
            va="center",
            fontsize=8,
            color="#444444",
        )

    legend_seen: set[str] = set()
    styles = {
        "primary_double_ring_wind_structure": (
            "*", "#b23a48", 95, 1.08, "primary double-ring scene"
        ),
        "primary_structurally_eligible_no_operator_trigger": (
            "o", "#2a6f97", 42, 1.00, "eligible, no double-ring trigger"
        ),
        "primary_indeterminate_quality_or_spatial_coverage": (
            "x", "#6c757d", 50, 0.92, "indeterminate coverage/quality"
        ),
    }
    for record in passes:
        marker, color, size, y_value, label = styles[record["evidence_class"]]
        axis.scatter(
            parse_utc(record["valid_at_utc"]),
            y_value,
            marker=marker,
            color=color,
            s=size,
            linewidths=1.5,
            label=label if label not in legend_seen else None,
            zorder=3,
        )
        legend_seen.add(label)

    axis.set_ylim(0.72, 1.43)
    axis.set_yticks([1.0])
    axis.set_yticklabels(["CyclObs SAR"])
    axis.set_xlabel("Valid time (UTC)")
    axis.set_title("Bavi primary SAR coverage vs secondary narrative windows")
    axis.xaxis.set_major_locator(mdates.DayLocator())
    axis.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    axis.grid(axis="x", color="#dddddd", linewidth=0.7)
    axis.legend(
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.19),
        ncol=3,
        fontsize=8,
    )
    tcprimed_status = (
        f"TC PRIMED WP09: {tcprimed_file_count} preliminary files"
        if tcprimed_file_count
        else "TC PRIMED WP09: no preliminary files at retrieval time"
    )
    axis.text(
        0.01,
        0.04,
        tcprimed_status,
        transform=axis.transAxes,
        fontsize=8,
        color="#555555",
    )
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    cyclobs_path = ROOT / config["cyclobs"]["input_path"]
    cyclobs_audit = json.loads(cyclobs_path.read_text(encoding="utf-8"))
    passes = extract_cyclobs_passes(
        cyclobs_audit,
        maximum_center_quality_exclusive=float(
            config["cyclobs"]["maximum_center_quality_exclusive"]
        ),
    )
    intervals = observed_dual_ring_intervals(passes)
    windows = classify_narrative_windows(
        passes,
        config["event_windows"],
        maximum_sufficient_sampling_gap_hours=float(
            config["cyclobs"]["maximum_sufficient_sampling_gap_hours"]
        ),
        operator_detection_rate_available=bool(
            config["cyclobs"]["validated_erc_detection_rate_available"]
        ),
    )
    tcprimed_keys, tcprimed_evidence = fetch_tcprimed_keys(config)
    tally = evidence_tally(intervals, windows)
    payload = {
        "report_id": "bavi-primary-erc-coverage-correction-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "retrospective-primary-coverage-audit",
        "authoritative_forecast": False,
        "primary_evidence": {
            "cyclobs": {
                "evidence_class": config["cyclobs"]["evidence_class"],
                "scene_count": len(passes),
                "quality_eligible_scene_count": sum(
                    record["quality_eligible"] for record in passes
                ),
                "first_scene_utc": passes[0]["valid_at_utc"],
                "last_scene_utc": passes[-1]["valid_at_utc"],
                "passes": passes,
                "observed_double_ring_intervals": intervals,
            },
            "tc_primed": {
                "evidence_class_if_files_exist": config["tc_primed"][
                    "evidence_class_if_files_exist"
                ],
                "storm_prefix": config["tc_primed"]["storm_prefix"],
                "file_count": len(tcprimed_keys),
                "keys": tcprimed_keys,
                "coverage_status": (
                    "primary_files_available"
                    if tcprimed_keys
                    else "no_wp09_primary_files_at_retrieval_time"
                ),
                "source_evidence": tcprimed_evidence,
            },
            "narrative_window_audit": windows,
            "required_tally": tally,
        },
        "secondary_narrative": {
            "source": config["secondary_narrative_source"],
            "other_sources": config["secondary_sources"],
            "role": "hypothesis_windows_only; excluded from primary tally",
        },
        "source_integrity": {
            "config_path": str(args.config.resolve()),
            "config_sha256": file_sha256(args.config),
            "cyclobs_input_path": str(cyclobs_path.resolve()),
            "cyclobs_input_sha256": file_sha256(cyclobs_path),
            "implementation_sha256": file_sha256(Path(__file__).resolve()),
        },
        "evidence_rule": (
            "Primary observations can confirm scene-level structure or remain "
            "indeterminate. Physical absence requires sufficient sampling and a "
            "validated observation operator. Secondary narrative is a lead only."
        ),
    }

    json_path = args.output_dir / "bavi_erc_coverage_audit.json"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pass_csv = args.output_dir / "cyclobs_scene_evidence.csv"
    write_csv(pass_csv, passes)
    window_csv = args.output_dir / "narrative_window_coverage.csv"
    write_csv(window_csv, windows)
    figure_path = args.output_dir / "bavi_primary_coverage_timeline.png"
    plot_coverage(passes, windows, len(tcprimed_keys), figure_path)
    manifest = {
        path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
        for path in (json_path, pass_csv, window_csv, figure_path)
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
