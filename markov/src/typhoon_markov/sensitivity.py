"""Synthetic fixed-constant sensitivity utilities for the rejected v0.1 model."""

from __future__ import annotations

from dataclasses import asdict, fields, replace
from typing import Any, Sequence

from .model import (
    Forcing,
    MarkovParameters,
    PhysicalConstants,
    Regime,
    State,
    fast_tendencies,
    simulate,
    state_tendencies,
)
from .rk45 import integrate_rk45


STATE_FIELDS = ("wind_ms", "core_moisture", "central_pressure_pa", "rmw_m")


def constants_with_multipliers(
    constants: PhysicalConstants,
    multipliers: dict[str, float],
) -> PhysicalConstants:
    allowed = {field.name for field in fields(PhysicalConstants)}
    unknown = set(multipliers).difference(allowed)
    if unknown:
        raise ValueError(f"unknown physical constants: {sorted(unknown)}")
    if any(value <= 0.0 for value in multipliers.values()):
        raise ValueError("constant multipliers must be positive")
    updates = {
        name: getattr(constants, name) * multiplier
        for name, multiplier in multipliers.items()
    }
    result = replace(constants, **updates)
    if result.beta <= 0.0:
        raise ValueError("perturbation makes FAST beta non-positive")
    return result


def regime_from_name(name: str) -> Regime:
    try:
        return Regime[name.strip().upper()]
    except KeyError as error:
        raise ValueError(f"unknown regime: {name}") from error


def _serialize_state(state: State) -> dict[str, float]:
    return {
        **asdict(state),
        "central_pressure_hpa": state.central_pressure_pa / 100.0,
        "rmw_km": state.rmw_m / 1000.0,
    }


def fixed_regime_trajectory(
    initial_state: State,
    forcings: Sequence[Forcing],
    regime_schedule: Sequence[Regime],
    constants: PhysicalConstants,
    *,
    duration_hours: float,
) -> list[dict[str, Any]]:
    if len(forcings) != len(regime_schedule):
        raise ValueError("forcing and fixed-regime schedules must have equal length")
    state = initial_state
    steps: list[dict[str, Any]] = []
    for index, (forcing, regime) in enumerate(zip(forcings, regime_schedule), start=1):
        diagnostics = fast_tendencies(state, forcing, constants)

        def derivative(_time_s: float, values: tuple[float, ...]) -> tuple[float, ...]:
            return state_tendencies(State.from_vector(values), regime, forcing, constants)

        integration = integrate_rk45(
            derivative,
            state.as_vector(),
            0.0,
            duration_hours * 3600.0,
            relative_tolerance=1.0e-6,
            absolute_tolerances=(1.0e-4, 1.0e-8, 0.1, 0.1),
            initial_step_s=300.0,
            maximum_step_s=900.0,
        )
        state = State.from_vector(integration.values)
        state.validate()
        steps.append(
            {
                "valid_hour": duration_hours * index,
                "regime": regime.name.lower(),
                "state": _serialize_state(state),
                "diagnostics_at_step_start": asdict(diagnostics),
                "transition_probabilities": None,
                "solver": asdict(integration.stats),
            }
        )
    return steps


def markov_trajectory(
    initial_state: State,
    initial_regime: Regime,
    forcings: Sequence[Forcing],
    parameters: MarkovParameters,
    constants: PhysicalConstants,
    *,
    seed: int,
    duration_hours: float,
) -> list[dict[str, Any]]:
    results = simulate(
        initial_state,
        initial_regime,
        forcings,
        parameters,
        constants,
        seed=seed,
        duration_hours=duration_hours,
    )
    return [
        {
            "valid_hour": duration_hours * index,
            "regime": result.regime.name.lower(),
            "state": _serialize_state(result.state),
            "diagnostics_at_step_start": asdict(result.diagnostics),
            "transition_probabilities": {
                regime.name.lower(): probability
                for regime, probability in zip(Regime, result.transition_probabilities)
            },
            "solver": asdict(result.integration.stats),
        }
        for index, result in enumerate(results, start=1)
    ]


def compare_trajectories(
    baseline: Sequence[dict[str, Any]],
    perturbed: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    if len(baseline) != len(perturbed) or not baseline:
        raise ValueError("non-empty equal-length trajectories are required")
    scales = {
        "wind_ms": 1.0,
        "core_moisture": 1.0,
        "central_pressure_hpa": 1.0,
        "rmw_km": 1.0,
    }
    final_delta: dict[str, float] = {}
    max_abs_delta: dict[str, float] = {}
    for field, scale in scales.items():
        differences = [
            (variant["state"][field] - base["state"][field]) * scale
            for base, variant in zip(baseline, perturbed)
        ]
        final_delta[field] = differences[-1]
        max_abs_delta[field] = max(abs(value) for value in differences)

    native_max = 0.0
    for base, variant in zip(baseline, perturbed):
        for field in STATE_FIELDS:
            native_max = max(
                native_max,
                abs(variant["state"][field] - base["state"][field]),
            )

    probability_l1: list[float] = []
    for base, variant in zip(baseline, perturbed):
        if base["transition_probabilities"] is None:
            continue
        probability_l1.append(
            sum(
                abs(
                    variant["transition_probabilities"][name]
                    - base["transition_probabilities"][name]
                )
                for name in ("weakening", "quasi_steady", "intensifying")
            )
        )
    return {
        "final_delta": final_delta,
        "max_abs_trajectory_delta": max_abs_delta,
        "max_abs_native_state_delta": native_max,
        "regime_mismatch_steps": sum(
            base["regime"] != variant["regime"]
            for base, variant in zip(baseline, perturbed)
        ),
        "max_transition_probability_l1_delta": max(probability_l1, default=0.0),
    }


def instantaneous_probe(
    initial_state: State,
    first_forcing: Forcing,
    constants: PhysicalConstants,
) -> dict[str, float]:
    diagnostic = fast_tendencies(initial_state, first_forcing, constants)
    seconds_per_day = 86400.0
    return {
        "wind_tendency_ms_per_day": diagnostic.wind_tendency_ms2 * seconds_per_day,
        "moisture_tendency_per_day": diagnostic.moisture_tendency_s * seconds_per_day,
        "ocean_feedback_alpha": diagnostic.ocean_feedback_alpha,
        "standardized_24h_tendency": diagnostic.standardized_24h_tendency,
    }


def parse_scenario(scenario: dict[str, Any]) -> tuple[State, Regime, list[Forcing], list[Regime]]:
    initial_state = State(**scenario["initial_state"])
    initial_state.validate()
    initial_regime = regime_from_name(scenario["initial_regime"])
    forcings = [Forcing(**forcing) for forcing in scenario["forcings"]]
    for forcing in forcings:
        forcing.validate()
    schedule = [regime_from_name(name) for name in scenario["forced_regime_schedule"]]
    return initial_state, initial_regime, forcings, schedule


def run_sensitivity(config: dict[str, Any]) -> dict[str, Any]:
    baseline_constants = PhysicalConstants.literature_western_north_pacific()
    parameters = MarkovParameters(**config["markov_parameters"])
    parameters.validate()
    duration_hours = float(config["integration"]["duration_hours_per_step"])
    perturbations = [
        {
            **item,
            "constants": constants_with_multipliers(
                baseline_constants, item["multipliers"]
            ),
        }
        for item in config["perturbations"]
    ]
    scenario_outputs: list[dict[str, Any]] = []
    flat_comparisons: list[dict[str, Any]] = []
    engine_wind_invariance: list[float] = []

    for scenario in config["scenarios"]:
        initial_state, initial_regime, forcings, schedule = parse_scenario(scenario)
        baseline_fixed = fixed_regime_trajectory(
            initial_state,
            forcings,
            schedule,
            baseline_constants,
            duration_hours=duration_hours,
        )
        baseline_markov = markov_trajectory(
            initial_state,
            initial_regime,
            forcings,
            parameters,
            baseline_constants,
            seed=int(scenario["seed"]),
            duration_hours=duration_hours,
        )
        engine_wind_invariance.append(
            max(
                abs(left["state"]["wind_ms"] - right["state"]["wind_ms"])
                for left, right in zip(baseline_fixed, baseline_markov)
            )
        )
        engines: dict[str, Any] = {}
        for engine_name, baseline_trajectory in (
            ("fixed_regime", baseline_fixed),
            ("full_markov", baseline_markov),
        ):
            variants: list[dict[str, Any]] = []
            for perturbation in perturbations:
                constants = perturbation["constants"]
                if engine_name == "fixed_regime":
                    trajectory = fixed_regime_trajectory(
                        initial_state,
                        forcings,
                        schedule,
                        constants,
                        duration_hours=duration_hours,
                    )
                else:
                    trajectory = markov_trajectory(
                        initial_state,
                        initial_regime,
                        forcings,
                        parameters,
                        constants,
                        seed=int(scenario["seed"]),
                        duration_hours=duration_hours,
                    )
                comparison = compare_trajectories(baseline_trajectory, trajectory)
                record = {
                    "scenario": scenario["id"],
                    "engine": engine_name,
                    "variant": perturbation["id"],
                    "type": perturbation["type"],
                    "multipliers": perturbation["multipliers"],
                    "comparison": comparison,
                }
                flat_comparisons.append(record)
                variants.append(
                    {
                        "id": perturbation["id"],
                        "type": perturbation["type"],
                        "multipliers": perturbation["multipliers"],
                        "constants": asdict(constants),
                        "instantaneous_probe": instantaneous_probe(
                            initial_state, forcings[0], constants
                        ),
                        "comparison_to_baseline": comparison,
                        "trajectory": trajectory,
                    }
                )
            engines[engine_name] = {
                "baseline": {
                    "constants": asdict(baseline_constants),
                    "instantaneous_probe": instantaneous_probe(
                        initial_state, forcings[0], baseline_constants
                    ),
                    "trajectory": baseline_trajectory,
                },
                "variants": variants,
            }
        scenario_outputs.append(
            {
                "id": scenario["id"],
                "seed": scenario["seed"],
                "initial_state": scenario["initial_state"],
                "initial_regime": scenario["initial_regime"],
                "forced_regime_schedule": scenario["forced_regime_schedule"],
                "engines": engines,
            }
        )

    summary = summarize_comparisons(flat_comparisons)
    ratio_records = [
        record
        for record in flat_comparisons
        if record["type"] == "ratio_preserving_structural_control"
    ]
    ratio_max_state = max(
        record["comparison"]["max_abs_native_state_delta"]
        for record in ratio_records
    )
    ratio_max_probability = max(
        record["comparison"]["max_transition_probability_l1_delta"]
        for record in ratio_records
    )
    tolerance = float(config["ratio_invariance_absolute_tolerance"])
    return {
        "baseline_constants": asdict(baseline_constants),
        "markov_parameters": asdict(parameters),
        "scenarios": scenario_outputs,
        "summary": summary,
        "structural_checks": {
            "Ck_over_h_ratio_invariance": {
                "absolute_tolerance": tolerance,
                "maximum_native_state_delta": ratio_max_state,
                "maximum_transition_probability_l1_delta": ratio_max_probability,
                "passed": ratio_max_state <= tolerance
                and ratio_max_probability <= tolerance,
            },
            "regime_has_zero_wind_path_effect": {
                "maximum_fixed_vs_markov_wind_delta_ms": max(engine_wind_invariance),
                "passed": max(engine_wind_invariance) <= tolerance,
            },
        },
    }


def summarize_comparisons(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault((record["engine"], record["variant"]), []).append(record)
    summary: list[dict[str, Any]] = []
    for (engine, variant), items in sorted(groups.items()):
        summary.append(
            {
                "engine": engine,
                "variant": variant,
                "type": items[0]["type"],
                "multipliers": items[0]["multipliers"],
                "max_across_scenarios": {
                    "absolute_final_wind_delta_ms": max(
                        abs(item["comparison"]["final_delta"]["wind_ms"])
                        for item in items
                    ),
                    "absolute_final_pressure_delta_hpa": max(
                        abs(
                            item["comparison"]["final_delta"][
                                "central_pressure_hpa"
                            ]
                        )
                        for item in items
                    ),
                    "absolute_final_rmw_delta_km": max(
                        abs(item["comparison"]["final_delta"]["rmw_km"])
                        for item in items
                    ),
                    "absolute_trajectory_wind_delta_ms": max(
                        item["comparison"]["max_abs_trajectory_delta"]["wind_ms"]
                        for item in items
                    ),
                    "transition_probability_l1_delta": max(
                        item["comparison"]["max_transition_probability_l1_delta"]
                        for item in items
                    ),
                    "regime_mismatch_steps": max(
                        item["comparison"]["regime_mismatch_steps"] for item in items
                    ),
                },
            }
        )
    return summary
