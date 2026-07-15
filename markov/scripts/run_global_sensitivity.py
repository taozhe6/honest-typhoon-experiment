#!/usr/bin/env python3
"""Run the preregistered FAST fixed-constant sensitivity audit."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from typhoon_markov.sensitivity import run_sensitivity


DEFAULT_CONFIG = ROOT / "config" / "global_sensitivity.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "global_sensitivity"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def comparison_rows(results: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in results["scenarios"]:
        for engine_name, engine in scenario["engines"].items():
            for variant in engine["variants"]:
                comparison = variant["comparison_to_baseline"]
                rows.append(
                    {
                        "scenario": scenario["id"],
                        "engine": engine_name,
                        "variant": variant["id"],
                        "type": variant["type"],
                        "final_wind_delta_ms": comparison["final_delta"]["wind_ms"],
                        "final_moisture_delta": comparison["final_delta"]["core_moisture"],
                        "final_pressure_delta_hpa": comparison["final_delta"][
                            "central_pressure_hpa"
                        ],
                        "final_rmw_delta_km": comparison["final_delta"]["rmw_km"],
                        "max_wind_delta_ms": comparison["max_abs_trajectory_delta"][
                            "wind_ms"
                        ],
                        "max_pressure_delta_hpa": comparison[
                            "max_abs_trajectory_delta"
                        ]["central_pressure_hpa"],
                        "max_rmw_delta_km": comparison["max_abs_trajectory_delta"][
                            "rmw_km"
                        ],
                        "regime_mismatch_steps": comparison["regime_mismatch_steps"],
                        "max_transition_probability_l1_delta": comparison[
                            "max_transition_probability_l1_delta"
                        ],
                    }
                )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot_fixed_regime_wind(results: dict[str, Any], output_path: Path) -> None:
    scenarios = results["scenarios"]
    fig, axes = plt.subplots(
        len(scenarios), 1, figsize=(8.8, 8.4), sharex=True, constrained_layout=True
    )
    colors = {
        "Ck_minus_30pct": "#397367",
        "Ck_plus_30pct": "#0b6e4f",
        "boundary_layer_depth_minus_30pct": "#c44e3b",
        "boundary_layer_depth_plus_30pct": "#e07a5f",
        "fast_kappa_minus_30pct": "#355070",
        "fast_kappa_plus_30pct": "#6d597a",
        "Ck_and_depth_minus_30pct": "#777777",
        "Ck_and_depth_plus_30pct": "#aaaaaa",
    }
    labels = {
        "Ck_minus_30pct": "Ck -30%",
        "Ck_plus_30pct": "Ck +30%",
        "boundary_layer_depth_minus_30pct": "h -30%",
        "boundary_layer_depth_plus_30pct": "h +30%",
        "fast_kappa_minus_30pct": "kappa -30%",
        "fast_kappa_plus_30pct": "kappa +30%",
        "Ck_and_depth_minus_30pct": "Ck,h -30%",
        "Ck_and_depth_plus_30pct": "Ck,h +30%",
    }
    for axis, scenario in zip(axes, scenarios):
        engine = scenario["engines"]["fixed_regime"]
        baseline = engine["baseline"]["trajectory"]
        hours = [step["valid_hour"] for step in baseline]
        baseline_wind = [step["state"]["wind_ms"] for step in baseline]
        for variant in engine["variants"]:
            delta = [
                step["state"]["wind_ms"] - base
                for step, base in zip(variant["trajectory"], baseline_wind)
            ]
            structural = variant["type"] == "ratio_preserving_structural_control"
            axis.plot(
                hours,
                delta,
                color=colors[variant["id"]],
                linestyle="--" if structural else "-",
                linewidth=1.5,
                marker="o",
                markersize=3.5,
                label=labels[variant["id"]],
            )
        axis.axhline(0.0, color="#333333", linewidth=0.8)
        axis.set_ylabel("Delta wind (m/s)")
        axis.set_title(scenario["id"].replace("_", " "))
        axis.grid(color="#dddddd", linewidth=0.7)
    axes[0].legend(frameon=False, ncol=4, fontsize=8, loc="best")
    axes[-1].set_xlabel("Integration hour")
    fig.suptitle("FAST fixed-regime wind sensitivity to fixed constants (+/-30%)")
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
    results = run_sensitivity(config)
    payload = {
        "report_id": "fast-fixed-constant-sensitivity-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "synthetic-structural-audit",
        "authoritative_forecast": False,
        "model_status": "research-rejected",
        "config": {
            "path": str(args.config.resolve()),
            "sha256": file_sha256(args.config),
        },
        "upstream_fast_source": config["upstream_fast_source"],
        **results,
        "interpretation_limits": [
            "All three scenarios are synthetic and cannot estimate real forecast error.",
            "The Markov parameters remain unfitted; full-engine regime changes are structural probes.",
            "One-at-a-time perturbations measure local dependence around the cited constants.",
        ],
    }
    json_path = args.output_dir / "global_sensitivity.json"
    save_json(json_path, payload)
    rows = comparison_rows(results)
    csv_path = args.output_dir / "global_sensitivity_summary.csv"
    write_csv(csv_path, rows)
    figure_path = args.output_dir / "global_sensitivity_wind.png"
    plot_fixed_regime_wind(results, figure_path)
    manifest = {
        path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
        for path in (json_path, csv_path, figure_path)
    }
    save_json(args.output_dir / "manifest.json", manifest)


if __name__ == "__main__":
    main()
