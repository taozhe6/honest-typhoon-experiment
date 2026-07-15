#!/usr/bin/env python3
"""Run a clearly labeled synthetic smoke test for the research model."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from typhoon_markov.model import (
    FREE_PARAMETER_NAMES,
    OBSERVATION_CHANNELS,
    Forcing,
    MarkovParameters,
    PhysicalConstants,
    Regime,
    State,
    simulate,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    config = json.loads((ROOT / "config" / "model_v0.json").read_text(encoding="utf-8"))
    parameters = MarkovParameters(
        persistence_logit=config["free_parameters"]["persistence_logit"][
            "synthetic_demo_value"
        ],
        regime_width=config["free_parameters"]["regime_width"]["synthetic_demo_value"],
        calibration_id="synthetic-demo-from-model-v0-config",
        calibrated=False,
    )
    constants = PhysicalConstants.literature_western_north_pacific()
    initial_state = State(
        wind_ms=35.0,
        core_moisture=0.78,
        central_pressure_pa=97_000.0,
        rmw_m=40_000.0,
    )

    forcings = []
    for index in range(8):
        forcings.append(
            Forcing(
                potential_intensity_ms=72.0 - 0.8 * index,
                shear_ms=7.0 + 1.8 * index,
                entropy_deficit=0.45,
                mixed_layer_depth_m=55.0,
                submixed_stratification_k_per_100m=1.0,
                translation_speed_ms=5.5,
                surface_exchange_multiplier=1.0,
                land_fraction=0.0,
                latitude_deg=20.0 + 0.4 * index,
            )
        )

    results = simulate(
        initial_state,
        Regime.QUASI_STEADY,
        forcings,
        parameters,
        constants,
        seed=20260711,
    )
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_id": config["model_id"],
        "model_status": config["status"],
        "scenario": "synthetic-smoke-test",
        "authoritative_forecast": False,
        "global_free_parameter_count": len(FREE_PARAMETER_NAMES),
        "scored_observation_field_count": len(OBSERVATION_CHANNELS),
        "parameters": asdict(parameters),
        "initial_state": asdict(initial_state),
        "steps": [],
    }
    for index, result in enumerate(results, start=1):
        payload["steps"].append(
            {
                "valid_hour": 6 * index,
                "regime": result.regime.name.lower(),
                "transition_probabilities": {
                    regime.name.lower(): probability
                    for regime, probability in zip(Regime, result.transition_probabilities)
                },
                "state": asdict(result.state),
                "diagnostics_at_step_start": asdict(result.diagnostics),
                "solver": asdict(result.integration.stats),
            }
        )

    output_path = ROOT / "outputs" / "synthetic_smoke.json"
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output_path)
    print(json.dumps(payload["steps"][-1], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
