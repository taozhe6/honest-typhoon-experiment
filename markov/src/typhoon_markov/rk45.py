"""Dependency-free Dormand-Prince 5(4) integrator.

The embedded pair is the same Runge-Kutta family used by MATLAB's ode45.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Sequence


Vector = tuple[float, ...]
Derivative = Callable[[float, Vector], Sequence[float]]


@dataclass(frozen=True)
class SolverStats:
    accepted_steps: int
    rejected_steps: int
    derivative_evaluations: int
    final_time_s: float


@dataclass(frozen=True)
class IntegrationResult:
    values: Vector
    stats: SolverStats


class IntegrationError(RuntimeError):
    """Raised when adaptive integration cannot satisfy its error target."""


def _combine(base: Vector, h: float, terms: Sequence[tuple[float, Vector]]) -> Vector:
    return tuple(
        base[i] + h * sum(coefficient * vector[i] for coefficient, vector in terms)
        for i in range(len(base))
    )


def _as_vector(values: Sequence[float], expected_length: int) -> Vector:
    vector = tuple(float(value) for value in values)
    if len(vector) != expected_length:
        raise ValueError("derivative returned a vector with the wrong dimension")
    if any(not math.isfinite(value) for value in vector):
        raise IntegrationError("derivative returned a non-finite value")
    return vector


def integrate_rk45(
    derivative: Derivative,
    initial_values: Sequence[float],
    start_time_s: float,
    end_time_s: float,
    *,
    relative_tolerance: float = 1.0e-6,
    absolute_tolerances: Sequence[float] | None = None,
    initial_step_s: float = 300.0,
    maximum_step_s: float = 900.0,
    minimum_step_s: float = 1.0e-3,
    maximum_steps: int = 100_000,
) -> IntegrationResult:
    """Integrate a vector ODE with an adaptive Dormand-Prince 5(4) pair."""

    values = tuple(float(value) for value in initial_values)
    dimension = len(values)
    if dimension == 0:
        raise ValueError("initial_values must contain at least one state")
    if end_time_s < start_time_s:
        raise ValueError("end_time_s must be greater than or equal to start_time_s")
    if relative_tolerance <= 0.0:
        raise ValueError("relative_tolerance must be positive")
    if minimum_step_s <= 0.0 or maximum_step_s < minimum_step_s:
        raise ValueError("step-size bounds are invalid")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("initial_values must be finite")

    if absolute_tolerances is None:
        absolute_tolerances = (1.0e-8,) * dimension
    absolute = tuple(float(value) for value in absolute_tolerances)
    if len(absolute) != dimension or any(value <= 0.0 for value in absolute):
        raise ValueError("absolute_tolerances must be positive and match the state dimension")

    if end_time_s == start_time_s:
        return IntegrationResult(values, SolverStats(0, 0, 0, end_time_s))

    time_s = float(start_time_s)
    step_s = min(float(initial_step_s), maximum_step_s, end_time_s - start_time_s)
    accepted = 0
    rejected = 0
    evaluations = 0

    for _ in range(maximum_steps):
        if time_s >= end_time_s:
            break
        step_s = min(step_s, end_time_s - time_s)

        k1 = _as_vector(derivative(time_s, values), dimension)
        k2 = _as_vector(
            derivative(time_s + step_s * (1.0 / 5.0), _combine(values, step_s, ((1.0 / 5.0, k1),))),
            dimension,
        )
        k3 = _as_vector(
            derivative(
                time_s + step_s * (3.0 / 10.0),
                _combine(values, step_s, ((3.0 / 40.0, k1), (9.0 / 40.0, k2))),
            ),
            dimension,
        )
        k4 = _as_vector(
            derivative(
                time_s + step_s * (4.0 / 5.0),
                _combine(
                    values,
                    step_s,
                    ((44.0 / 45.0, k1), (-56.0 / 15.0, k2), (32.0 / 9.0, k3)),
                ),
            ),
            dimension,
        )
        k5 = _as_vector(
            derivative(
                time_s + step_s * (8.0 / 9.0),
                _combine(
                    values,
                    step_s,
                    (
                        (19372.0 / 6561.0, k1),
                        (-25360.0 / 2187.0, k2),
                        (64448.0 / 6561.0, k3),
                        (-212.0 / 729.0, k4),
                    ),
                ),
            ),
            dimension,
        )
        k6 = _as_vector(
            derivative(
                time_s + step_s,
                _combine(
                    values,
                    step_s,
                    (
                        (9017.0 / 3168.0, k1),
                        (-355.0 / 33.0, k2),
                        (46732.0 / 5247.0, k3),
                        (49.0 / 176.0, k4),
                        (-5103.0 / 18656.0, k5),
                    ),
                ),
            ),
            dimension,
        )

        fifth_order = _combine(
            values,
            step_s,
            (
                (35.0 / 384.0, k1),
                (500.0 / 1113.0, k3),
                (125.0 / 192.0, k4),
                (-2187.0 / 6784.0, k5),
                (11.0 / 84.0, k6),
            ),
        )
        k7 = _as_vector(derivative(time_s + step_s, fifth_order), dimension)
        fourth_order = _combine(
            values,
            step_s,
            (
                (5179.0 / 57600.0, k1),
                (7571.0 / 16695.0, k3),
                (393.0 / 640.0, k4),
                (-92097.0 / 339200.0, k5),
                (187.0 / 2100.0, k6),
                (1.0 / 40.0, k7),
            ),
        )
        evaluations += 7

        scaled_errors = []
        for index in range(dimension):
            scale = absolute[index] + relative_tolerance * max(
                abs(values[index]), abs(fifth_order[index])
            )
            scaled_errors.append((fifth_order[index] - fourth_order[index]) / scale)
        error_norm = math.sqrt(sum(error * error for error in scaled_errors) / dimension)

        if error_norm <= 1.0:
            time_s += step_s
            values = fifth_order
            accepted += 1
        else:
            rejected += 1

        if error_norm == 0.0:
            factor = 5.0
        else:
            factor = 0.9 * error_norm ** (-0.2)
            factor = min(5.0, max(0.2, factor))
        if error_norm > 1.0:
            factor = min(0.5, factor)
        next_step = min(maximum_step_s, max(minimum_step_s, step_s * factor))
        if step_s <= minimum_step_s and error_norm > 1.0:
            raise IntegrationError("minimum step reached before the error target was met")
        step_s = next_step
    else:
        raise IntegrationError("maximum integration step count exceeded")

    return IntegrationResult(
        values=values,
        stats=SolverStats(accepted, rejected, evaluations, time_s),
    )
