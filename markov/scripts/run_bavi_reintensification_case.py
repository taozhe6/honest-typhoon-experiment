#!/usr/bin/env python3
"""Score agency-native Bavi evidence and audit model eligibility."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from typhoon_markov.case_validation import (
    audit_regime_wind_influence,
    evaluate_model_eligibility,
    score_native_channel,
)
from typhoon_markov.model import Forcing, PhysicalConstants, State


ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "cases" / "bavi_2026_reintensification.json"
CONTRACT_PATH = ROOT / "config" / "wind_observation_contract.json"
MODEL_CONFIG_PATH = ROOT / "config" / "model_v0.json"
OUTPUT_PATH = ROOT / "outputs" / "bavi_2026_reintensification_case.json"


def structural_probes() -> list[tuple[State, Forcing]]:
    """Return synthetic probes used only to test code-path dependence on regime."""

    return [
        (
            State(38.5833333333, 0.75, 96_200.0, 120_000.0),
            Forcing(70.0, 8.0, 0.45, 55.0, 1.0, 6.0, 1.0, 0.0, 22.0),
        ),
        (
            State(45.0, 0.82, 95_000.0, 50_000.0),
            Forcing(68.0, 16.0, 0.65, 35.0, 1.5, 8.0, 1.0, 0.0, 25.0),
        ),
    ]


def main() -> None:
    case = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    model_config = json.loads(MODEL_CONFIG_PATH.read_text(encoding="utf-8"))

    scores = {
        agency: score_native_channel(channel, contract)
        for agency, channel in case["channels"].items()
    }
    structural = audit_regime_wind_influence(
        structural_probes(), PhysicalConstants.literature_western_north_pacific()
    )
    eligibility = evaluate_model_eligibility(ROOT, model_config, case, structural)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": case["case_id"],
        "model_id": model_config["model_id"],
        "model_status": model_config["status"],
        "native_channel_scores": {
            agency: asdict(score) for agency, score in scores.items()
        },
        "averaging_period_trace": {
            agency: {
                stage: channel[stage]["wind_averaging_period_seconds"]
                for stage in ("initial_analysis", "forecast", "truth")
            }
            for agency, channel in case["channels"].items()
        },
        "cross_channel_score_performed": False,
        "cross_channel_reason": (
            "No registered agency observation operator with uncertainty exists. "
            "All benchmark scores remain in native channels."
        ),
        "regime_structural_audit": asdict(structural),
        "full_model_case_eligibility": asdict(eligibility),
        "case_scope": case["model_evaluation"]["scope"],
    }
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for agency, score in scores.items():
        print(
            f"{agency}: window={score.averaging_period_seconds}s "
            f"initial={score.initial_wind_native:g}{score.native_unit} "
            f"forecast={score.forecast_wind_native:g}{score.native_unit} "
            f"truth={score.observed_wind_native:g}{score.native_unit} "
            f"error={score.forecast_error_native:+g}{score.native_unit} "
            f"direction_captured={score.event_direction_captured}"
        )
    print(
        "regime_wind_effect=",
        structural.maximum_pairwise_difference_ms_per_day,
        "m/s/day",
    )
    print("model_verdict=", eligibility.verdict)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
