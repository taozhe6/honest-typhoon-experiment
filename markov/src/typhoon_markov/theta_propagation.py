"""Propagate the identifiable FAST ratio theta=Ck/h to synthetic final states."""

from __future__ import annotations

from dataclasses import asdict, replace
import math
from typing import Any, Sequence

from .model import PhysicalConstants
from .sensitivity import (
    compare_trajectories,
    fixed_regime_trajectory,
    parse_scenario,
)


def linear_grid(minimum: float, maximum: float, points: int) -> list[float]:
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise ValueError("grid endpoints must be finite")
    if minimum <= 0.0 or maximum <= minimum:
        raise ValueError("grid requires 0 < minimum < maximum")
    if points < 3:
        raise ValueError("grid requires at least three points")
    step = (maximum - minimum) / (points - 1)
    return [minimum + index * step for index in range(points)]


def constants_for_theta_multiplier(
    baseline: PhysicalConstants,
    multiplier: float,
    *,
    parameterization: str,
) -> PhysicalConstants:
    if not math.isfinite(multiplier) or multiplier <= 0.0:
        raise ValueError("theta multiplier must be finite and positive")
    if parameterization == "exchange_coefficient":
        return replace(
            baseline,
            exchange_coefficient=baseline.exchange_coefficient * multiplier,
        )
    if parameterization == "boundary_layer_depth":
        return replace(
            baseline,
            boundary_layer_depth_m=baseline.boundary_layer_depth_m / multiplier,
        )
    raise ValueError(f"unknown theta parameterization: {parameterization}")


def _final_state(trajectory: Sequence[dict[str, Any]]) -> dict[str, float]:
    if not trajectory:
        raise ValueError("trajectory is empty")
    state = trajectory[-1]["state"]
    return {
        "wind_ms": float(state["wind_ms"]),
        "core_moisture": float(state["core_moisture"]),
        "central_pressure_hpa": float(state["central_pressure_hpa"]),
        "rmw_km": float(state["rmw_km"]),
    }


def _is_monotonic(values: Sequence[float], tolerance: float = 1.0e-10) -> str:
    differences = [right - left for left, right in zip(values, values[1:])]
    if all(value >= -tolerance for value in differences):
        return "nondecreasing"
    if all(value <= tolerance for value in differences):
        return "nonincreasing"
    return "nonmonotonic"


def run_theta_grid(
    theta_config: dict[str, Any],
    scenario_config: dict[str, Any],
) -> dict[str, Any]:
    baseline = PhysicalConstants.literature_western_north_pacific()
    registered = theta_config["baseline"]
    registered_theta = float(registered["theta_per_m"])
    actual_theta = baseline.exchange_coefficient / baseline.boundary_layer_depth_m
    if not math.isclose(
        float(registered["exchange_coefficient"]),
        baseline.exchange_coefficient,
        rel_tol=0.0,
        abs_tol=1.0e-15,
    ):
        raise RuntimeError("registered exchange coefficient does not match model")
    if not math.isclose(
        float(registered["boundary_layer_depth_m"]),
        baseline.boundary_layer_depth_m,
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise RuntimeError("registered boundary-layer depth does not match model")
    if not math.isclose(registered_theta, actual_theta, rel_tol=1.0e-12):
        raise RuntimeError("registered theta does not match model constants")

    grid_config = theta_config["theta_multiplier_grid"]
    multipliers = linear_grid(
        float(grid_config["minimum"]),
        float(grid_config["maximum"]),
        int(grid_config["points"]),
    )
    baseline_index = min(
        range(len(multipliers)), key=lambda index: abs(multipliers[index] - 1.0)
    )
    if not math.isclose(multipliers[baseline_index], 1.0, abs_tol=1.0e-12):
        raise RuntimeError("theta grid must contain multiplier 1.0")

    integration = scenario_config["integration"]
    duration_hours = float(integration["duration_hours_per_step"])
    integration_steps = int(integration["steps"])
    comparison_hour = float(theta_config["comparison_hour"])
    configured_integration_hour = float(integration["comparison_hour"])
    calculated_integration_hour = duration_hours * integration_steps
    if not math.isclose(
        comparison_hour, configured_integration_hour, rel_tol=0.0, abs_tol=1.0e-12
    ):
        raise RuntimeError("theta and scenario comparison hours do not match")
    if not math.isclose(
        comparison_hour, calculated_integration_hour, rel_tol=0.0, abs_tol=1.0e-12
    ):
        raise RuntimeError("comparison hour does not equal duration times steps")
    wanted = set(theta_config["scenario_ids"])
    scenarios = [
        scenario for scenario in scenario_config["scenarios"] if scenario["id"] in wanted
    ]
    if {scenario["id"] for scenario in scenarios} != wanted:
        raise RuntimeError("one or more registered theta scenarios are missing")

    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    parameterization_checks: list[dict[str, Any]] = []
    tolerance = float(theta_config["absolute_invariance_tolerance"])

    for scenario in scenarios:
        initial_state, _initial_regime, forcings, schedule = parse_scenario(scenario)
        if len(forcings) != integration_steps or len(schedule) != integration_steps:
            raise RuntimeError(
                f"scenario {scenario['id']} does not contain the registered step count"
            )
        trajectories: list[list[dict[str, Any]]] = []
        for multiplier in multipliers:
            constants = constants_for_theta_multiplier(
                baseline, multiplier, parameterization="exchange_coefficient"
            )
            trajectory = fixed_regime_trajectory(
                initial_state,
                forcings,
                schedule,
                constants,
                duration_hours=duration_hours,
            )
            trajectories.append(trajectory)
        baseline_final = _final_state(trajectories[baseline_index])

        scenario_rows: list[dict[str, Any]] = []
        for multiplier, trajectory in zip(multipliers, trajectories):
            constants = constants_for_theta_multiplier(
                baseline, multiplier, parameterization="exchange_coefficient"
            )
            final = _final_state(trajectory)
            row = {
                "scenario": scenario["id"],
                "theta_multiplier": multiplier,
                "theta_per_m": actual_theta * multiplier,
                "exchange_coefficient": constants.exchange_coefficient,
                "boundary_layer_depth_m": constants.boundary_layer_depth_m,
                "comparison_hour": comparison_hour,
                **{f"final_{name}": value for name, value in final.items()},
                **{
                    f"delta_{name}": value - baseline_final[name]
                    for name, value in final.items()
                },
            }
            rows.append(row)
            scenario_rows.append(row)

        lower = scenario_rows[0]
        upper = scenario_rows[-1]
        winds = [row["final_wind_ms"] for row in scenario_rows]
        minimum_row = min(scenario_rows, key=lambda row: row["final_wind_ms"])
        maximum_row = max(scenario_rows, key=lambda row: row["final_wind_ms"])
        summaries.append(
            {
                "scenario": scenario["id"],
                "initial_wind_ms": initial_state.wind_ms,
                "baseline_final": baseline_final,
                "lower_theta_endpoint": lower,
                "upper_theta_endpoint": upper,
                "wind_envelope": {
                    "minimum_ms": minimum_row["final_wind_ms"],
                    "minimum_theta_multiplier": minimum_row["theta_multiplier"],
                    "maximum_ms": maximum_row["final_wind_ms"],
                    "maximum_theta_multiplier": maximum_row["theta_multiplier"],
                    "endpoint_to_endpoint_width_ms": max(winds) - min(winds),
                    "maximum_baseline_centered_absolute_delta_ms": max(
                        abs(row["delta_wind_ms"]) for row in scenario_rows
                    ),
                    "monotonicity": _is_monotonic(winds),
                },
            }
        )

        for endpoint_index in (0, len(multipliers) - 1):
            multiplier = multipliers[endpoint_index]
            h_constants = constants_for_theta_multiplier(
                baseline, multiplier, parameterization="boundary_layer_depth"
            )
            h_trajectory = fixed_regime_trajectory(
                initial_state,
                forcings,
                schedule,
                h_constants,
                duration_hours=duration_hours,
            )
            comparison = compare_trajectories(
                trajectories[endpoint_index], h_trajectory
            )
            parameterization_checks.append(
                {
                    "scenario": scenario["id"],
                    "theta_multiplier": multiplier,
                    "Ck_parameterization": {
                        "exchange_coefficient": baseline.exchange_coefficient
                        * multiplier,
                        "boundary_layer_depth_m": baseline.boundary_layer_depth_m,
                    },
                    "h_parameterization": {
                        "exchange_coefficient": baseline.exchange_coefficient,
                        "boundary_layer_depth_m": baseline.boundary_layer_depth_m
                        / multiplier,
                    },
                    "comparison": comparison,
                }
            )

    maximum_parameterization_state_delta = max(
        item["comparison"]["max_abs_native_state_delta"]
        for item in parameterization_checks
    )
    maximum_parameterization_wind_delta = max(
        item["comparison"]["max_abs_trajectory_delta"]["wind_ms"]
        for item in parameterization_checks
    )
    return {
        "theta_definition": "exchange_coefficient / boundary_layer_depth_m",
        "baseline_constants": asdict(baseline),
        "baseline_theta_per_m": actual_theta,
        "theta_multipliers": multipliers,
        "rows": rows,
        "scenario_summaries": summaries,
        "structural_checks": {
            "equivalent_parameterizations": {
                "absolute_tolerance": tolerance,
                "maximum_native_state_delta": maximum_parameterization_state_delta,
                "maximum_wind_trajectory_delta_ms": maximum_parameterization_wind_delta,
                "passed": maximum_parameterization_state_delta <= tolerance,
                "details": parameterization_checks,
            },
            "all_wind_responses_monotonic": all(
                item["wind_envelope"]["monotonicity"] != "nonmonotonic"
                for item in summaries
            ),
        },
        "cross_scenario": {
            "maximum_baseline_centered_absolute_delta_ms": max(
                item["wind_envelope"][
                    "maximum_baseline_centered_absolute_delta_ms"
                ]
                for item in summaries
            ),
            "maximum_endpoint_to_endpoint_width_ms": max(
                item["wind_envelope"]["endpoint_to_endpoint_width_ms"]
                for item in summaries
            ),
        },
    }
