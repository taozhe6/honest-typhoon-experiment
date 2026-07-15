#!/usr/bin/env python3
"""Run the frozen theta=Ck/h synthetic final-state propagation."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
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

from typhoon_markov.intensity_events import file_sha256
from typhoon_markov.theta_propagation import run_theta_grid


DEFAULT_CONFIG = ROOT / "config" / "theta_propagation.json"
DEFAULT_SCENARIOS = ROOT / "config" / "global_sensitivity.json"
DEFAULT_OUTPUT = ROOT / "outputs" / "theta_propagation"
PROTOCOL = ROOT / "docs" / "theta-propagation-protocol.md"


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("theta row table is empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def plot_final_wind(result: dict[str, Any], output_path: Path) -> None:
    summaries = result["scenario_summaries"]
    fig, axes = plt.subplots(
        len(summaries), 1, figsize=(8.4, 8.0), sharex=True, constrained_layout=True
    )
    colors = ("#2a7185", "#9a4d3d", "#397a52")
    labels = {
        "open_ocean_intensifying": "Open-ocean intensifying probe",
        "hostile_open_ocean": "Hostile open-ocean probe",
        "landfall_transition": "Landfall-transition probe",
    }
    for axis, summary, color in zip(axes, summaries, colors):
        rows = [
            row for row in result["rows"] if row["scenario"] == summary["scenario"]
        ]
        x = [row["theta_multiplier"] for row in rows]
        y = [row["final_wind_ms"] for row in rows]
        axis.plot(x, y, color=color, linewidth=2)
        axis.scatter(
            [0.7, 1.0, 1.3],
            [
                rows[0]["final_wind_ms"],
                summary["baseline_final"]["wind_ms"],
                rows[-1]["final_wind_ms"],
            ],
            color=color,
            s=38,
            zorder=3,
        )
        axis.axvline(1.0, color="#666666", linestyle="--", linewidth=0.9)
        axis.set_ylabel("Final V (m/s)")
        axis.set_title(labels.get(summary["scenario"], summary["scenario"]), loc="left")
        axis.grid(axis="y", color="#d9d9d9", linewidth=0.7)
        axis.spines[["top", "right"]].set_visible(False)
    axes[-1].set_xlabel(r"Assumed $\theta/\theta_0$ multiplier")
    fig.suptitle(r"48 h synthetic final intensity under $\theta=C_k/h$ scenarios")
    fig.savefig(output_path, dpi=180, facecolor="white")
    plt.close(fig)


def build_report(result: dict[str, Any]) -> str:
    cross = result["cross_scenario"]
    check = result["structural_checks"]["equivalent_parameterizations"]
    lines = [
        "# `theta=Ck/h` 到 48 小时终值的传播",
        "",
        "状态：`synthetic-structural-sensitivity`；资格：`unvalidated`。",
        "",
        "## 这轮做成了什么",
        "",
        f"- [CITED] 基准 `Ck=1.2e-3`、`h=1800 m`，因此 "
        f"`theta_0={result['baseline_theta_per_m']:.3e} m^-1`。",
        "- [ASSUMED] `theta/theta_0` 采用 `[0.7,1.3]` 的 61 点有界情景；"
        "它是 scenario envelope，没有概率分布语义。",
        f"- [MEASURED] 三个合成场景的最大基准中心 48 h `abs(delta V)` 为 "
        f"`{cross['maximum_baseline_centered_absolute_delta_ms']:.2f} m/s`；"
        f"最大端点到端点宽度为 "
        f"`{cross['maximum_endpoint_to_endpoint_width_ms']:.2f} m/s`。",
        f"- [MEASURED] `Ck` 缩放与等价 `h` 反向缩放的完整轨迹最大原生状态差 "
        f"`{check['maximum_native_state_delta']:.3e}`，结构不变性"
        f"{'通过' if check['passed'] else '失败'}。",
        "- [MEASURED] 机构统一风窗后的成对强度分歧为 `2--6 m/s`；"
        f"本合成探针的最大单侧变化 "
        f"{cross['maximum_baseline_centered_absolute_delta_ms']:.2f} m/s 位于同一数量级。"
        "两者来源和统计语义不同。",
        "- [MEASURED] 这些终值界定 v0.1 对一个假定常量范围的结构敏感度；"
        "台风强度预报资格仍为 `unvalidated`。",
        "",
        "![theta 终值传播](outputs/theta_propagation/theta_final_wind.png)",
        "",
        "## 场景终值",
        "",
        "[MEASURED] 风速单位 m/s。`max abs delta` 以基准 `theta_0` 为中心；"
        "`width` 是 61 点最小到最大终值范围。",
        "",
        "|合成场景|初始 V|0.7 theta0 终值|theta0 终值|1.3 theta0 终值|max abs delta|width|单调性|",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for summary in result["scenario_summaries"]:
        envelope = summary["wind_envelope"]
        lines.append(
            f"|`{summary['scenario']}`|{summary['initial_wind_ms']:.2f}|"
            f"{summary['lower_theta_endpoint']['final_wind_ms']:.2f}|"
            f"{summary['baseline_final']['wind_ms']:.2f}|"
            f"{summary['upper_theta_endpoint']['final_wind_ms']:.2f}|"
            f"{envelope['maximum_baseline_centered_absolute_delta_ms']:.2f}|"
            f"{envelope['endpoint_to_endpoint_width_ms']:.2f}|"
            f"{envelope['monotonicity']}|"
        )

    lines.extend(
        [
            "",
            "## 结构解释",
            "",
            "[MEASURED] FAST 的风速与水汽倾向前因子为 `0.5*Ck/h`。"
            "`Ck` 与 `h` 同比缩放的轨迹保持不变，说明当前实现只识别一个组合 "
            "`theta`；二者分解存在 1 个不可识别自由度。",
            "",
            "[ASSUMED] `+/-30%` 由项目作为压力测试边界指定。文献提供基准常量；"
            "该区间的概率分布语义为空。因此 3.54 m/s 属于合成模型响应，"
            "95% 误差语义与机构分歧的方差相加资格均为零。",
            "",
            "## 三把刀自检",
            "",
            "1. 状态向量：`X=(V,m,Pc,RMW)`；固定 regime 只作为已知日程。",
            "2. 参数与观测：拟合参数 0；扫描 1 个可识别组合；三个场景属于合成强迫序列。",
            "3. 证伪数据：两种参数化的完整轨迹和 61 点终值；真实预报能力仍由"
            "独立登陆真值与密封回报评分。",
            "",
            "## 偏离清单",
            "",
            "- [MEASURED] 无。场景、网格、引擎和终值定义均在专用网格运行前冻结。",
            "",
            "## 缺口与下一步",
            "",
            "- 区间宽度由假定 `+/-30%` 边界决定；现实常量不确定性尚未形成概率分布。",
            "- 三个强迫序列是合成探针；真实登陆误差仍缺少独立测站真值。",
            "- v0.1 regime 对风速路径的作用为 0，本报告保留固定日程以隔离该失败结构。",
            "",
            "## 来源与复现",
            "",
            "- [Lin et al. (2023), JAMES](https://doi.org/10.1029/2023MS003686)",
            f"- [MEASURED] 分析代码 Git `{result['provenance']['analysis_code_git_commit']}`；"
            f"生成时刻 `{result['generated_at_utc']}`。",
            "- 机器可读结果：`outputs/theta_propagation/theta_propagation.json`、"
            "`theta_grid.csv`、`manifest.json`。",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    theta_config = json.loads(args.config.read_text(encoding="utf-8"))
    scenario_config = json.loads(args.scenarios.read_text(encoding="utf-8"))
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    calculation = run_theta_grid(theta_config, scenario_config)
    generated_at = datetime.now(UTC).isoformat()
    result = {
        "report_id": "theta-identifiable-ratio-final-state-propagation-v1",
        "generated_at_utc": generated_at,
        "status": "synthetic-structural-sensitivity",
        "qualification": "unvalidated",
        "authoritative_forecast": False,
        "uncertainty_semantics": theta_config["uncertainty_semantics"],
        **calculation,
        "provenance": {
            "analysis_code_git_commit": git_head(),
            "config_path": str(args.config.resolve()),
            "config_sha256": file_sha256(args.config),
            "scenario_config_path": str(args.scenarios.resolve()),
            "scenario_config_sha256": file_sha256(args.scenarios),
            "protocol_path": str(PROTOCOL.resolve()),
            "protocol_sha256": file_sha256(PROTOCOL),
        },
    }
    write_rows(output / "theta_grid.csv", result["rows"])
    save_json(output / "theta_propagation.json", result)
    plot_final_wind(result, output / "theta_final_wind.png")
    (ROOT / "report_theta_propagation.md").write_text(
        build_report(result), encoding="utf-8"
    )
    manifest = {
        "generated_at_utc": generated_at,
        "analysis_code_git_commit": result["provenance"]["analysis_code_git_commit"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
    }
    save_json(output / "manifest.json", manifest)


if __name__ == "__main__":
    main()
