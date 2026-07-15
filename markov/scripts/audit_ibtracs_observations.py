#!/usr/bin/env python3
"""Audit dependence and quantization in IBTrACS V/Pc/RMW observations."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Sequence


CHANNELS = ("USA_WIND", "USA_PRES", "USA_RMW")


def correlation_matrix(rows: Sequence[Sequence[float]]) -> list[list[float]]:
    if len(rows) < 2:
        raise ValueError("at least two complete observations are required")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ValueError("observations must form a non-empty rectangular matrix")

    means = [sum(row[column] for row in rows) / len(rows) for column in range(width)]
    centered = [
        [row[column] - means[column] for column in range(width)] for row in rows
    ]
    sums_of_squares = [
        sum(row[column] ** 2 for row in centered) for column in range(width)
    ]
    if any(value <= 0.0 for value in sums_of_squares):
        raise ValueError("every observation channel must have non-zero variance")

    result = [[0.0] * width for _ in range(width)]
    for left in range(width):
        for right in range(width):
            covariance_sum = sum(row[left] * row[right] for row in centered)
            result[left][right] = covariance_sum / math.sqrt(
                sums_of_squares[left] * sums_of_squares[right]
            )
    return result


def symmetric_3x3_eigenvalues(matrix: Sequence[Sequence[float]]) -> list[float]:
    if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
        raise ValueError("a 3 by 3 symmetric matrix is required")
    if any(abs(matrix[i][j] - matrix[j][i]) > 1.0e-10 for i in range(3) for j in range(3)):
        raise ValueError("matrix must be symmetric")

    a11, a12, a13 = matrix[0]
    _, a22, a23 = matrix[1]
    _, _, a33 = matrix[2]
    off_diagonal_power = a12 * a12 + a13 * a13 + a23 * a23
    if off_diagonal_power == 0.0:
        return sorted((a11, a22, a33), reverse=True)

    mean = (a11 + a22 + a33) / 3.0
    spread = math.sqrt(
        (
            (a11 - mean) ** 2
            + (a22 - mean) ** 2
            + (a33 - mean) ** 2
            + 2.0 * off_diagonal_power
        )
        / 6.0
    )
    b11, b22, b33 = (a11 - mean) / spread, (a22 - mean) / spread, (a33 - mean) / spread
    b12, b13, b23 = a12 / spread, a13 / spread, a23 / spread
    determinant = (
        b11 * b22 * b33
        + 2.0 * b12 * b13 * b23
        - b11 * b23 * b23
        - b22 * b13 * b13
        - b33 * b12 * b12
    )
    angle = math.acos(max(-1.0, min(1.0, determinant / 2.0))) / 3.0
    largest = mean + 2.0 * spread * math.cos(angle)
    smallest = mean + 2.0 * spread * math.cos(angle + 2.0 * math.pi / 3.0)
    middle = 3.0 * mean - largest - smallest
    return sorted((largest, middle, smallest), reverse=True)


def effective_dimensions(eigenvalues: Sequence[float]) -> dict[str, float]:
    nonnegative = [max(0.0, value) for value in eigenvalues]
    total = sum(nonnegative)
    participation = total * total / sum(value * value for value in nonnegative)
    probabilities = [value / total for value in nonnegative if value > 0.0]
    entropy_rank = math.exp(-sum(value * math.log(value) for value in probabilities))
    return {
        "participation_ratio": participation,
        "entropy_effective_rank": entropy_rank,
    }


def parse_positive(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0.0 else None


def load_records(path: Path, start_year: int, end_year: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        next(reader)  # IBTrACS CSV files place a units row after the header.
        for row in reader:
            try:
                season = int(row["SEASON"])
            except ValueError:
                continue
            if not start_year <= season <= end_year or row["TRACK_TYPE"].strip() != "main":
                continue
            values = [parse_positive(row[channel].strip()) for channel in CHANNELS]
            if any(value is None for value in values):
                continue
            records.append(
                {
                    "sid": row["SID"],
                    "time": row["ISO_TIME"],
                    "agency": row["USA_AGENCY"].strip(),
                    "values": tuple(float(value) for value in values if value is not None),
                }
            )
    return records


def summarize(records: Sequence[dict[str, object]]) -> dict[str, object]:
    values = [record["values"] for record in records]
    matrix = correlation_matrix(values)  # type: ignore[arg-type]
    eigenvalues = symmetric_3x3_eigenvalues(matrix)
    rmw_values = [float(record["values"][2]) for record in records]  # type: ignore[index]

    previous: dict[str, float] = {}
    consecutive_pairs = 0
    unchanged_pairs = 0
    for record, rmw in zip(records, rmw_values):
        sid = str(record["sid"])
        if sid in previous:
            consecutive_pairs += 1
            unchanged_pairs += rmw == previous[sid]
        previous[sid] = rmw

    histogram = Counter(rmw_values)
    return {
        "record_count": len(records),
        "storm_count": len({str(record["sid"]) for record in records}),
        "correlation_matrix": matrix,
        "correlation_eigenvalues": eigenvalues,
        "effective_dimensions": effective_dimensions(eigenvalues),
        "rmw_quality_diagnostics": {
            "integer_fraction": sum(value.is_integer() for value in rmw_values) / len(rmw_values),
            "multiple_of_5_nm_fraction": sum(value % 5.0 == 0.0 for value in rmw_values)
            / len(rmw_values),
            "multiple_of_10_nm_fraction": sum(value % 10.0 == 0.0 for value in rmw_values)
            / len(rmw_values),
            "consecutive_unchanged_fraction": unchanged_pairs / consecutive_pairs,
            "consecutive_pair_count": consecutive_pairs,
            "top_values_nm": [
                {"value": value, "count": count}
                for value, count in histogram.most_common(20)
            ],
        },
    }


def select(records: Iterable[dict[str, object]], mode: str) -> list[dict[str, object]]:
    if mode == "all_complete":
        return list(records)
    if mode == "jtwc_wp_only":
        return [record for record in records if record["agency"] == "jtwc_wp"]
    if mode == "synoptic_6h":
        return [
            record
            for record in records
            if int(str(record["time"])[11:13]) % 6 == 0
        ]
    raise ValueError(f"unknown selection mode: {mode}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--start-year", type=int, default=2001)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    records = load_records(args.csv_path, args.start_year, args.end_year)
    report = {
        "dataset": {
            "name": "NOAA NCEI IBTrACS v04r01 Western Pacific CSV",
            "path": str(args.csv_path.resolve()),
            "bytes": args.csv_path.stat().st_size,
            "sha256": file_sha256(args.csv_path),
        },
        "selection": {
            "years": [args.start_year, args.end_year],
            "track_type": "main",
            "complete_positive_channels": list(CHANNELS),
        },
        "channel_order": list(CHANNELS),
        "subsets": {
            mode: summarize(select(records, mode))
            for mode in ("all_complete", "jtwc_wp_only", "synoptic_6h")
        },
        "interpretation_limits": [
            "Cross-channel effective dimension diagnoses redundancy; it does not by itself prove parameter non-identifiability.",
            "Parameter identifiability requires a real-data sensitivity or Fisher-information analysis with observation-error covariance.",
            "RMW quantization and persistence diagnose limited precision; provenance flags are required before treating RMW as an independent measurement.",
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
