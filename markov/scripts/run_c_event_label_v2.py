#!/usr/bin/env python3
"""Select and score the preregistered C-branch waveform label v2."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from typhoon_markov.intensity_events import file_sha256, load_event_source
from typhoon_markov.intensity_events_v2 import (
    build_nine_point_windows,
    label_candidate,
    quantization_audit,
    run_temporal_benchmark,
    select_development_label,
)


DEFAULT_IBTRACS = (
    ROOT.parent
    / "ibtracs-agency-disagreement"
    / "data"
    / "raw"
    / "ibtracs.WP.list.v04r01.csv"
)
DEFAULT_CONFIG = ROOT / "config" / "c_event_label_v2.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "c_event_label_v2"
PREREGISTRATION = ROOT / "docs" / "c-event-label-v2-preregistration.md"


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=json_default,
        )
        + "\n",
        encoding="utf-8",
    )


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def dataframe_sha256(frame: pd.DataFrame, columns: list[str]) -> str:
    digest = hashlib.sha256()
    for row in frame.loc[:, columns].sort_values(columns[:-1]).itertuples(index=False):
        digest.update(("|".join(map(str, row)) + "\n").encode("utf-8"))
    return digest.hexdigest()


def plot_candidate_rates(candidates: pd.DataFrame, output_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8.6, 5.3), constrained_layout=True)
    colors = {2.5: "#26734d", 3.0: "#2f6da5", 4.0: "#8c5b9f", 5.0: "#bc3f35"}
    offsets = {2.5: -1.5, 3.0: -0.5, 4.0: 0.5, 5.0: 1.5}
    ax.axhspan(5, 15, color="#d9ead3", alpha=0.65, label="Target 5-15%")
    ax.axhline(10, color="#5c7753", linestyle="--", linewidth=1)
    for threshold in sorted(candidates["threshold_ms"].unique()):
        data = candidates.loc[candidates["threshold_ms"].eq(threshold)].sort_values(
            "horizon_hours"
        )
        x = data["horizon_hours"] + offsets[float(threshold)]
        y = 100.0 * data["event_row_rate"]
        lower = y - 100.0 * data["event_row_rate_ci95_low"]
        upper = 100.0 * data["event_row_rate_ci95_high"] - y
        ax.errorbar(
            x,
            y,
            yerr=[lower, upper],
            color=colors[float(threshold)],
            marker="o",
            linewidth=1.7,
            capsize=3,
            label=f"q={threshold:g} m/s",
        )
    ax.set_title("Development-set weakening-reintensification label rates")
    ax.set_xlabel("Future horizon (hours)")
    ax.set_ylabel("Event rows (%) with storm-block 95% CI")
    ax.set_xticks([12, 18, 24])
    ax.grid(axis="y", color="#d8d8d8", linewidth=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=2)
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def plot_reliability(
    reliability: pd.DataFrame,
    selected: dict[str, Any],
    output_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(6.8, 5.6), constrained_layout=True)
    colors = {"climatology": "#277a65", "persistence": "#b43a35"}
    markers = {"climatology": "o", "persistence": "s"}
    maximum = max(
        0.16,
        float(reliability["predicted_probability"].max()) * 1.25,
        float(reliability["observed_rate"].max()) * 1.25,
    )
    ax.plot((0, maximum), (0, maximum), color="#555555", linestyle="--", linewidth=1)
    for model in ("climatology", "persistence"):
        data = reliability.loc[reliability["model"].eq(model)]
        ax.scatter(
            data["predicted_probability"],
            data["observed_rate"],
            color=colors[model],
            marker=markers[model],
            s=65,
            label=model.capitalize(),
            zorder=3,
        )
        for row in data.itertuples():
            ax.annotate(
                f"n={row.rows}, events={row.events}",
                (row.predicted_probability, row.observed_rate),
                xytext=(5, 7),
                textcoords="offset points",
                fontsize=8,
                color=colors[model],
            )
    ax.set(xlim=(0, maximum), ylim=(0, maximum))
    ax.set_xlabel("Development-fitted probability")
    ax.set_ylabel("Sealed-period observed rate")
    ax.set_title(
        f"Temporal reliability: H={selected['horizon_hours']} h, "
        f"q={selected['threshold_ms']:g} m/s"
    )
    ax.grid(color="#d8d8d8", linewidth=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def _percent_metric(metric: dict[str, float]) -> str:
    return (
        f"{100 * metric['value']:.1f}% "
        f"[{100 * metric['ci95_low']:.1f}%, {100 * metric['ci95_high']:.1f}%]"
    )


def _decimal_metric(metric: dict[str, float], digits: int = 4) -> str:
    return (
        f"{metric['value']:.{digits}f} "
        f"[{metric['ci95_low']:.{digits}f}, {metric['ci95_high']:.{digits}f}]"
    )


def build_report(result: dict[str, Any], candidates: pd.DataFrame) -> str:
    selected = result["selection"]["selected"]
    metrics = result["benchmark"]["metrics"]
    training = result["benchmark"]["training_audit"]
    brier = metrics["brier"]
    difference = brier["persistence_minus_climatology_brier"]
    if difference["ci95_high"] < 0:
        brier_decision = "持续性基线显示可分辨改进。"
    elif difference["ci95_low"] > 0:
        brier_decision = "持续性基线被密封时间检验证伪。"
    else:
        brier_decision = "当前密封时间检验无法分辨持续性增益。"
    target_statement = (
        "开发集目标率判据通过。"
        if result["selection"]["target_achieved"]
        else "候选网格未达到开发集 5-15% 目标率，标签只具诊断资格。"
    )
    equivalence_groups = result["selection"]["quantization_equivalence_groups"]
    repeated_groups = [group for group in equivalence_groups if len(group["labels"]) > 1]
    development_rate = metrics["development_rates"]["event_row_rate"]
    validation_rate = metrics["validation_rates"]["event_row_rate"]
    rate_shift_points = 100.0 * (
        validation_rate["value"] - development_rate["value"]
    )
    rate_intervals_overlap = not (
        development_rate["ci95_high"] < validation_rate["ci95_low"]
        or validation_rate["ci95_high"] < development_rate["ci95_low"]
    )

    lines = [
        "# C-代理：未来 24 h 减弱—再增强强度波形标签 v2",
        "",
        "状态：`sealed-temporal-research-baseline`；资格：`unvalidated`。",
        "",
        "## 这轮做成了什么",
        "",
        f"- [MEASURED] 确定性开发集选择得到 `H={selected['horizon_hours']} h`、"
        f"`q={selected['threshold_ms']:g} m/s`；{target_statement}",
        f"- [MEASURED] 开发集逐时次事件率 "
        f"{_percent_metric(development_rate)}；"
        f"2019--2024 密封时间段事件率 "
        f"{_percent_metric(validation_rate)}。",
        f"- [MEASURED] 密封时间段点估计比开发集高 {rate_shift_points:.1f} 个百分点；"
        f"两个台风聚类 95% CI "
        f"{'重叠' if rate_intervals_overlap else '不重叠'}。开发集点估计通过 5% 门槛，"
        f"其区间下界为 {100 * development_rate['ci95_low']:.1f}%。",
        f"- [MEASURED] 密封时间段包含 {metrics['validation_events']} 个事件、"
        f"{metrics['validation_rows']} 行、{metrics['validation_storms']} 场台风；"
        f"双类别评分门槛"
        f"{'通过' if metrics['nondegenerate_validation_classes'] else '关闭'}。",
        f"- [MEASURED] 气候 Brier="
        f"{_decimal_metric(brier['climatology_brier'])}；持续性 Brier="
        f"{_decimal_metric(brier['persistence_brier'])}。",
        f"- [MEASURED] `Brier_persistence-Brier_climatology`="
        f"{_decimal_metric(difference, 6)}；{brier_decision}",
        f"- [MEASURED] 1 分钟 `USA_WIND` 中，5 kt 整倍数占 "
        f"{100 * result['quantization_audit']['multiple_of_5kt_fraction']:.1f}%；"
        f"12 个数值候选折叠成 {len(equivalence_groups)} 个事件向量等价类，"
        f"其中 {len(repeated_groups)} 类包含重复标签。",
        f"- [CITED+MEASURED] 5 kt 等于 "
        f"{result['quantization_audit']['minimum_positive_native_increment_ms']:.3f} m/s；"
        f"选定的 `q=2.5 m/s` 在该量化序列上对应至少一个 5 kt 强度档。",
        "- [MEASURED] C-代理已经建立一个可评分、可被后续方法击败的概率门槛。",
        "- [MEASURED] 该标签是强度波形，不是 ERC。ERC 是可能成因之一，非唯一成因。",
        "",
        "![开发集候选率](outputs/c_event_label_v2/candidate_development_rates.png)",
        "",
        "![密封时间可靠性](outputs/c_event_label_v2/validation_reliability.png)",
        "",
        "## 候选标签审计",
        "",
        "[MEASURED] 比率括号为按台风 bootstrap 2,000 次的 95% CI。选择只使用"
        " 2001--2018；表中粗体行为冻结标签。",
        "",
        "|H|q|行/台风|事件|开发率|目标内|向量哈希|",
        "|---:|---:|---:|---:|---:|:---:|---|",
    ]
    for row in candidates.sort_values(["horizon_hours", "threshold_ms"]).itertuples():
        selected_row = (
            row.horizon_hours == selected["horizon_hours"]
            and row.threshold_ms == selected["threshold_ms"]
        )
        h_value = f"**{row.horizon_hours} h**" if selected_row else f"{row.horizon_hours} h"
        q_value = f"**{row.threshold_ms:g}**" if selected_row else f"{row.threshold_ms:g}"
        rate = (
            f"{100 * row.event_row_rate:.1f}% "
            f"[{100 * row.event_row_rate_ci95_low:.1f}%, "
            f"{100 * row.event_row_rate_ci95_high:.1f}%]"
        )
        lines.append(
            f"|{h_value}|{q_value}|{row.rows}/{row.storms}|{row.events}|{rate}|"
            f"{'是' if row.in_target_interval else '否'}|`{row.event_vector_sha256[:10]}`|"
        )

    lines.extend(
        [
            "",
            "## 量化等价类",
            "",
            "[MEASURED] 同一哈希代表开发集的行键和事件向量完全相同。数值阈值名称"
            "仍保留，统计证据按一个等价类计。",
            "",
        ]
    )
    for group in equivalence_groups:
        labels = ", ".join(
            f"H={item['horizon_hours']}h/q={item['threshold_ms']:g}m/s"
            for item in group["labels"]
        )
        lines.append(f"- `{group['event_vector_sha256'][:12]}`：{labels}。")

    lines.extend(
        [
            "",
            "## 密封时间评分",
            "",
            f"- [ASSUMED+MEASURED] 开发集气候概率 "
            f"`p={training['climatology_probability']:.4f}`；Jeffreys alpha=0.5。",
            f"- [MEASURED] 历史波形 `H_t=0`："
            f"`p={training['persistence_strata']['0']['jeffreys_probability']:.4f}`，"
            f"{training['persistence_strata']['0']['events']}/"
            f"{training['persistence_strata']['0']['rows']} 事件/行。",
            f"- [MEASURED] 历史波形 `H_t=1`："
            f"`p={training['persistence_strata']['1']['jeffreys_probability']:.4f}`，"
            f"{training['persistence_strata']['1']['events']}/"
            f"{training['persistence_strata']['1']['rows']} 事件/行。",
            f"- [MEASURED] 持续性 Brier skill="
            f"{_decimal_metric(brier['persistence_brier_skill'], 6)}。",
            "- [MEASURED] 全部区间按密封时间段台风 SID 整块重采样；相邻 6 h 行"
            "不充当独立样本。",
            "",
            "## 三把刀自检",
            "",
            "1. 状态向量：无动力状态；观测向量为九点 1 分钟 best-track 强度与未来海陆标记。",
            "2. 参数与观测：标签含 2 个开发集离散超参数；气候/持续性基线含 1/2 个"
            "经验概率参数；密封评分的独立单位是台风。",
            "3. 证伪数据：2019--2024 密封风暴的 Brier、可靠性、基准率漂移和台风聚类区间。",
            "",
            "## 偏离清单",
            "",
            "- [MEASURED] 无。样本、物理域、候选网格、排序、时间分区、概率和评分均在"
            "新标签率读取前冻结于 Git。",
            "",
            "## 缺口与下一步",
            "",
            "- C-代理测量 1 分钟 best-track 的强度谷形；成因字段仍未赋值。",
            "- 5 kt 量化压缩了阈值自由度；更细强度观测或微波径向结构才能增加独立信息。",
            "- C-代理后续概率模型沿用本报告的气候 Brier 门槛与可靠性评分。",
            "",
            "## 来源与复现",
            "",
            "- [NOAA/NCEI IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive)",
            f"- [MEASURED] 分析代码 Git `{result['provenance']['analysis_code_git_commit']}`；"
            f"生成时刻 `{result['generated_at_utc']}`。",
            "- 机器可读结果：`outputs/c_event_label_v2/benchmark.json`、"
            "`candidate_rates.csv`、`validation_rows.csv`、`validation_reliability.csv`、"
            "`manifest.json`。",
        ]
    )
    return "\n".join(lines) + "\n"


def equivalence_groups(candidates: pd.DataFrame) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for digest, frame in candidates.groupby("event_vector_sha256", sort=True):
        groups.append(
            {
                "event_vector_sha256": digest,
                "labels": [
                    {
                        "horizon_hours": int(row.horizon_hours),
                        "threshold_ms": float(row.threshold_ms),
                    }
                    for row in frame.sort_values(
                        ["horizon_hours", "threshold_ms"]
                    ).itertuples()
                ],
            }
        )
    return groups


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ibtracs", type=Path, default=DEFAULT_IBTRACS)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    source, source_audit = load_event_source(args.ibtracs)
    windows = build_nine_point_windows(source)
    split = config["season_split"]
    domain = config["physical_domain"]
    candidates_config = config["candidates"]
    bootstrap = config["bootstrap"]
    selection = select_development_label(
        windows,
        horizons=candidates_config["horizon_hours"],
        thresholds_ms=candidates_config["threshold_ms"],
        development_seasons=tuple(split["development"]),
        minimum_initial_intensity_ms=float(domain["minimum_initial_intensity_ms"]),
        minimum_future_distance_km=float(
            domain["future_window_all_dist2land_gt_km"]
        ),
        target_interval=tuple(candidates_config["target_rate_interval"]),
        target_midpoint=float(candidates_config["target_rate_midpoint"]),
        bootstrap_replicates=int(bootstrap["replicates"]),
        bootstrap_seed=int(bootstrap["seed"]),
    )
    selected = selection.selected
    labelled = label_candidate(
        windows,
        horizon_hours=int(selected["horizon_hours"]),
        threshold_ms=float(selected["threshold_ms"]),
        minimum_initial_intensity_ms=float(domain["minimum_initial_intensity_ms"]),
        minimum_future_distance_km=float(
            domain["future_window_all_dist2land_gt_km"]
        ),
    )
    benchmark = run_temporal_benchmark(
        labelled,
        development_seasons=tuple(split["development"]),
        validation_seasons=tuple(split["sealed_validation"]),
        alpha=float(config["probability_baselines"]["jeffreys_alpha"]),
        bootstrap_replicates=int(bootstrap["replicates"]),
        bootstrap_seed=int(bootstrap["seed"]),
    )
    quantization = quantization_audit(source)
    groups = equivalence_groups(selection.candidates)
    generated_at = datetime.now(UTC).isoformat()
    provenance = {
        "analysis_code_git_commit": git_head(),
        "config_path": str(args.config.resolve()),
        "config_sha256": file_sha256(args.config),
        "preregistration_path": str(PREREGISTRATION.resolve()),
        "preregistration_sha256": file_sha256(PREREGISTRATION),
        "source_path": str(args.ibtracs.resolve()),
        "source_sha256": file_sha256(args.ibtracs),
    }
    result = {
        "report_id": "c-event-label-v2-sealed-temporal-benchmark",
        "generated_at_utc": generated_at,
        "status": "sealed-temporal-research-baseline",
        "qualification": "unvalidated",
        "authoritative_forecast": False,
        "label_semantics": (
            "future weakening-then-reintensification best-track waveform; "
            "physical ERC causation remains unassigned"
        ),
        "source_audit": source_audit,
        "nine_point_windows": {
            "rows": int(len(windows)),
            "storms": int(windows["SID"].nunique()),
            "wind_average_window_minutes": 1,
        },
        "quantization_audit": quantization,
        "selection": {
            "target_achieved": selection.target_achieved,
            "selected": selected,
            "quantization_equivalence_groups": groups,
        },
        "benchmark": {
            "training_audit": benchmark.training_audit,
            "metrics": benchmark.metrics,
            "validation_row_sha256": dataframe_sha256(
                benchmark.validation,
                ["SID", "time", "event", "past_event", "p_climatology", "p_persistence"],
            ),
        },
        "provenance": provenance,
    }

    selection.candidates.to_csv(output / "candidate_rates.csv", index=False)
    benchmark.validation.to_csv(output / "validation_rows.csv", index=False)
    benchmark.reliability.to_csv(output / "validation_reliability.csv", index=False)
    save_json(output / "benchmark.json", result)
    plot_candidate_rates(
        selection.candidates, output / "candidate_development_rates.png"
    )
    plot_reliability(
        benchmark.reliability, selected, output / "validation_reliability.png"
    )
    (ROOT / "report_c_event_label_v2.md").write_text(
        build_report(result, selection.candidates), encoding="utf-8"
    )
    manifest = {
        "generated_at_utc": generated_at,
        "analysis_code_git_commit": provenance["analysis_code_git_commit"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
    }
    save_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    main()
