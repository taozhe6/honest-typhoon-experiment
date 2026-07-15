"""Falsifiable Markov-switching reduced-order tropical-cyclone model."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
import math
import random
from typing import Iterable, Sequence

from .rk45 import IntegrationResult, integrate_rk45


SECONDS_PER_DAY = 86_400.0
KNOT_TO_MS = 0.5144444444444445
RI_THRESHOLD_MS_PER_DAY = 30.0 * KNOT_TO_MS
OBSERVATION_CHANNELS = ("wind_ms", "central_pressure_pa", "rmw_m")
FREE_PARAMETER_NAMES = ("persistence_logit", "regime_width")
REGIME_CENTERS = (-1.0, 0.0, 1.0)


class ModelDomainError(ValueError):
    """Raised when a state or forcing lies outside the registered model domain."""


class InitializationError(ValueError):
    """Raised when past observations cannot initialize the physical state consistently."""


class Regime(IntEnum):
    WEAKENING = 0
    QUASI_STEADY = 1
    INTENSIFYING = 2


@dataclass(frozen=True)
class PhysicalConstants:
    exchange_coefficient: float
    boundary_layer_depth_m: float
    thermodynamic_efficiency: float
    kappa: float
    weakening_column_speed_m_s: float
    intensifying_column_speed_m_s: float
    earth_angular_velocity_s: float

    @classmethod
    def literature_western_north_pacific(cls) -> "PhysicalConstants":
        """Return the preregistered constants; these values are outside calibration."""

        return cls(
            exchange_coefficient=1.2e-3,
            boundary_layer_depth_m=1_800.0,
            thermodynamic_efficiency=0.33,
            kappa=0.10,
            weakening_column_speed_m_s=-0.24 * 1_000.0 / SECONDS_PER_DAY,
            intensifying_column_speed_m_s=0.29 * 1_000.0 / SECONDS_PER_DAY,
            earth_angular_velocity_s=7.2921159e-5,
        )

    @property
    def beta(self) -> float:
        return 1.0 - self.thermodynamic_efficiency - self.kappa


@dataclass(frozen=True)
class MarkovParameters:
    """The complete set of globally calibratable numeric parameters."""

    persistence_logit: float
    regime_width: float
    calibration_id: str
    calibrated: bool

    def validate(self) -> None:
        if not math.isfinite(self.persistence_logit) or not 0.0 <= self.persistence_logit <= 20.0:
            raise ModelDomainError("persistence_logit must lie within [0, 20]")
        if not math.isfinite(self.regime_width) or not 0.05 <= self.regime_width <= 3.0:
            raise ModelDomainError("regime_width must lie within [0.05, 3.0]")
        if not self.calibration_id.strip():
            raise ModelDomainError("calibration_id is required for traceability")


@dataclass(frozen=True)
class State:
    """Continuous state X=(V, m, Pc, RMW)."""

    wind_ms: float
    core_moisture: float
    central_pressure_pa: float
    rmw_m: float

    def validate(self) -> None:
        values = asdict(self)
        if any(not math.isfinite(value) for value in values.values()):
            raise ModelDomainError("state values must be finite")
        if not 0.0 <= self.wind_ms <= 120.0:
            raise ModelDomainError("wind_ms lies outside [0, 120] m/s")
        if not 0.0 <= self.core_moisture <= 1.0:
            raise ModelDomainError("core_moisture lies outside [0, 1]")
        if not 80_000.0 <= self.central_pressure_pa <= 106_000.0:
            raise ModelDomainError("central_pressure_pa lies outside [80000, 106000] Pa")
        if not 3_000.0 <= self.rmw_m <= 300_000.0:
            raise ModelDomainError("rmw_m lies outside [3, 300] km")

    def as_vector(self) -> tuple[float, float, float, float]:
        return self.wind_ms, self.core_moisture, self.central_pressure_pa, self.rmw_m

    def observable_vector(self) -> tuple[float, float, float]:
        return self.wind_ms, self.central_pressure_pa, self.rmw_m

    @classmethod
    def from_vector(cls, values: Sequence[float]) -> "State":
        if len(values) != 4:
            raise ValueError("state vector must have four entries")
        return cls(*(float(value) for value in values))


@dataclass(frozen=True)
class Forcing:
    """Externally supplied environmental forcing for one integration interval."""

    potential_intensity_ms: float
    shear_ms: float
    entropy_deficit: float
    mixed_layer_depth_m: float
    submixed_stratification_k_per_100m: float
    translation_speed_ms: float
    surface_exchange_multiplier: float
    land_fraction: float
    latitude_deg: float

    def validate(self) -> None:
        values = asdict(self)
        if any(not math.isfinite(value) for value in values.values()):
            raise ModelDomainError("forcing values must be finite")
        if not 0.0 <= self.land_fraction <= 1.0:
            raise ModelDomainError("land_fraction lies outside [0, 1]")
        ocean_fraction = 1.0 - self.land_fraction
        if ocean_fraction >= 0.05 and not 20.0 <= self.potential_intensity_ms <= 100.0:
            raise ModelDomainError(
                "open-ocean potential intensity must lie within [20, 100] m/s; "
                "supply a full-profile PI product"
            )
        if ocean_fraction < 0.05 and not 0.0 <= self.potential_intensity_ms <= 100.0:
            raise ModelDomainError("potential_intensity_ms lies outside [0, 100] m/s")
        if not 0.0 <= self.shear_ms <= 60.0:
            raise ModelDomainError("shear_ms lies outside [0, 60] m/s")
        if not 0.0 <= self.entropy_deficit <= 5.0:
            raise ModelDomainError("entropy_deficit lies outside [0, 5]")
        if ocean_fraction >= 0.05:
            if not 1.0 <= self.mixed_layer_depth_m <= 300.0:
                raise ModelDomainError("mixed_layer_depth_m lies outside [1, 300] m")
            if not 0.01 <= self.submixed_stratification_k_per_100m <= 10.0:
                raise ModelDomainError(
                    "submixed_stratification_k_per_100m lies outside [0.01, 10]"
                )
        if not 0.0 <= self.translation_speed_ms <= 40.0:
            raise ModelDomainError("translation_speed_ms lies outside [0, 40] m/s")
        if not 0.5 <= self.surface_exchange_multiplier <= 8.0:
            raise ModelDomainError("surface_exchange_multiplier lies outside [0.5, 8]")
        if not -45.0 <= self.latitude_deg <= 45.0:
            raise ModelDomainError("latitude_deg lies outside the tropical model domain")

    @property
    def ocean_fraction(self) -> float:
        return 1.0 - self.land_fraction


@dataclass(frozen=True)
class TendencyDiagnostics:
    wind_tendency_ms2: float
    moisture_tendency_s: float
    ocean_feedback_alpha: float
    standardized_24h_tendency: float


@dataclass(frozen=True)
class StepResult:
    state: State
    regime: Regime
    transition_probabilities: tuple[float, float, float]
    diagnostics: TendencyDiagnostics
    integration: IntegrationResult


def ocean_feedback_alpha(state: State, forcing: Forcing) -> float:
    """Schade-Emanuel ocean interaction factor used by FAST."""

    if forcing.ocean_fraction < 0.05:
        return 1.0
    denominator_wind = max(state.wind_ms, 1.0)
    z_value = (
        0.01
        * forcing.submixed_stratification_k_per_100m ** (-0.4)
        * forcing.mixed_layer_depth_m
        * forcing.translation_speed_ms
        * forcing.potential_intensity_ms
        / denominator_wind
    )
    ocean_alpha = 1.0 - 0.87 * math.exp(-min(100.0, max(0.0, z_value)))
    return forcing.ocean_fraction * ocean_alpha + forcing.land_fraction


def fast_tendencies(
    state: State,
    forcing: Forcing,
    constants: PhysicalConstants,
) -> TendencyDiagnostics:
    """Evaluate the FAST V-m physical core without a statistical correction."""

    alpha = ocean_feedback_alpha(state, forcing)
    gamma = constants.thermodynamic_efficiency + alpha * constants.kappa
    moisture_cubed = state.core_moisture**3
    effective_pi_squared = forcing.ocean_fraction * forcing.potential_intensity_ms**2
    prefactor = (
        0.5
        * constants.exchange_coefficient
        * forcing.surface_exchange_multiplier
        / constants.boundary_layer_depth_m
    )
    wind_tendency = prefactor * (
        alpha * constants.beta * effective_pi_squared * moisture_cubed
        - (1.0 - gamma * moisture_cubed) * state.wind_ms**2
    )
    moisture_tendency = prefactor * (
        (1.0 - state.core_moisture) * state.wind_ms
        - forcing.entropy_deficit * forcing.shear_ms * state.core_moisture
    )
    standardized = wind_tendency * SECONDS_PER_DAY / RI_THRESHOLD_MS_PER_DAY
    return TendencyDiagnostics(wind_tendency, moisture_tendency, alpha, standardized)


def transition_probabilities(
    current_regime: Regime,
    state: State,
    forcing: Forcing,
    parameters: MarkovParameters,
    constants: PhysicalConstants,
) -> tuple[float, float, float]:
    """Environment-dependent first-order Markov transition kernel."""

    state.validate()
    forcing.validate()
    parameters.validate()
    score = fast_tendencies(state, forcing, constants).standardized_24h_tendency
    logits = []
    for index, center in enumerate(REGIME_CENTERS):
        persistence = (
            parameters.persistence_logit * forcing.ocean_fraction
            if index == int(current_regime)
            else 0.0
        )
        distance_penalty = -0.5 * ((score - center) / parameters.regime_width) ** 2
        logits.append(persistence + distance_penalty)
    maximum = max(logits)
    weights = [math.exp(logit - maximum) for logit in logits]
    weights[int(Regime.INTENSIFYING)] *= forcing.ocean_fraction
    total = sum(weights)
    return tuple(weight / total for weight in weights)  # type: ignore[return-value]


def sample_regime(
    probabilities: Sequence[float],
    random_generator: random.Random,
) -> Regime:
    if len(probabilities) != 3 or any(value < 0.0 for value in probabilities):
        raise ValueError("three non-negative regime probabilities are required")
    total = sum(probabilities)
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1.0e-10):
        raise ValueError("regime probabilities must sum to one")
    draw = random_generator.random()
    cumulative = 0.0
    for index, probability in enumerate(probabilities):
        cumulative += probability
        if draw <= cumulative:
            return Regime(index)
    return Regime.INTENSIFYING


def _column_speed(regime: Regime, constants: PhysicalConstants) -> float:
    if regime == Regime.WEAKENING:
        return constants.weakening_column_speed_m_s
    if regime == Regime.INTENSIFYING:
        return constants.intensifying_column_speed_m_s
    return 0.0


def _derivative(
    regime: Regime,
    forcing: Forcing,
    constants: PhysicalConstants,
):
    column_speed = _column_speed(regime, constants)
    coriolis = abs(
        2.0
        * constants.earth_angular_velocity_s
        * math.sin(math.radians(forcing.latitude_deg))
    )

    def evaluate(_time_s: float, values: tuple[float, ...]) -> tuple[float, ...]:
        state = State.from_vector(values)
        diagnostics = fast_tendencies(state, forcing, constants)
        pressure_tendency = (
            -2.0 * state.central_pressure_pa * column_speed / state.rmw_m
        )
        angular_momentum_denominator = state.wind_ms + coriolis * state.rmw_m
        rmw_tendency = (
            -state.rmw_m
            * diagnostics.wind_tendency_ms2
            / max(angular_momentum_denominator, 1.0)
        )
        return (
            diagnostics.wind_tendency_ms2,
            diagnostics.moisture_tendency_s,
            pressure_tendency,
            rmw_tendency,
        )

    return evaluate


def state_tendencies(
    state: State,
    regime: Regime,
    forcing: Forcing,
    constants: PhysicalConstants,
) -> tuple[float, float, float, float]:
    """Expose the instantaneous state derivative for structural audits."""

    state.validate()
    forcing.validate()
    return _derivative(regime, forcing, constants)(0.0, state.as_vector())


def initialize_core_moisture(
    wind_ms: float,
    observed_past_wind_tendency_ms2: float,
    forcing: Forcing,
    constants: PhysicalConstants,
) -> float:
    """Infer m from wind and a past-only observed intensity tendency."""

    probe = State(wind_ms, 0.5, 100_000.0, 30_000.0)
    forcing.validate()
    alpha = ocean_feedback_alpha(probe, forcing)
    gamma = constants.thermodynamic_efficiency + alpha * constants.kappa
    denominator = (
        alpha
        * constants.beta
        * forcing.ocean_fraction
        * forcing.potential_intensity_ms**2
        + gamma * wind_ms**2
    )
    numerator = (
        2.0
        * constants.boundary_layer_depth_m
        * observed_past_wind_tendency_ms2
        / (constants.exchange_coefficient * forcing.surface_exchange_multiplier)
        + wind_ms**2
    )
    moisture_cubed = numerator / max(denominator, 1.0e-12)
    if not -0.05 <= moisture_cubed <= 1.05:
        raise InitializationError(
            "past intensity tendency is inconsistent with FAST under the supplied forcing"
        )
    return min(1.0, max(0.0, moisture_cubed)) ** (1.0 / 3.0)


def infer_initial_regime(observed_past_wind_tendency_ms2: float) -> Regime:
    score = observed_past_wind_tendency_ms2 * SECONDS_PER_DAY / RI_THRESHOLD_MS_PER_DAY
    index = min(range(3), key=lambda candidate: abs(score - REGIME_CENTERS[candidate]))
    return Regime(index)


def step(
    state: State,
    current_regime: Regime,
    forcing: Forcing,
    parameters: MarkovParameters,
    constants: PhysicalConstants,
    random_generator: random.Random,
    *,
    duration_hours: float = 6.0,
) -> StepResult:
    """Advance one Markov interval using only (X_k, Z_k, U_k)."""

    state.validate()
    forcing.validate()
    parameters.validate()
    if not 0.0 < duration_hours <= 24.0:
        raise ModelDomainError("duration_hours must lie within (0, 24]")

    probabilities = transition_probabilities(
        current_regime, state, forcing, parameters, constants
    )
    next_regime = sample_regime(probabilities, random_generator)
    diagnostics = fast_tendencies(state, forcing, constants)
    integration = integrate_rk45(
        _derivative(next_regime, forcing, constants),
        state.as_vector(),
        0.0,
        duration_hours * 3_600.0,
        relative_tolerance=1.0e-6,
        absolute_tolerances=(1.0e-4, 1.0e-8, 0.1, 0.1),
        initial_step_s=300.0,
        maximum_step_s=900.0,
    )
    next_state = State.from_vector(integration.values)
    next_state.validate()
    return StepResult(next_state, next_regime, probabilities, diagnostics, integration)


def simulate(
    initial_state: State,
    initial_regime: Regime,
    forcings: Iterable[Forcing],
    parameters: MarkovParameters,
    constants: PhysicalConstants,
    *,
    seed: int,
    duration_hours: float = 6.0,
) -> list[StepResult]:
    random_generator = random.Random(seed)
    state = initial_state
    regime = initial_regime
    results: list[StepResult] = []
    for forcing in forcings:
        result = step(
            state,
            regime,
            forcing,
            parameters,
            constants,
            random_generator,
            duration_hours=duration_hours,
        )
        results.append(result)
        state = result.state
        regime = result.regime
    return results


def observation_vector(state: State) -> tuple[float, float, float]:
    """Identity observation equation for V, Pc and RMW; m remains latent."""

    state.validate()
    return state.observable_vector()
