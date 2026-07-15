#!/usr/bin/env python3
"""Run the preregistered zero-label intensity-event and ERC-resource audit."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from typhoon_markov.intensity_events import (
    BOOTSTRAP_REPLICATES,
    build_five_point_windows,
    file_sha256,
    load_event_source,
    run_event_benchmark,
)
from typhoon_markov.published_erc import (
    audit_resource_registry,
    extract_pdf_text,
    parse_kuo_ce_table,
)
from typhoon_markov.structure_series import (
    build_cyclobs_structure_series,
    observed_dual_ring_intervals,
)


DEFAULT_IBTRACS = (
    ROOT.parent
    / "ibtracs-agency-disagreement"
    / "data"
    / "raw"
    / "ibtracs.WP.list.v04r01.csv"
)
DEFAULT_BAVI = ROOT / "outputs" / "bavi_2026_cyclobs_structure_audit.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "c_branch"
DEFAULT_PUBLISHED_DATA = ROOT / "data" / "published"
THRESHOLDS_MS = (2.5, 5.0, 7.5)
SUBSETS = ("all_tropical", "intense", "intense_over_ocean")


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )


def plot_reliability(table: pd.DataFrame, output_path: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9.4, 4.8), constrained_layout=True)
    colors = {"climatology": "#157a6e", "persistence": "#c44e3b"}
    markers = {"climatology": "o", "persistence": "s"}
    common = table.loc[table["predicted_probability"].lt(0.01)]
    for model in ("climatology", "persistence"):
        data = common.loc[common["model"].eq(model)].sort_values("predicted_probability")
        axes[0].plot(
            data["predicted_probability"],
            data["observed_rate"],
            color=colors[model],
            marker=markers[model],
            linewidth=1.6,
            markersize=6,
            label=model.capitalize(),
        )
    common_min = min(common["predicted_probability"].min(), common["observed_rate"].min())
    common_max = max(common["predicted_probability"].max(), common["observed_rate"].max())
    padding = (common_max - common_min) * 0.18
    lower, upper = common_min - padding, common_max + padding
    axes[0].plot((lower, upper), (lower, upper), color="#555555", linestyle="--", linewidth=1)
    axes[0].set(xlim=(lower, upper), ylim=(lower, upper))
    axes[0].set_title("Common state and fold climatology")
    axes[0].legend(frameon=False)

    rare = table.loc[table["predicted_probability"].ge(0.01)].sort_values(
        "predicted_probability"
    )
    axes[1].scatter(
        rare["predicted_probability"],
        rare["observed_rate"],
        color=colors["persistence"],
        marker=markers["persistence"],
        s=55,
        zorder=3,
    )
    for row in rare.itertuples():
        axes[1].annotate(
            f"n={row.rows}",
            (row.predicted_probability, row.observed_rate),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            fontsize=8,
            color=colors["persistence"],
        )
    axes[1].plot((0.03, 0.05), (0.03, 0.05), color="#555555", linestyle="--", linewidth=1)
    axes[1].set(xlim=(0.03, 0.05), ylim=(-0.002, 0.052))
    axes[1].xaxis.set_major_locator(mticker.FixedLocator((0.03, 0.035, 0.04, 0.045, 0.05)))
    axes[1].xaxis.set_major_formatter(mticker.FormatStrFormatter("%.3f"))
    axes[1].set_title("Past-pattern persistence stratum")

    for axis in axes:
        axis.set_xlabel("Out-of-fold predicted probability")
        axis.grid(color="#d9d9d9", linewidth=0.7)
    axes[0].set_ylabel("Observed event rate")
    fig.suptitle("12 h drop-then-rise event reliability, 5 m/s threshold")
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def plot_bavi_structure(
    series: pd.DataFrame,
    intervals: list[dict[str, Any]],
    output_path: Path,
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(8.6, 8.0), sharex=True, constrained_layout=True)
    fields = (
        ("axisymmetric_maximum_wind_ms", "Axisymmetric max wind (m/s)"),
        ("axisymmetric_maximum_radius_km", "Radius of axisymmetric max (km)"),
        ("v_times_r_1e6_m2s", "V x R (10^6 m^2/s)"),
    )
    for axis, (field, label) in zip(axes, fields):
        axis.plot(series["time"], series[field], color="#355070", linewidth=1.3, alpha=0.8)
        ordinary = series["quality_eligible"] & ~series["dual_peak_candidate"] & ~series["subthreshold_pair"]
        axis.scatter(
            series.loc[ordinary, "time"],
            series.loc[ordinary, field],
            color="#355070",
            s=28,
            zorder=3,
            label="quality-eligible single peak" if axis is axes[0] else None,
        )
        ineligible = ~series["quality_eligible"]
        axis.scatter(
            series.loc[ineligible, "time"],
            series.loc[ineligible, field],
            color="#9b9b9b",
            marker="x",
            s=30,
            zorder=3,
            label="limited eye/center quality" if axis is axes[0] else None,
        )
        dual = series["dual_peak_candidate"]
        axis.scatter(
            series.loc[dual, "time"],
            series.loc[dual, field],
            color="#c44e3b",
            edgecolor="white",
            linewidth=0.7,
            s=55,
            zorder=4,
            label="dual-peak overpass" if axis is axes[0] else None,
        )
        shoulder = series["subthreshold_pair"]
        axis.scatter(
            series.loc[shoulder, "time"],
            series.loc[shoulder, field],
            facecolor="#f4c95d",
            edgecolor="#5f4b00",
            marker="^",
            s=52,
            zorder=4,
            label="subthreshold two-shoulder pass" if axis is axes[0] else None,
        )
        for interval in intervals:
            axis.axvspan(
                pd.Timestamp(interval["start_utc"]),
                pd.Timestamp(interval["end_utc"]),
                color="#e78f8e",
                alpha=0.16,
                linewidth=0,
            )
        axis.set_ylabel(label)
        axis.grid(color="#dedede", linewidth=0.7)

    for row in series.loc[series["dual_peak_candidate"]].itertuples():
        axes[1].annotate(
            f"{row.inner_peak_radius_km:.0f}/{row.outer_peak_radius_km:.0f} km",
            (row.time, row.axisymmetric_maximum_radius_km),
            xytext=(0, 9),
            textcoords="offset points",
            ha="center",
            fontsize=7,
            color="#8a2d24",
        )
    july11 = series.loc[
        series["time"].dt.strftime("%Y-%m-%d %H:%M").eq("2026-07-11 09:52")
    ]
    if len(july11) == 1:
        row = july11.iloc[0]
        axes[1].annotate(
            f"threshold-low shoulders\n{row['inner_peak_radius_km']:.0f}/{row['outer_peak_radius_km']:.0f} km",
            (row["time"], row["axisymmetric_maximum_radius_km"]),
            xytext=(-78, -38),
            textcoords="offset points",
            arrowprops={"arrowstyle": "->", "color": "#5f4b00", "linewidth": 0.8},
            fontsize=8,
            color="#5f4b00",
        )
    axes[0].legend(loc="upper left", frameon=False, fontsize=8, ncol=2)
    axes[0].set_title(
        "BAVI (WP09/2026): CyclObs SAR V x R and observed wind-ring structure"
    )
    axes[-1].xaxis.set_major_locator(mdates.DayLocator(interval=1))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    axes[-1].set_xlabel("Valid time (UTC)")
    axes[-1].text(
        0.015,
        0.96,
        "Four dual-peak overpasses form one observed interval; sampling gaps remain unobserved.",
        transform=axes[-1].transAxes,
        ha="left",
        va="top",
        fontsize=8,
        color="#444444",
    )
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ibtracs", type=Path, default=DEFAULT_IBTRACS)
    parser.add_argument("--bavi-audit", type=Path, default=DEFAULT_BAVI)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--published-data-dir", type=Path, default=DEFAULT_PUBLISHED_DATA)
    parser.add_argument("--bootstrap-replicates", type=int, default=BOOTSTRAP_REPLICATES)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    resource_audit = audit_resource_registry(
        ROOT / "config" / "published_erc_resources.json",
        cache_directory=args.published_data_dir,
    )
    kuo_pdf = args.published_data_dir / "CEdataWPAC9706.pdf"
    if not kuo_pdf.exists():
        raise RuntimeError("Kuo et al. published CE table PDF could not be retrieved")
    kuo_rows = parse_kuo_ce_table(extract_pdf_text(kuo_pdf))
    if len(kuo_rows) != 62:
        raise RuntimeError(f"expected 62 Kuo CE cases, parsed {len(kuo_rows)}")
    kuo_frame = pd.DataFrame(kuo_rows)
    kuo_frame.to_csv(output / "kuo_2009_wpac_ce_cases.csv", index=False)
    resource_audit["kuo_table_parse"] = {
        "pdf_path": str(kuo_pdf.resolve()),
        "pdf_sha256": file_sha256(kuo_pdf),
        "parsed_rows": len(kuo_rows),
        "unique_tropical_cyclones": int(kuo_frame["tc_number"].nunique()),
        "semantic_class": "concentric-eyewall formation cases; not ERC onset labels",
        "csv_path": str((output / "kuo_2009_wpac_ce_cases.csv").resolve()),
    }
    save_json(output / "published_erc_resource_audit.json", resource_audit)

    source, source_audit = load_event_source(args.ibtracs)
    windows = build_five_point_windows(source)
    summaries: list[dict[str, Any]] = []
    primary_predictions: pd.DataFrame | None = None
    primary_reliability: pd.DataFrame | None = None
    for threshold in THRESHOLDS_MS:
        for subset in SUBSETS:
            benchmark = run_event_benchmark(
                windows,
                threshold_ms=threshold,
                subset_name=subset,
                bootstrap_replicates=args.bootstrap_replicates,
            )
            summaries.append(benchmark.summary)
            if threshold == 5.0 and subset == "all_tropical":
                primary_predictions = benchmark.predictions
                primary_reliability = benchmark.reliability
    if primary_predictions is None or primary_reliability is None:
        raise RuntimeError("primary event scenario was not generated")
    primary_predictions.to_csv(output / "event_rows_primary.csv", index=False)
    primary_reliability.to_csv(output / "event_reliability_primary.csv", index=False)
    event_report = {
        "report_id": "c-branch-zero-label-intensity-event-benchmark-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "research-baseline",
        "authoritative_forecast": False,
        "label_semantics": (
            "automatic future-12-hour intensity drop-then-rise waveform; "
            "no causal ERC label"
        ),
        "preregistration": {
            "path": str((ROOT / "docs" / "c-branch-preregistration.md").resolve()),
            "sha256": file_sha256(ROOT / "docs" / "c-branch-preregistration.md"),
        },
        "source_audit": source_audit,
        "five_point_windows": {
            "rows": int(len(windows)),
            "storms": int(windows["SID"].nunique()),
        },
        "scenarios": summaries,
        "interpretation_limits": [
            "The label measures a best-track intensity waveform and leaves physical causation unassigned.",
            "USA_WIND is a post-season best-track estimate with a 1-minute averaging window.",
            "Storm-block intervals address serial dependence within storms; best-track analysis uncertainty remains unobserved.",
        ],
    }
    save_json(output / "intensity_event_benchmark.json", event_report)
    plot_reliability(primary_reliability, output / "event_reliability_primary.png")

    bavi_audit = json.loads(args.bavi_audit.read_text(encoding="utf-8"))
    bavi_series = build_cyclobs_structure_series(bavi_audit)
    intervals = observed_dual_ring_intervals(bavi_series)
    bavi_series.to_csv(output / "bavi_cyclobs_vr_series.csv", index=False)
    bavi_summary = {
        "report_id": "bavi-cyclobs-vr-retrospective-v1",
        "status": "retrospective-structure-observation",
        "authoritative_forecast": False,
        "source_path": str(args.bavi_audit.resolve()),
        "source_sha256": file_sha256(args.bavi_audit),
        "overpasses": int(len(bavi_series)),
        "quality_eligible_overpasses": int(bavi_series["quality_eligible"].sum()),
        "dual_peak_overpasses": int(bavi_series["dual_peak_candidate"].sum()),
        "subthreshold_two_shoulder_overpasses": int(bavi_series["subthreshold_pair"].sum()),
        "observed_dual_ring_interval_count": len(intervals),
        "observed_dual_ring_intervals": intervals,
        "interpretation": (
            "The audited SAR sequence supports the reported number of observed dual-ring "
            "intervals; it does not fill temporal sampling gaps or label completed ERCs."
        ),
    }
    save_json(output / "bavi_cyclobs_vr_summary.json", bavi_summary)
    plot_bavi_structure(bavi_series, intervals, output / "bavi_cyclobs_vr_timeline.png")

    manifest = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
    }
    save_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    main()
