from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BootstrapResult:
    point: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    replicates: np.ndarray


def pairwise_sd_matrix(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    count = values.shape[1]
    result = np.zeros((count, count), dtype=float)
    for left in range(count):
        for right in range(left + 1, count):
            valid = np.isfinite(values[:, left]) & np.isfinite(values[:, right])
            difference = values[valid, left] - values[valid, right]
            value = np.std(difference, ddof=1) if difference.size >= 2 else np.nan
            result[left, right] = result[right, left] = value
    return result


def pairwise_count_matrix(values: np.ndarray, storm_ids: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(values, dtype=float)
    storm_ids = np.asarray(storm_ids)
    count = values.shape[1]
    records = np.zeros((count, count), dtype=int)
    storms = np.zeros((count, count), dtype=int)
    for left in range(count):
        for right in range(count):
            valid = np.isfinite(values[:, left]) & np.isfinite(values[:, right])
            records[left, right] = int(valid.sum())
            storms[left, right] = int(np.unique(storm_ids[valid]).size)
    return records, storms


def leave_one_out_deviations(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    count = values.shape[1]
    if not np.isfinite(values).all():
        raise ValueError("Leave-one-out deviations require complete rows")
    return values - (values.sum(axis=1, keepdims=True) - values) / (count - 1)


def correlation_matrix(values: np.ndarray) -> np.ndarray:
    return np.corrcoef(np.asarray(values, dtype=float), rowvar=False)


def average_off_diagonal(correlation: np.ndarray) -> float:
    count = correlation.shape[0]
    indices = np.triu_indices(count, k=1)
    return float(np.mean(correlation[indices]))


def neff_from_rho(rho_bar: float, count: int = 5) -> tuple[float, float]:
    denominator = 1.0 + (count - 1) * rho_bar
    neff = count / denominator if denominator != 0 else np.copysign(np.inf, denominator)
    return denominator, neff


def factorize_clusters(storm_ids: Sequence[str]) -> tuple[np.ndarray, np.ndarray]:
    labels, uniques = pd.factorize(np.asarray(storm_ids), sort=True)
    if (labels < 0).any():
        raise ValueError("Storm identifiers must be present")
    return labels, uniques


def cluster_draws(cluster_count: int, replicates: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    probabilities = np.full(cluster_count, 1.0 / cluster_count)
    return rng.multinomial(cluster_count, probabilities, size=replicates).astype(float)


def _cluster_scalar_moments(values: np.ndarray, labels: np.ndarray, cluster_count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    valid = np.isfinite(values)
    counts = np.bincount(labels[valid], minlength=cluster_count).astype(float)
    sums = np.bincount(labels[valid], weights=values[valid], minlength=cluster_count)
    squares = np.bincount(labels[valid], weights=values[valid] ** 2, minlength=cluster_count)
    return counts, sums, squares


def bootstrap_pairwise_sd(
    values: np.ndarray,
    storm_ids: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = 20260712,
) -> BootstrapResult:
    values = np.asarray(values, dtype=float)
    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    count = values.shape[1]
    point = pairwise_sd_matrix(values)
    boot = np.zeros((replicates, count, count), dtype=float)
    for left in range(count):
        for right in range(left + 1, count):
            difference = values[:, left] - values[:, right]
            n_cluster, sum_cluster, square_cluster = _cluster_scalar_moments(
                difference, labels, len(clusters)
            )
            n = draws @ n_cluster
            total = draws @ sum_cluster
            square = draws @ square_cluster
            variance = (square - total**2 / n) / (n - 1.0)
            estimate = np.sqrt(np.maximum(variance, 0.0))
            boot[:, left, right] = boot[:, right, left] = estimate
    lower, upper = np.nanpercentile(boot, (2.5, 97.5), axis=0)
    return BootstrapResult(point=point, lower=lower, upper=upper, replicates=boot)


def _cluster_vector_moments(
    values: np.ndarray, labels: np.ndarray, cluster_count: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows, columns = values.shape
    counts = np.bincount(labels, minlength=cluster_count).astype(float)
    sums = np.zeros((cluster_count, columns), dtype=float)
    cross = np.zeros((cluster_count, columns, columns), dtype=float)
    for cluster in range(cluster_count):
        subset = values[labels == cluster]
        sums[cluster] = subset.sum(axis=0)
        cross[cluster] = subset.T @ subset
    if counts.sum() != rows:
        raise AssertionError("Cluster moment count mismatch")
    return counts, sums, cross


def bootstrap_neff(
    values: np.ndarray,
    storm_ids: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = 20260712,
) -> dict[str, object]:
    deviations = leave_one_out_deviations(values)
    point_corr = correlation_matrix(deviations)
    point_rho = average_off_diagonal(point_corr)
    point_denominator, point_neff = neff_from_rho(point_rho, values.shape[1])

    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    counts, sums, cross = _cluster_vector_moments(deviations, labels, len(clusters))
    n = draws @ counts
    totals = draws @ sums
    second = np.tensordot(draws, cross, axes=(1, 0))
    means = totals / n[:, None]
    covariance = second / n[:, None, None] - means[:, :, None] * means[:, None, :]
    variances = np.diagonal(covariance, axis1=1, axis2=2)
    scale = np.sqrt(np.maximum(variances[:, :, None] * variances[:, None, :], 0.0))
    correlations = np.divide(
        covariance,
        scale,
        out=np.full_like(covariance, np.nan),
        where=scale > 0,
    )
    upper_indices = np.triu_indices(values.shape[1], k=1)
    rho = np.nanmean(correlations[:, upper_indices[0], upper_indices[1]], axis=1)
    denominator = 1.0 + (values.shape[1] - 1) * rho
    neff = np.divide(
        values.shape[1],
        denominator,
        out=np.full_like(denominator, np.nan),
        where=denominator != 0,
    )
    rho_interval = np.nanpercentile(rho, (2.5, 97.5))
    denominator_interval = np.nanpercentile(denominator, (2.5, 97.5))
    neff_interval = np.nanpercentile(neff, (2.5, 97.5))
    finite_identifiable = bool(
        denominator_interval[0] > 0
        and neff_interval[0] >= 1
        and neff_interval[1] <= values.shape[1]
    )
    return {
        "correlation_matrix": point_corr,
        "rho_bar": point_rho,
        "denominator": point_denominator,
        "neff": point_neff,
        "rho_interval": rho_interval,
        "denominator_interval": denominator_interval,
        "neff_interval": neff_interval,
        "denominator_nonpositive_fraction": float(np.mean(denominator <= 0)),
        "finite_identifiable": finite_identifiable,
        "bootstrap_rho": rho,
        "bootstrap_denominator": denominator,
        "bootstrap_neff": neff,
    }


def bootstrap_correlation_matrix(
    values: np.ndarray,
    storm_ids: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = 20260712,
) -> dict[str, np.ndarray]:
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Correlation bootstrap requires complete rows")
    point = correlation_matrix(values)
    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    counts, sums, cross = _cluster_vector_moments(values, labels, len(clusters))
    n = draws @ counts
    totals = draws @ sums
    second = np.tensordot(draws, cross, axes=(1, 0))
    means = totals / n[:, None]
    covariance = second / n[:, None, None] - means[:, :, None] * means[:, None, :]
    variances = np.diagonal(covariance, axis1=1, axis2=2)
    scale = np.sqrt(np.maximum(variances[:, :, None] * variances[:, None, :], 0.0))
    boot = np.divide(
        covariance,
        scale,
        out=np.full_like(covariance, np.nan),
        where=scale > 0,
    )
    lower, upper = np.nanpercentile(boot, (2.5, 97.5), axis=0)
    return {"point": point, "lower": lower, "upper": upper, "replicates": boot}


def bootstrap_mean_interval(
    values: np.ndarray,
    storm_ids: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = 20260712,
) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    counts, sums, _ = _cluster_scalar_moments(values, labels, len(clusters))
    estimates = (draws @ sums) / (draws @ counts)
    lower, upper = np.nanpercentile(estimates, (2.5, 97.5))
    return float(np.mean(values)), float(lower), float(upper)


def kish_effective_cluster_count(storm_ids: Sequence[str]) -> float:
    _, counts = np.unique(np.asarray(storm_ids), return_counts=True)
    return float(counts.sum() ** 2 / np.sum(counts**2))


def _cluster_regression_moments(
    x: np.ndarray, y: np.ndarray, labels: np.ndarray, cluster_count: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    columns = x.shape[1]
    xtx = np.zeros((cluster_count, columns, columns), dtype=float)
    xty = np.zeros((cluster_count, columns), dtype=float)
    yty = np.zeros(cluster_count, dtype=float)
    counts = np.zeros(cluster_count, dtype=float)
    for cluster in range(cluster_count):
        selected = labels == cluster
        xc = x[selected]
        yc = y[selected]
        xtx[cluster] = xc.T @ xc
        xty[cluster] = xc.T @ yc
        yty[cluster] = yc @ yc
        counts[cluster] = selected.sum()
    return xtx, xty, yty, counts


def fit_cluster_bootstrap_regression(
    x: np.ndarray,
    y: np.ndarray,
    storm_ids: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = 20260712,
) -> dict[str, np.ndarray | float]:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ beta
    rss = float(residual @ residual)
    n = len(y)
    aic = float(n * np.log(rss / n) + 2 * x.shape[1])

    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    xtx, xty, _, _ = _cluster_regression_moments(x, y, labels, len(clusters))
    boot_xtx = np.tensordot(draws, xtx, axes=(1, 0))
    boot_xty = draws @ xty
    boot_beta = np.empty((replicates, x.shape[1]), dtype=float)
    for index in range(replicates):
        try:
            boot_beta[index] = np.linalg.solve(boot_xtx[index], boot_xty[index])
        except np.linalg.LinAlgError:
            boot_beta[index] = np.linalg.lstsq(boot_xtx[index], boot_xty[index], rcond=None)[0]
    lower, upper = np.nanpercentile(boot_beta, (2.5, 97.5), axis=0)
    return {
        "beta": beta,
        "lower": lower,
        "upper": upper,
        "bootstrap_beta": boot_beta,
        "rss": rss,
        "aic": aic,
    }


def weighted_bootstrap_median_interval(
    values: np.ndarray,
    storm_ids: Sequence[str],
    *,
    replicates: int = 2000,
    seed: int = 20260712,
) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    order = np.argsort(values)
    sorted_values = values[order]
    labels, clusters = factorize_clusters(storm_ids)
    sorted_labels = labels[order]
    draws = cluster_draws(len(clusters), replicates, seed)
    weights = draws[:, sorted_labels]
    cumulative = np.cumsum(weights, axis=1)
    targets = weights.sum(axis=1) * 0.5
    positions = np.argmax(cumulative >= targets[:, None], axis=1)
    boot = sorted_values[positions]
    lower, upper = np.percentile(boot, (2.5, 97.5))
    return float(np.median(values)), float(lower), float(upper)
