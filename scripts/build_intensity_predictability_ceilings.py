#!/usr/bin/env python3
"""Build the cross-project evidence package for intensity predictability limits."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "intensity_predictability_ceilings"
SOURCES = {
    "independent_truth": ROOT
    / "ibtracs-agency-disagreement"
    / "outputs"
    / "b_branch"
    / "independent_truth_error_table.csv",
    "pairwise_s088": ROOT
    / "ibtracs-agency-disagreement"
    / "outputs"
    / "pairwise_disagreement_S088.csv",
    "pairwise_s093": ROOT
    / "ibtracs-agency-disagreement"
    / "outputs"
    / "pairwise_disagreement_S093.csv",
    "five_agency_neff": ROOT
    / "ibtracs-agency-disagreement"
    / "outputs"
    / "neff_sensitivity.json",
    "observation_dimension": ROOT
    / "markov"
    / "outputs"
    / "ibtracs_observation_audit_2001_2024.json",
    "path_round_v2": ROOT
    / "path-track-benchmark"
    / "outputs"
    / "round_v2"
    / "correlation_neff.json",
    "path_round_v3": ROOT
    / "path-track-benchmark"
    / "outputs"
    / "round_v3"
    / "correlation_neff.json",
    "global_sensitivity": ROOT
    / "markov"
    / "outputs"
    / "global_sensitivity"
    / "global_sensitivity.json",
    "theta_propagation": ROOT
    / "markov"
    / "outputs"
    / "theta_propagation"
    / "theta_propagation.json",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def pairwise_range(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    agencies = [key for key in rows[0] if key]
    values: list[float] = []
    for row in rows:
        row_agency = row[""]
        for column_agency in agencies:
            if row_agency < column_agency:
                values.append(float(row[column_agency]))
    if len(values) != 10:
        raise RuntimeError(f"expected 10 agency pairs in {path}")
    return {
        "pair_count": len(values),
        "minimum_raw_ms": min(values),
        "maximum_raw_ms": max(values),
        "minimum_reported_integer_ms": round(min(values)),
        "maximum_reported_integer_ms": round(max(values)),
    }


def truth_coverage(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    event_counts = {int(row["landfall_events"]) for row in rows}
    matched_counts = {
        int(row["matched_independent_truth_events"]) for row in rows
    }
    if len(rows) != 5 or len(event_counts) != 1 or len(matched_counts) != 1:
        raise RuntimeError("independent-truth table has inconsistent agency coverage")
    return {
        "agency_count": len(rows),
        "landfall_events": event_counts.pop(),
        "matched_independent_truth_events": matched_counts.pop(),
        "agencies": [row["agency"] for row in rows],
        "status_by_agency": {row["agency"]: row["status"] for row in rows},
    }


def fixed_regime_sensitivity(global_result: dict[str, Any]) -> dict[str, Any]:
    rows = {
        row["variant"]: row["max_across_scenarios"]
        for row in global_result["summary"]
        if row["engine"] == "fixed_regime"
    }
    wanted = {
        "Ck_minus_30pct": "Ck -30%",
        "Ck_plus_30pct": "Ck +30%",
        "boundary_layer_depth_minus_30pct": "h -30%",
        "boundary_layer_depth_plus_30pct": "h +30%",
        "fast_kappa_minus_30pct": "kappa -30%",
        "fast_kappa_plus_30pct": "kappa +30%",
    }
    missing = set(wanted) - set(rows)
    if missing:
        raise RuntimeError(f"missing fixed-regime sensitivity rows: {sorted(missing)}")
    values = {
        label: rows[variant]["absolute_final_wind_delta_ms"]
        for variant, label in wanted.items()
    }
    return {
        "absolute_final_wind_delta_ms": values,
        "family_maximum_ms": {
            "Ck": max(values["Ck -30%"], values["Ck +30%"]),
            "h": max(values["h -30%"], values["h +30%"]),
            "kappa": max(
                values["kappa -30%"], values["kappa +30%"]
            ),
        },
        "ratio_invariance": global_result["structural_checks"][
            "Ck_over_h_ratio_invariance"
        ],
    }


def extract_evidence() -> dict[str, Any]:
    five_neff = load_json(SOURCES["five_agency_neff"])
    observation = load_json(SOURCES["observation_dimension"])
    path_v2 = load_json(SOURCES["path_round_v2"])
    path_v3 = load_json(SOURCES["path_round_v3"])
    global_result = load_json(SOURCES["global_sensitivity"])
    theta = load_json(SOURCES["theta_propagation"])
    observation_wp = observation["subsets"]["jtwc_wp_only"]
    path_v3_primary = path_v3["primary"]
    return {
        "truth_ceiling": {
            "evidence_label": "MEASURED",
            **truth_coverage(SOURCES["independent_truth"]),
            "identifiable_outputs": [],
            "unidentifiable_outputs": [
                "agency landfall bias against independent truth",
                "agency landfall MAE against independent truth",
                "agency landfall RMSE against independent truth",
                "correlation of agency errors against independent truth",
            ],
        },
        "redundancy_ceiling": {
            "agency_pairwise_disagreement": {
                "evidence_label": "ASSUMED_TO_MEASURED",
                "wind_average_window_minutes": 10,
                "JTWC_1min_to_10min_0.88": pairwise_range(
                    SOURCES["pairwise_s088"]
                ),
                "JTWC_1min_to_10min_0.93": pairwise_range(
                    SOURCES["pairwise_s093"]
                ),
            },
            "five_agency_intensity_neff": {
                "evidence_label": "ASSUMED_TO_MEASURED",
                "status": "unidentifiable",
                "reason": five_neff["algebraic_constraint"],
                "algebraic_point_range": [
                    five_neff["sensitivity_envelope"]["neff_point_min"],
                    five_neff["sensitivity_envelope"]["neff_point_max"],
                ],
                "number_of_agencies": 5,
            },
            "separate_redundancy_diagnostics": {
                "JTWC_V_Pc_RMW_participation_ratio": {
                    "evidence_label": "MEASURED",
                    "value": observation_wp["effective_dimensions"][
                        "participation_ratio"
                    ],
                    "record_count": observation_wp["record_count"],
                    "storm_count": observation_wp["storm_count"],
                    "construct": "cross-channel effective dimension of USA_WIND, USA_PRES, USA_RMW",
                    "ci95": None,
                },
                "path_CMC_NGX_round_v2_neff": {
                    "evidence_label": "ASSUMED_TO_MEASURED",
                    "value": path_v2["primary"]["neff"],
                    "ci95": path_v2["primary"]["neff_ci95"],
                    "record_count": path_v2["primary"]["record_count"],
                    "storm_count": path_v2["primary"]["storm_count"],
                    "construct": "exchangeable two-stream lead-centered radial track-error n_eff",
                },
                "path_LOCAL_EQ2_UKM_round_v3_neff": {
                    "evidence_label": "ASSUMED_TO_MEASURED",
                    "value": path_v3_primary["neff_local_eq2_ukm"],
                    "ci95": path_v3_primary["neff_local_eq2_ukm_ci95"],
                    "record_count": path_v3_primary["record_count"],
                    "storm_count": path_v3_primary["storm_count"],
                    "construct": "exchangeable two-stream lead-centered radial track-error n_eff",
                },
                "path_round_v3_increment": {
                    "evidence_label": "MEASURED",
                    "value": path_v3_primary["delta_neff"],
                    "ci95": path_v3_primary["delta_neff_ci95"],
                    "construct": "same-bootstrap difference between LOCAL_EQ2-UKM and CMC-NGX path n_eff",
                },
            },
            "correction": {
                "claim_status": "retracted",
                "retracted_claim": "1.46, 1.47, and 1.46 are three independent replications",
                "reason": "the two 1.46 references are the same observation audit, while 1.47 is a path-error construct; round-v2 and round-v3 path samples also overlap",
            },
        },
        "structural_ceiling": {
            "theta_final_state_propagation": {
                "evidence_label": "ASSUMED_TO_MEASURED",
                "theta_definition": theta["theta_definition"],
                "theta_multiplier_range": [
                    theta["theta_multipliers"][0],
                    theta["theta_multipliers"][-1],
                ],
                "grid_points": len(theta["theta_multipliers"]),
                "comparison_hour": theta["rows"][0]["comparison_hour"],
                **theta["cross_scenario"],
                "equivalent_parameterizations_passed": theta[
                    "structural_checks"
                ]["equivalent_parameterizations"]["passed"],
                "uncertainty_semantics": theta["uncertainty_semantics"],
            },
            "individual_constant_sensitivity": fixed_regime_sensitivity(
                global_result
            ),
            "comparison_scope": "numerical scale comparison only; agency disagreement and synthetic parameter response have different statistical semantics",
        },
    }


def validate_evidence(evidence: dict[str, Any]) -> None:
    truth = evidence["truth_ceiling"]
    if truth["landfall_events"] != 108:
        raise RuntimeError("expected 108 complete-five landfall events")
    if truth["matched_independent_truth_events"] != 0:
        raise RuntimeError("independent truth coverage changed; re-audit conclusions")

    redundancy = evidence["redundancy_ceiling"]
    if redundancy["five_agency_intensity_neff"]["status"] != "unidentifiable":
        raise RuntimeError("five-agency intensity n_eff gate unexpectedly changed")
    s088 = redundancy["agency_pairwise_disagreement"][
        "JTWC_1min_to_10min_0.88"
    ]
    s093 = redundancy["agency_pairwise_disagreement"][
        "JTWC_1min_to_10min_0.93"
    ]
    if [s088["minimum_reported_integer_ms"], s088["maximum_reported_integer_ms"]] != [2, 5]:
        raise RuntimeError("S088 displayed disagreement range changed")
    if [s093["minimum_reported_integer_ms"], s093["maximum_reported_integer_ms"]] != [2, 6]:
        raise RuntimeError("S093 displayed disagreement range changed")

    structural = evidence["structural_ceiling"]
    theta = structural["theta_final_state_propagation"]
    if not theta["equivalent_parameterizations_passed"]:
        raise RuntimeError("theta equivalent-parameterization check failed")
    ck_minus = structural["individual_constant_sensitivity"][
        "absolute_final_wind_delta_ms"
    ]["Ck -30%"]
    if not math.isclose(
        theta["maximum_baseline_centered_absolute_delta_ms"],
        ck_minus,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise RuntimeError("theta endpoint disagrees with frozen Ck sensitivity")


def source_provenance() -> dict[str, Any]:
    return {
        name: {
            "path": str(path.relative_to(ROOT)),
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for name, path in SOURCES.items()
    }


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_figure(evidence: dict[str, Any], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(
        3, 1, figsize=(10.5, 10.8), constrained_layout=True
    )

    truth = evidence["truth_ceiling"]
    axes[0].barh(
        [0],
        [truth["landfall_events"]],
        height=0.5,
        color="#e7e9ec",
        edgecolor="#616b75",
    )
    axes[0].scatter(
        [truth["matched_independent_truth_events"]],
        [0],
        color="#b23a48",
        s=75,
        zorder=3,
    )
    axes[0].text(
        truth["landfall_events"] / 2,
        0,
        "Independent truth coverage: 0 / 108 landfalls",
        ha="center",
        va="center",
        fontsize=11,
        weight="bold",
    )
    axes[0].set_xlim(-3, 112)
    axes[0].set_yticks([])
    axes[0].set_xlabel("Complete-five landfall events")
    axes[0].set_title("1. Verification ceiling", loc="left", weight="bold")

    diagnostics = evidence["redundancy_ceiling"][
        "separate_redundancy_diagnostics"
    ]
    rows = [
        (
            "Intensity V/Pc/RMW\neffective dimension",
            diagnostics["JTWC_V_Pc_RMW_participation_ratio"],
            "#2a7185",
        ),
        (
            "Path CMC+NGX\nn_eff (round v2)",
            diagnostics["path_CMC_NGX_round_v2_neff"],
            "#9a4d3d",
        ),
        (
            "Path local consensus+UKM\nn_eff (round v3)",
            diagnostics["path_LOCAL_EQ2_UKM_round_v3_neff"],
            "#397a52",
        ),
    ]
    for y, (label, item, color) in enumerate(rows):
        ci = item["ci95"]
        error = None
        if ci is not None:
            error = [[item["value"] - ci[0]], [ci[1] - item["value"]]]
        axes[1].errorbar(
            item["value"],
            y,
            xerr=error,
            fmt="o",
            color=color,
            ecolor=color,
            capsize=4,
            markersize=7,
        )
        axes[1].text(1.02, y, label, ha="left", va="center", fontsize=9)
    axes[1].set_xlim(1.0, 2.02)
    axes[1].set_ylim(-0.7, len(rows) - 0.3)
    axes[1].set_yticks([])
    axes[1].set_xlabel("Effective dimension / assumed two-stream n_eff")
    axes[1].set_title(
        "2. Redundancy diagnostics: different constructs, not replications",
        loc="left",
        weight="bold",
    )
    axes[1].text(
        2.0,
        2.25,
        "Five-agency intensity n_eff: unidentifiable",
        ha="right",
        va="center",
        color="#b23a48",
        fontsize=9,
    )
    axes[1].grid(axis="x", color="#d9d9d9", linewidth=0.7)

    pairwise = evidence["redundancy_ceiling"]["agency_pairwise_disagreement"]
    theta = evidence["structural_ceiling"]["theta_final_state_propagation"]
    kappa = evidence["structural_ceiling"]["individual_constant_sensitivity"][
        "family_maximum_ms"
    ]["kappa"]
    interval_rows = [
        (
            "Agency disagreement S088",
            pairwise["JTWC_1min_to_10min_0.88"]["minimum_reported_integer_ms"],
            pairwise["JTWC_1min_to_10min_0.88"]["maximum_reported_integer_ms"],
            "#2a7185",
        ),
        (
            "Agency disagreement S093",
            pairwise["JTWC_1min_to_10min_0.93"]["minimum_reported_integer_ms"],
            pairwise["JTWC_1min_to_10min_0.93"]["maximum_reported_integer_ms"],
            "#507dbc",
        ),
        (
            "theta +/-30% max centered |delta V|",
            0.0,
            theta["maximum_baseline_centered_absolute_delta_ms"],
            "#9a4d3d",
        ),
        (
            "theta endpoint-to-endpoint width",
            0.0,
            theta["maximum_endpoint_to_endpoint_width_ms"],
            "#c17c38",
        ),
        ("kappa +/-30% max |delta V|", 0.0, kappa, "#397a52"),
    ]
    for y, (label, lower, upper, color) in enumerate(interval_rows):
        axes[2].plot([lower, upper], [y, y], color=color, linewidth=6, solid_capstyle="butt")
        axes[2].scatter([lower, upper], [y, y], color=color, s=28, zorder=3)
        axes[2].text(6.25, y, label, va="center", fontsize=9)
    axes[2].set_xlim(0, 10.3)
    axes[2].set_ylim(-0.7, len(interval_rows) - 0.3)
    axes[2].set_yticks([])
    axes[2].set_xlabel("m/s (scale comparison; statistical semantics differ)")
    axes[2].set_title(
        "3. Analysis spread and synthetic structural response",
        loc="left",
        weight="bold",
    )
    axes[2].grid(axis="x", color="#d9d9d9", linewidth=0.7)

    for axis in axes:
        axis.spines[["top", "right", "left"]].set_visible(False)
    fig.suptitle(
        "Western North Pacific intensity predictability: three evidence bottlenecks",
        fontsize=15,
        weight="bold",
    )
    fig.savefig(path, dpi=180, facecolor="white")
    plt.close(fig)


def build() -> None:
    evidence = extract_evidence()
    validate_evidence(evidence)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result = {
        "report_id": "western-north-pacific-intensity-predictability-ceilings-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "analysis_code_git_commit": git_head(),
        "qualification": "cross-project-evidence-synthesis; unvalidated for forecasting",
        "ceiling_semantics": "evidence bottlenecks, not theorem-level upper bounds on atmospheric predictability",
        "evidence": evidence,
        "sources": source_provenance(),
    }
    result_path = OUTPUT / "synthesis.json"
    figure_path = OUTPUT / "three_ceilings.png"
    save_json(result_path, result)
    build_figure(evidence, figure_path)
    manifest = {
        "generated_at_utc": result["generated_at_utc"],
        "analysis_code_git_commit": result["analysis_code_git_commit"],
        "outputs": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
            for path in (result_path, figure_path)
        },
    }
    save_json(OUTPUT / "manifest.json", manifest)


def check() -> None:
    result_path = OUTPUT / "synthesis.json"
    manifest_path = OUTPUT / "manifest.json"
    if not result_path.exists() or not manifest_path.exists():
        raise RuntimeError("ceiling synthesis outputs are missing")
    stored = load_json(result_path)
    current_evidence = extract_evidence()
    validate_evidence(current_evidence)
    if stored["evidence"] != current_evidence:
        raise RuntimeError("ceiling synthesis evidence is stale")
    if stored["sources"] != source_provenance():
        raise RuntimeError("ceiling synthesis source provenance is stale")
    manifest = load_json(manifest_path)
    for name, metadata in manifest["outputs"].items():
        path = OUTPUT / name
        if path.stat().st_size != metadata["bytes"]:
            raise RuntimeError(f"artifact byte count changed: {name}")
        if file_sha256(path) != metadata["sha256"]:
            raise RuntimeError(f"artifact hash changed: {name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.check:
        check()
    else:
        build()


if __name__ == "__main__":
    main()
