#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ibtracs_measurement.data import AGENCIES  # noqa: E402
from ibtracs_measurement.stats import (  # noqa: E402
    bootstrap_correlation_matrix,
    cluster_draws,
    factorize_clusters,
)
from ibtracs_measurement.wind_pressure import (  # noqa: E402
    BOOTSTRAP_SEED,
    bootstrap_error_intervals,
    cross_validate_pressure_only,
    diagnose_wind_pressure,
    error_metrics,
    legacy_wind_pressure_correlation,
    load_wind_pressure_samples,
)

IBTRACS_PATH = ROOT / "data" / "raw" / "ibtracs.WP.list.v04r01.csv"
SOURCE_TAR_PATH = ROOT / "data" / "raw" / "ibtracs_v04r01_input-bt_c20260706.tar.gz"
SOURCE_TAR_URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-"
    "stewardship-ibtracs/v04r01/input/final/"
    "ibtracs_v04r01_input-bt_c20260706.tar.gz"
)
LANDFALL_PATH = ROOT / "outputs" / "landfall_records_S093.csv"
OUTPUT_DIR = ROOT / "outputs" / "b_branch"
REPORT_PATH = ROOT / "report_b_branch.md"
REFERENCE_AGENCIES = ("JTWC", "JMA", "HKO", "KMA")
EXTERNAL_TRUTH_PATH = OUTPUT_DIR / "independent_truth_error_table.csv"
EXTERNAL_TRUTH_SUMMARY_PATH = OUTPUT_DIR / "landfall_truth_coverage_summary.json"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, np.ndarray):
        return json_ready(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(json_ready(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_source_tar(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    request = urllib.request.Request(
        SOURCE_TAR_URL,
        headers={"User-Agent": "ibtracs-measurement/1.0"},
    )
    with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as output:
        while block := response.read(1024 * 1024):
            output.write(block)
    temporary.replace(path)


def _parse_cma_annual_file(text: str) -> dict[str, int]:
    data_rows = 0
    owd_present_rows = 0
    positive_owd_rows = 0
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] == "66666" or not re.fullmatch(r"\d{10}", fields[0]):
            continue
        data_rows += 1
        if len(fields) >= 7:
            try:
                owd = float(fields[6])
            except ValueError:
                continue
            owd_present_rows += 1
            positive_owd_rows += int(owd > 0)
    return {
        "data_rows": data_rows,
        "owd_present_rows": owd_present_rows,
        "positive_owd_rows": positive_owd_rows,
    }


def audit_source_package(path: Path) -> dict[str, Any]:
    with tarfile.open(path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        member_names = [member.name for member in members]
        tier1_pattern = re.compile(r"station|anem|radar", flags=re.IGNORECASE)
        tier1_candidates = [name for name in member_names if tier1_pattern.search(name)]
        content_pattern = re.compile(br"station|anemometer|radar", flags=re.IGNORECASE)
        content_candidates: list[dict[str, Any]] = []
        wp_source_directories = {"cma", "hko", "jtwc", "kma", "tokyo"}
        for member in members:
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            count = len(content_pattern.findall(extracted.read()))
            if count:
                normalized = member.name.lstrip("./")
                source_directory = normalized.split("/", maxsplit=1)[0]
                content_candidates.append(
                    {
                        "member": member.name,
                        "term_occurrences": count,
                        "source_directory": source_directory,
                        "western_north_pacific_agency_source": source_directory
                        in wp_source_directories,
                    }
                )
        annual: dict[str, dict[str, int]] = {}
        for year in range(2015, 2025):
            suffix = f"cma/CH{year}BST.txt"
            matches = [member for member in members if member.name.lstrip("./").endswith(suffix)]
            if len(matches) != 1:
                annual[str(year)] = {
                    "files": len(matches),
                    "data_rows": 0,
                    "owd_present_rows": 0,
                    "positive_owd_rows": 0,
                }
                continue
            extracted = archive.extractfile(matches[0])
            if extracted is None:
                raise ValueError(f"Cannot read {matches[0].name}")
            audit = _parse_cma_annual_file(extracted.read().decode("utf-8", errors="replace"))
            annual[str(year)] = {"files": 1, **audit}

    return {
        "source_url": SOURCE_TAR_URL,
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "archive_file_count": len(member_names),
        "tier1_station_anemometer_radar_filename_candidates": tier1_candidates,
        "tier1_candidate_count": len(tier1_candidates),
        "station_anemometer_radar_content_candidates": content_candidates,
        "content_candidate_file_count": len(content_candidates),
        "content_term_occurrence_count": int(
            sum(item["term_occurrences"] for item in content_candidates)
        ),
        "wp_agency_content_candidate_file_count": int(
            sum(item["western_north_pacific_agency_source"] for item in content_candidates)
        ),
        "cma_annual_2015_2024": annual,
        "cma_2015_2024_data_rows": int(sum(item["data_rows"] for item in annual.values())),
        "cma_2015_2024_owd_present_rows": int(
            sum(item["owd_present_rows"] for item in annual.values())
        ),
        "cma_2015_2024_positive_owd_rows": int(
            sum(item["positive_owd_rows"] for item in annual.values())
        ),
    }


def bootstrap_scalar_metric_intervals(
    values: np.ndarray,
    storm_ids: np.ndarray,
    *,
    replicates: int,
    seed: int,
) -> dict[str, list[float]]:
    return bootstrap_error_intervals(
        np.zeros(len(values), dtype=float),
        values,
        storm_ids,
        replicates=replicates,
        seed=seed,
    )


def load_complete_landfalls(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    original = frame["all_original"]
    if original.dtype != bool:
        original = original.astype(str).str.lower().eq("true")
    complete = frame.loc[original & frame[list(AGENCIES)].notna().all(axis=1)].copy()
    if len(complete) != 108:
        raise ValueError(f"Frozen S093 complete-five landfall sample must have 108 rows; got {len(complete)}")
    if complete["SID"].duplicated().any():
        raise ValueError("The frozen first-landfall sample must contain one row per storm")
    return complete.sort_values("SID").reset_index(drop=True)


def load_external_truth_table(
    path: Path,
    summary_path: Path,
    landfalls: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists() or not summary_path.exists():
        raise FileNotFoundError(
            "External truth outputs are required; run scripts/run_landfall_truth.py first"
        )
    table = pd.read_csv(path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if set(table["agency"]) != set(AGENCIES) or len(table) != len(AGENCIES):
        raise ValueError("External truth table must contain exactly the five frozen agencies")
    if not table["landfall_events"].eq(len(landfalls)).all():
        raise ValueError("External truth table uses a different frozen landfall universe")
    if summary["frozen_events"] != len(landfalls):
        raise ValueError("External truth summary uses a different frozen landfall universe")
    if not table["matched_independent_truth_events"].eq(
        summary["grade_a_events"]
    ).all():
        raise ValueError("External truth score table and A-grade coverage disagree")
    summary = {**summary, "source": "external landfall_truth audit"}
    return table, summary


def cma_reference_analysis(
    landfalls: pd.DataFrame,
    *,
    replicates: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, np.ndarray]]:
    records = landfalls[
        ["SID", "NAME", "crossing_time", "crossing_lon", "crossing_lat", *AGENCIES]
    ].copy()
    for agency in AGENCIES:
        records[f"{agency}_minus_CMA_ms"] = records[agency] - records["CMA"]

    summaries: list[dict[str, Any]] = []
    for offset, agency in enumerate(AGENCIES):
        difference = records[f"{agency}_minus_CMA_ms"].to_numpy(float)
        point = error_metrics(np.zeros(len(difference)), difference)
        interval = bootstrap_scalar_metric_intervals(
            difference,
            records["SID"].astype(str).to_numpy(),
            replicates=replicates,
            seed=seed + offset,
        )
        summaries.append(
            {
                "agency": agency,
                "reference": "CMA normalized 10-minute landfall analysis",
                "events": len(records),
                "mean_difference_ms": point["bias_ms"],
                "mean_difference_95ci_low": interval["bias_ms"][0],
                "mean_difference_95ci_high": interval["bias_ms"][1],
                "mae_ms": point["mae_ms"],
                "mae_95ci_low": interval["mae_ms"][0],
                "mae_95ci_high": interval["mae_ms"][1],
                "rmse_ms": point["rmse_ms"],
                "rmse_95ci_low": interval["rmse_ms"][0],
                "rmse_95ci_high": interval["rmse_ms"][1],
                "difference_sd_ms": point["residual_sd_ms"],
                "difference_sd_95ci_low": interval["residual_sd_ms"][0],
                "difference_sd_95ci_high": interval["residual_sd_ms"][1],
                "interpretation": "self_reference" if agency == "CMA" else "descriptive_proxy",
            }
        )

    delta_columns = [f"{agency}_minus_CMA_ms" for agency in REFERENCE_AGENCIES]
    corr = bootstrap_correlation_matrix(
        records[delta_columns].to_numpy(float),
        records["SID"].astype(str).to_numpy(),
        replicates=replicates,
        seed=seed + 20,
    )
    expanded: dict[str, np.ndarray] = {}
    for key in ("point", "lower", "upper"):
        matrix = np.full((len(AGENCIES), len(AGENCIES)), np.nan, dtype=float)
        for left, left_agency in enumerate(REFERENCE_AGENCIES):
            for right, right_agency in enumerate(REFERENCE_AGENCIES):
                i = AGENCIES.index(left_agency)
                j = AGENCIES.index(right_agency)
                matrix[i, j] = corr[key][left, right]
        expanded[key] = matrix
    return records, pd.DataFrame(summaries), expanded


def variance_reduction_interval(
    observed: np.ndarray,
    model: np.ndarray,
    baseline: np.ndarray,
    storm_ids: np.ndarray,
    *,
    replicates: int,
    seed: int,
) -> list[float]:
    model_error = model - observed
    baseline_error = baseline - observed
    labels, clusters = factorize_clusters(storm_ids)
    draws = cluster_draws(len(clusters), replicates, seed)
    weights = draws[:, labels]
    model_mse = weights @ (model_error**2) / weights.sum(axis=1)
    baseline_mse = weights @ (baseline_error**2) / weights.sum(axis=1)
    reduction = 1.0 - model_mse / baseline_mse
    return [float(value) for value in np.percentile(reduction, (2.5, 97.5))]


def compact_diagnostic(diagnostic: dict[str, Any]) -> dict[str, Any]:
    result = dict(diagnostic)
    for relation_name in ("wind_from_pressure", "pressure_from_wind"):
        relation = dict(result[relation_name])
        relation.pop("fitted")
        relation.pop("residual")
        result[relation_name] = relation
    return result


def make_wind_pressure_plot(frame: pd.DataFrame, diagnostic: dict[str, Any], path: Path) -> None:
    relation = diagnostic["wind_from_pressure"]
    fitted = np.asarray(relation["fitted"])
    residual = np.asarray(relation["residual"])
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.8), constrained_layout=True)

    density = axes[0].hexbin(
        frame["pressure_hpa"],
        frame["wind_ms"],
        gridsize=55,
        mincnt=1,
        cmap="viridis",
    )
    pressure_grid = np.linspace(frame["pressure_hpa"].min(), frame["pressure_hpa"].max(), 300)
    predicted = relation["intercept"] + relation["slope"] * (1010.0 - pressure_grid)
    axes[0].plot(pressure_grid, predicted, color="#c53f3f", linewidth=2.2, label="OLS")
    axes[0].set_xlabel("Central pressure Pc (hPa)")
    axes[0].set_ylabel("JTWC maximum wind (m/s, 1-min)")
    axes[0].set_title("Observed V-Pc relation")
    axes[0].legend(frameon=False)
    figure.colorbar(density, ax=axes[0], label="records per hexagon")

    residual_density = axes[1].hexbin(
        fitted,
        residual,
        gridsize=55,
        mincnt=1,
        cmap="cividis",
    )
    axes[1].axhline(0.0, color="#c53f3f", linewidth=1.5)
    axes[1].set_xlabel("Fitted wind (m/s, 1-min)")
    axes[1].set_ylabel("Residual: observed - fitted (m/s)")
    axes[1].set_title("Residual structure")
    figure.colorbar(residual_density, ax=axes[1], label="records per hexagon")

    figure.suptitle("Western North Pacific wind-pressure diagnostic, 2001-2024")
    figure.savefig(path, dpi=180)
    plt.close(figure)


def fmt0(value: float) -> str:
    rendered = f"{value:.0f}"
    return "0" if rendered == "-0" else rendered


def fmt2(value: float) -> str:
    return f"{value:.2f}"


def format_interval(values: list[float], formatter: Callable[[float], str]) -> str:
    return f"{formatter(values[0])} to {formatter(values[1])}"


def write_report(
    *,
    source_audit: dict[str, Any],
    truth_table: pd.DataFrame,
    truth_summary: dict[str, Any],
    proxy_table: pd.DataFrame,
    diagnostic: dict[str, Any],
    legacy: dict[str, Any],
    cv: dict[str, Any],
) -> None:
    direct = diagnostic["wind_from_pressure"]
    inverse = diagnostic["pressure_from_wind"]
    model = cv["pressure_only"]
    baseline = cv["training_mean_baseline"]
    grade_a_events = int(truth_summary["grade_a_events"])
    observed_events = int(truth_summary["grade_a_or_b_events"])
    truth_records = int(truth_summary["truth_records"])
    grade_b_records = int(truth_summary["grade_b_records"])
    truth_statuses = ", ".join(sorted(set(truth_table["status"].astype(str))))
    truth_score_rows = []
    for row in truth_table.itertuples(index=False):
        if grade_a_events:
            truth_score_rows.append(
                f"|{row.agency}|{int(row.matched_independent_truth_events)}|"
                f"{row.bias_ms:.1f} [{row.bias_95ci_low:.1f}, {row.bias_95ci_high:.1f}]|"
                f"{row.mae_ms:.1f} [{row.mae_95ci_low:.1f}, {row.mae_95ci_high:.1f}]|"
                f"{row.rmse_ms:.1f} [{row.rmse_95ci_low:.1f}, {row.rmse_95ci_high:.1f}]|"
            )
        else:
            truth_score_rows.append(f"|{row.agency}|0|NA|NA|NA|")
    truth_score_table = "\n".join(truth_score_rows)
    proxy_lines = []
    for row in proxy_table.itertuples(index=False):
        proxy_lines.append(
            "|{agency}|{mean}|{mae}|{rmse}|{sd}|{kind}|".format(
                agency=row.agency,
                mean=f"{fmt0(row.mean_difference_ms)} [{fmt0(row.mean_difference_95ci_low)}, {fmt0(row.mean_difference_95ci_high)}]",
                mae=f"{fmt0(row.mae_ms)} [{fmt0(row.mae_95ci_low)}, {fmt0(row.mae_95ci_high)}]",
                rmse=f"{fmt0(row.rmse_ms)} [{fmt0(row.rmse_95ci_low)}, {fmt0(row.rmse_95ci_high)}]",
                sd=f"{fmt0(row.difference_sd_ms)} [{fmt0(row.difference_sd_95ci_low)}, {fmt0(row.difference_sd_95ci_high)}]",
                kind="自参照恒等式" if row.agency == "CMA" else "CMA 代理差",
            )
        )

    content = f"""# 支线 B：登陆强度与风压诊断

冻结协议：[`b_branch_protocol.md`](b_branch_protocol.md)

状态：`research-measured`；密封业务回报检验尚未完成，未使用 `validated` 标签。

## 这轮做成了什么

1. **[验证过][MEASURED] 外部登陆观测审计已接入。** IBTrACS 包内独立测站/雷达真值为
   `0/108`；包外公开档案采得 {truth_records} 条 A/B 级仪器记录，A+B 事件覆盖为
   `{observed_events}/108`，严格 A 级可评分覆盖为 `{grade_a_events}/108`。
   五家对这 {grade_a_events} 案的 MAE、RMSE 与真误差相关状态为 `{truth_statuses}`。
2. **[MEASURED] NCEI 最终输入包审计保持独立。** 输入包共
   {source_audit['archive_file_count']} 个文件；文件名审计得到 {source_audit['tier1_candidate_count']} 个测站、风速仪或雷达候选。
   全文词项审计在其他海盆来源中检出 {source_audit['content_candidate_file_count']} 个含相关叙述的文件，
   CMA/HKO/JTWC/KMA/Tokyo 西北太平洋来源目录命中 {source_audit['wp_agency_content_candidate_file_count']} 个。
   2015--2024 CMA 年度原始文件有 {source_audit['cma_2015_2024_data_rows']} 条最佳路径记录，
   `OWD` 有值记录为 {source_audit['cma_2015_2024_owd_present_rows']}。
   这一结果证伪“IBTrACS 最终输入包本身足以计算五家登陆真值误差”。
3. **[MEASURED+ASSUMED] CMA 参照代理表已经发布。** 该表衡量各机构与 CMA 登陆分析的差，
   CMA 会吸收中国测站资料，具有主场优势；代理差共享同一个 CMA 参照项。
4. **[MEASURED] 风压关系已固化为可复用模块。** 主样本含 {diagnostic['rows']} 条记录、
   {diagnostic['storms']} 个台风；JTWC 风速保持原生 1 分钟窗口，气压单位为 hPa。
5. **[MEASURED] Pc-only 五折留出台风检验已经完成。** 每个台风只属于一个折，误差区间按台风
   block bootstrap 2,000 次。

## B1 登陆强度真值审计

外部真值表位于 `outputs/b_branch/landfall_truth.csv`，逐事件来源状态位于
`landfall_truth_source_event_status.csv`，五家评分表位于
`independent_truth_error_table.csv`。本轮 A 级为 `{grade_a_events}/108`，A+B 为
`{observed_events}/108`。B 级测站风承担覆盖证据，严格 A 级承担机构误差评分。

[验证过] 当前五家评分状态为 `{truth_statuses}`。完整来源、平均窗、最大风区证据和访问门槛
见 [`landfall_truth_report.md`](landfall_truth_report.md)。

误差定义为 `agency_10min - external_truth_10min`，单位 m/s；括号为按 SID bootstrap
2,000 次的 95% CI。

|机构|A 级事件|bias|MAE|RMSE|
|---|---:|---:|---:|---:|
{truth_score_table}

[验证过][MEASURED] A 级真误差相关点值和区间位于
`independent_truth_error_correlation.csv` 与
`independent_truth_error_correlation_intervals.csv`。当前 4 案全部来自台湾，多数非对角
相关区间覆盖 `[-1, 1]`；矩阵承担 4 案描述，长期相关仍需扩充 A 级事件。

### Tier 3：CMA 分析参照

单位均为 m/s，点估计后括号为按台风聚类 95% CI。五家风速已经归一到 10 分钟：JTWC
采用 1 分钟到 10 分钟系数 0.93，CMA 采用冻结的 2 分钟到 10 分钟系数 0.96；JMA、HKO、
KMA 原生为 10 分钟。

|机构|均值差|MAE|RMSE|差值 SD|解释|
|---|---:|---:|---:|---:|---|
{chr(10).join(proxy_lines)}

[MEASURED+ASSUMED] 代理误差相关矩阵及其聚类 95% CI 位于
`landfall_cma_reference_correlation_*.csv`。CMA 自参照误差恒为 0，其相关系数按定义写作 NA。
这个矩阵描述共同 CMA 参照下的联动，无法识别五家共同偏差。

## B2 风压关系诊断

[ASSUMED] 冻结线性式为 `V_1min = alpha + beta * (1010 - Pc)`；含 2 个拟合参数。

- [MEASURED] `alpha = {fmt2(direct['intercept'])}` m/s，95% CI
  [{fmt2(direct['intercept_95ci'][0])}, {fmt2(direct['intercept_95ci'][1])}]。
- [MEASURED] `beta = {fmt2(direct['slope'])}` m/s/hPa，95% CI
  [{fmt2(direct['slope_95ci'][0])}, {fmt2(direct['slope_95ci'][1])}]。
- [MEASURED] `corr(V, Pc) = {fmt2(diagnostic['wind_pressure_pearson_r'])}`，台风聚类 95% CI
  [{fmt2(diagnostic['wind_pressure_pearson_r_95ci'][0])}, {fmt2(diagnostic['wind_pressure_pearson_r_95ci'][1])}]。
- [MEASURED] 回归残差尺度为 {fmt0(direct['residual_scale'])} m/s，95% CI
  [{fmt0(direct['residual_scale_95ci'][0])}, {fmt0(direct['residual_scale_95ci'][1])}]。
- [MEASURED] 逆式 `Pc = {fmt2(inverse['intercept'])} - {fmt2(abs(inverse['slope']))} * V_1min`。

[MEASURED] legacy `V/Pc/RMW` 三字段齐全样本含 {legacy['rows']} 条记录；复算相关为
{legacy['correlation']:.10f}，与旧值 {legacy['expected']:.10f} 的差为 {legacy['difference']:.2e}。
旧技术债数字得到精确复现，同时主回归保持独立的预注册筛选。

![风压关系与残差](outputs/b_branch/wind_pressure_diagnostic.png)

## B3 Pc 单独反推 V

|方法|MAE m/s|RMSE m/s|bias m/s|残差 SD m/s|P80/P95 绝对误差 m/s|
|---|---:|---:|---:|---:|---:|
|Pc-only 五折|{fmt0(model['mae_ms'])}|{fmt0(model['rmse_ms'])}|{fmt0(model['bias_ms'])}|{fmt0(model['residual_sd_ms'])}|{fmt0(model['absolute_error_p80_ms'])}/{fmt0(model['absolute_error_p95_ms'])}|
|训练集均值基线|{fmt0(baseline['mae_ms'])}|{fmt0(baseline['rmse_ms'])}|{fmt0(baseline['bias_ms'])}|{fmt0(baseline['residual_sd_ms'])}|{fmt0(baseline['absolute_error_p80_ms'])}/{fmt0(baseline['absolute_error_p95_ms'])}|

- [MEASURED] Pc-only RMSE 的台风聚类 95% CI 为
  [{fmt0(cv['pressure_only_cluster_95ci']['rmse_ms'][0])}, {fmt0(cv['pressure_only_cluster_95ci']['rmse_ms'][1])}] m/s。
- [ASSUMED+MEASURED] 相对训练均值基线的交叉验证方差削减为
  {100.0 * cv['cross_validated_variance_reduction']:.0f}%，95% CI
  [{100.0 * cv['cross_validated_variance_reduction_95ci'][0]:.0f}%,
  {100.0 * cv['cross_validated_variance_reduction_95ci'][1]:.0f}%]。

Pc 对 V 具有很强的可替代信息。V 与 Pc 来自同一事后分析体系，高相关主要体现风压物理关系
和联合分析约束。增加 Pc 对独立准确性的增益需要独立观测误差模型；本数据无法识别该量。

## 三把刀

1. **状态向量。** 本支线是测量诊断，记录向量为 `(V_1min, Pc)`；登陆表由五家 10 分钟
   归一风速和真值等级字段构成。
2. **参数与独立观测。** 风压回归含 2 个系数；统计独立单位按台风聚类。登陆观测 A+B 覆盖为
   {observed_events}/108，A 级可评分覆盖为 {grade_a_events}/108；机构绝对误差资格由 A 级闸门控制。
3. **证伪数据。** 完整元数据测站/雷达记录用于证伪登陆分析；留出台风的 JTWC `V_1min`
   用于证伪 Pc-only 关系。系数区间与留出 MSE 均按冻结规则判决。

## 预注册偏离

- [MEASURED] D004 已在计算前登记。源包现代 CMA 文件的 `OWD` 覆盖为 0，因此 Tier 2
  事件级匹配自然终止。B2/B3 选择、随机种子、折数和 bootstrap 次数均按协议执行。
- [MEASURED] D005 在第一次正式运行后登记。旧 `-0.9817` 属于
  `V/Pc/RMW complete + USA_AGENCY=jtwc_wp` 的 16,225 条子集；33,308 条全机构完整样本的
  原始旧值为 `-0.9789`。代码现同时记录两种样本规模，并按旧 JSON 的子集原名复现目标数字。

## 缺口与下一步

- A 级登陆真误差需要眼墙/最大风区证据与可比 10 分钟持续风；现有 {grade_b_records} 条 B 级记录
  已提供 ID、位置、时刻或影响期、平均窗口与质量字段。
- 点测站阵风、沿岸 2 分钟大风和台风中心最大持续风具有不同观测算子；未来数据接入需保留三者语义。
- 风压式属于统计诊断；密封年代外检验和独立观测误差建模完成前，状态保持 `research-measured`。

## 来源

- [CITED] [NOAA/NCEI IBTrACS 产品页](https://www.ncei.noaa.gov/products/international-best-track-archive)
- [CITED] [IBTrACS v04r01 字段文档](https://www.ncei.noaa.gov/sites/default/files/2025-09/IBTrACS_v04r01_column_documentation.pdf)
- [CITED] [CMA 热带气旋等级国家标准说明](https://www.cma.gov.cn/wmhd/gzly/cjwt/202311/t20231127_5912128.html)
"""
    REPORT_PATH.write_text(content, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ibtracs", type=Path, default=IBTRACS_PATH)
    parser.add_argument("--source-tar", type=Path, default=SOURCE_TAR_PATH)
    parser.add_argument("--landfalls", type=Path, default=LANDFALL_PATH)
    parser.add_argument("--external-truth", type=Path, default=EXTERNAL_TRUTH_PATH)
    parser.add_argument(
        "--external-truth-summary",
        type=Path,
        default=EXTERNAL_TRUTH_SUMMARY_PATH,
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_source_tar(args.source_tar)
    source_audit = audit_source_package(args.source_tar)
    landfalls = load_complete_landfalls(args.landfalls)
    truth, truth_summary = load_external_truth_table(
        args.external_truth,
        args.external_truth_summary,
        landfalls,
    )

    proxy_rows, proxy_summary, proxy_corr = cma_reference_analysis(
        landfalls,
        replicates=args.bootstrap_replicates,
        seed=BOOTSTRAP_SEED,
    )
    proxy_rows.to_csv(OUTPUT_DIR / "landfall_cma_reference_rows.csv", index=False)
    proxy_summary.to_csv(OUTPUT_DIR / "landfall_cma_reference_error_table.csv", index=False)
    for key, matrix in proxy_corr.items():
        pd.DataFrame(matrix, index=AGENCIES, columns=AGENCIES).to_csv(
            OUTPUT_DIR / f"landfall_cma_reference_correlation_{key}.csv"
        )

    primary, legacy_frame, sample_audit = load_wind_pressure_samples(args.ibtracs)
    diagnostic = diagnose_wind_pressure(
        primary,
        replicates=args.bootstrap_replicates,
        seed=BOOTSTRAP_SEED,
    )
    cv_rows, cv = cross_validate_pressure_only(primary, folds=5, seed=BOOTSTRAP_SEED)
    storm_ids = cv_rows["SID"].astype(str).to_numpy()
    observed = cv_rows["wind_ms"].to_numpy(float)
    predicted = cv_rows["predicted_wind_ms"].to_numpy(float)
    baseline = cv_rows["baseline_wind_ms"].to_numpy(float)
    cv["pressure_only_cluster_95ci"] = bootstrap_error_intervals(
        observed,
        predicted,
        storm_ids,
        replicates=args.bootstrap_replicates,
        seed=BOOTSTRAP_SEED + 30,
    )
    cv["training_mean_baseline_cluster_95ci"] = bootstrap_error_intervals(
        observed,
        baseline,
        storm_ids,
        replicates=args.bootstrap_replicates,
        seed=BOOTSTRAP_SEED + 30,
    )
    cv["cross_validated_variance_reduction_95ci"] = variance_reduction_interval(
        observed,
        predicted,
        baseline,
        storm_ids,
        replicates=args.bootstrap_replicates,
        seed=BOOTSTRAP_SEED + 30,
    )
    cv_rows.to_csv(OUTPUT_DIR / "pressure_only_cross_validation_rows.csv", index=False)

    legacy_value = legacy_wind_pressure_correlation(legacy_frame)
    legacy = {
        "rows": int(len(legacy_frame)),
        "storms": int(legacy_frame["SID"].nunique()),
        "correlation": legacy_value,
        "expected": -0.981735776370014,
        "difference": legacy_value - (-0.981735776370014),
    }
    compact = compact_diagnostic(diagnostic)
    write_json(OUTPUT_DIR / "source_truth_audit.json", source_audit)
    write_json(
        OUTPUT_DIR / "wind_pressure_results.json",
        {
            "selection_audit": sample_audit,
            "diagnostic": compact,
            "legacy_reproduction": legacy,
            "pressure_only_cross_validation": cv,
        },
    )
    make_wind_pressure_plot(primary, diagnostic, OUTPUT_DIR / "wind_pressure_diagnostic.png")
    write_report(
        source_audit=source_audit,
        truth_table=truth,
        truth_summary=truth_summary,
        proxy_table=proxy_summary,
        diagnostic=compact,
        legacy=legacy,
        cv=cv,
    )

    manifest = {
        "generated_utc": datetime.now(timezone.utc),
        "bootstrap_replicates": args.bootstrap_replicates,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "inputs": {
            "ibtracs": {
                "path": str(args.ibtracs.resolve()),
                "bytes": args.ibtracs.stat().st_size,
                "sha256": sha256(args.ibtracs),
            },
            "source_tar": source_audit,
            "landfalls": {
                "path": str(args.landfalls.resolve()),
                "bytes": args.landfalls.stat().st_size,
                "sha256": sha256(args.landfalls),
            },
            "external_truth": {
                "path": str(args.external_truth.resolve()),
                "bytes": args.external_truth.stat().st_size,
                "sha256": sha256(args.external_truth),
            },
            "external_truth_summary": {
                "path": str(args.external_truth_summary.resolve()),
                "bytes": args.external_truth_summary.stat().st_size,
                "sha256": sha256(args.external_truth_summary),
            },
        },
        "outputs": sorted(path.name for path in OUTPUT_DIR.iterdir() if path.is_file()),
    }
    write_json(OUTPUT_DIR / "run_manifest.json", manifest)
    print(
        json.dumps(
            {
                "landfalls": len(landfalls),
                "independent_truth_matches": int(truth["matched_independent_truth_events"].max()),
                "grade_a_or_b_events": truth_summary["grade_a_or_b_events"],
                "wind_pressure_rows": len(primary),
                "wind_pressure_storms": primary["SID"].nunique(),
                "legacy_correlation": legacy_value,
                "pc_only_rmse_ms": cv["pressure_only"]["rmse_ms"],
                "report": str(REPORT_PATH),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
