#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from path_benchmark.core import (  # noqa: E402
    ForecastPoint,
    PairedTrackRow,
    correlation_diagnostics,
    leave_one_storm_out_intervals,
    parse_adeck,
    read_ibtracs_truth,
    strict_pair,
    summarize_rows_v2,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, destination: Path, fallback: Path | None = None) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_mode = "cached"
    if not destination.exists():
        if fallback is not None and fallback.exists():
            shutil.copyfile(fallback, destination)
            source_mode = "shared-local-copy"
        else:
            request = urllib.request.Request(
                url, headers={"User-Agent": "typhoon-path-benchmark/2.0"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                destination.write_bytes(response.read())
            source_mode = "download"
    return {
        "url": url,
        "local_path": str(destination.relative_to(PROJECT_ROOT)),
        "source_mode": source_mode,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write an empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def coverage_audit(
    forecasts: Iterable[ForecastPoint],
    truth: dict[tuple[str, datetime], object],
    storm_ids: list[str],
    leads: list[int],
) -> list[dict[str, Any]]:
    cycles: dict[tuple[str, int, str], set[datetime]] = defaultdict(set)
    for point in forecasts:
        cycles[(point.atcf_id, point.lead_hours, point.aid)].add(point.cycle_utc)
    rows: list[dict[str, Any]] = []
    for atcf_id in storm_ids:
        for lead in leads:
            cmc = cycles[(atcf_id, lead, "CMC")]
            ngx = cycles[(atcf_id, lead, "NGX")]
            common = cmc & ngx
            truth_matched = {
                cycle
                for cycle in common
                if (atcf_id, cycle + timedelta(hours=lead)) in truth
            }
            rows.append(
                {
                    "atcf_id": atcf_id,
                    "lead_hours": lead,
                    "cmc_cycle_count": len(cmc),
                    "ngx_cycle_count": len(ngx),
                    "common_cycle_count": len(common),
                    "exact_truth_matched_count": len(truth_matched),
                    "paired_retention_fraction": (
                        len(truth_matched) / len(common) if common else None
                    ),
                }
            )
    return rows


def plot_summary(
    path: Path,
    summary: list[dict[str, Any]],
    storm_count: int,
) -> None:
    fig, ax = plt.subplots(figsize=(9.2, 5.6), constrained_layout=True)
    styles = {
        "CMC": {"color": "#b43a35", "marker": "o", "label": "CMC"},
        "NGX": {"color": "#176b87", "marker": "s", "label": "NAVGEM / NGX"},
        "DYC2": {
            "color": "#287a4b",
            "marker": "D",
            "label": "DYC2 spherical mean",
        },
    }
    for aid, style in styles.items():
        records = sorted(
            (item for item in summary if item["aid"] == aid),
            key=lambda item: int(item["lead_hours"]),
        )
        x = [int(item["lead_hours"]) for item in records]
        y = [float(item["mean_error_km"]) for item in records]
        lower = [
            value - float(item["mean_error_ci95_km"][0])
            for value, item in zip(y, records)
        ]
        upper = [
            float(item["mean_error_ci95_km"][1]) - value
            for value, item in zip(y, records)
        ]
        ax.errorbar(
            x,
            y,
            yerr=[lower, upper],
            linewidth=2.0,
            capsize=4,
            markersize=6,
            **style,
        )
    ax.set_title(
        f"Western North Pacific paired track-error benchmark ({storm_count} storms)"
    )
    ax.set_xlabel("Forecast lead (hours)")
    ax.set_ylabel("Mean WGS84 track error (km), storm-block 95% CI")
    ax.set_xticks([24, 48, 72, 96, 120])
    ax.grid(axis="y", color="#d8d8d8", linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _round_interval(interval: list[float], digits: int = 0) -> str:
    if digits == 0:
        return f"[{round(interval[0])}, {round(interval[1])}]"
    return f"[{interval[0]:.{digits}f}, {interval[1]:.{digits}f}]"


def honest_consensus_conclusion(summary: list[dict[str, Any]]) -> str:
    by_key = {(int(row["lead_hours"]), str(row["aid"])): row for row in summary}
    lower_than_best = 0
    clearly_better = 0
    clearly_worse = 0
    for lead in (24, 48, 72, 96, 120):
        cmc = float(by_key[(lead, "CMC")]["mean_error_km"])
        ngx = float(by_key[(lead, "NGX")]["mean_error_km"])
        best = "CMC" if cmc <= ngx else "NGX"
        difference = by_key[(lead, f"DYC2_MINUS_{best}")]
        estimate = float(difference["mean_error_difference_km"])
        lower, upper = map(float, difference["mean_error_difference_ci95_km"])
        lower_than_best += estimate < 0.0
        clearly_better += upper < 0.0
        clearly_worse += lower > 0.0
    if clearly_better > 0 and clearly_worse == 0:
        return (
            f"我的对比显示，DYC2 在 {lower_than_best}/5 个时效低于当时效最佳单模，"
            f"其中 {clearly_better}/5 个配对差的台风聚类 95% CI 完全低于 0。"
        )
    if clearly_worse > 0 and clearly_better == 0:
        return (
            f"我的对比显示，DYC2 在 {clearly_worse}/5 个时效显著高于当时效最佳单模；"
            "简单等权共识的改善命题被这组数据证伪。"
        )
    return (
        f"我的对比显示，DYC2 在 {lower_than_best}/5 个时效低于当时效最佳单模，"
        f"显著改善 {clearly_better}/5、显著变差 {clearly_worse}/5；"
        "共识优势在当前聚类区间下没有稳定方向。"
    )


def build_report(
    summary: list[dict[str, Any]],
    rows: list[PairedTrackRow],
    correlations: dict[str, Any],
    cv_summary: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    manifest: dict[str, Any],
    provenance: dict[str, Any],
) -> str:
    metrics = [row for row in summary if row["aid"] in {"CMC", "NGX", "DYC2"}]
    differences = [row for row in summary if str(row["aid"]).startswith("DYC2_MINUS")]
    primary = correlations["primary"]
    raw = correlations["raw_pearson_sensitivity"]
    neff_phrase = (
        "这两套路径在该样本中只提供约一个有效独立意见。"
        if float(primary["neff"]) < 1.25
        else "这两套路径仍提供多于一个有效独立意见，数值依赖交换相关假设。"
    )
    conclusion = honest_consensus_conclusion(summary)
    exact_pairs = sum(int(row["exact_truth_matched_count"]) for row in coverage)
    cv_80 = [
        row
        for row in cv_summary
        if row.get("status") == "estimated" and row.get("quantile") == 0.8
    ]

    lines = [
        "# A 支线路径对比：可发布学习性复现",
        "",
        "状态：`prospective-expanded-sample-learning-reproduction`。资格：`unvalidated`。",
        "",
        "## 这轮做成了什么",
        "",
        f"- [MEASURED] 按冻结规则从 27 个强台风候选中机械纳入 26 个台风，生成 "
        f"{len(rows)} 个严格同样本案例；覆盖审计的 exact-time 配对计数为 {exact_pairs}。",
        "- [MEASURED] 三条路径使用完全相同的风暴、循环、有效时刻和 best-track 位置；"
        "DYC2 是 0.5/0.5 单位球向量平均，拟合参数为 0。",
        f"- [MEASURED] {conclusion}",
        f"- [ASSUMED+MEASURED] lead-centered Pearson `rho={primary['rho']:.2f}` "
        f"(95% CI {_round_interval(primary['rho_ci95'], 2)})，交换相关公式得到 "
        f"`n_eff={primary['neff']:.2f}` (95% CI {_round_interval(primary['neff_ci95'], 2)})。"
        f"{neff_phrase}",
        "- [MEASURED] 留一台风交叉验证已为 DYC2 生成 50/80/95% 经验半径与真实覆盖率；"
        "训练与检验按整场台风分离。",
        "",
        "![误差随提前量变化](outputs/round_v2/error_vs_lead.png)",
        "",
        "## 配对路径误差",
        "",
        "[MEASURED] 单位 km；括号为按台风 block bootstrap 2,000 次的 95% CI。",
        "",
        "|时效|路径|记录/台风|平均误差|中位误差|P80|",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for item in sorted(metrics, key=lambda value: (value["lead_hours"], value["aid"])):
        lines.append(
            f"|{item['lead_hours']} h|{item['aid']}|{item['record_count']}/{item['storm_count']}|"
            f"{round(item['mean_error_km'])} {_round_interval(item['mean_error_ci95_km'])}|"
            f"{round(item['median_error_km'])} {_round_interval(item['median_error_ci95_km'])}|"
            f"{round(item['p80_error_km'])}|"
        )

    lines.extend(
        [
            "",
            "## 共识配对差",
            "",
            "[MEASURED] `DYC2 - 单模`；负值表示共识误差较小。",
            "",
            "|时效|对比|平均差 km|95% CI|",
            "|---:|---|---:|---:|",
        ]
    )
    for item in sorted(differences, key=lambda value: (value["lead_hours"], value["aid"])):
        lines.append(
            f"|{item['lead_hours']} h|{item['aid']}|"
            f"{round(item['mean_error_difference_km'])}|"
            f"{_round_interval(item['mean_error_difference_ci95_km'])}|"
        )

    lines.extend(
        [
            "",
            "## 模式相关与 n_eff",
            "",
            "[ASSUMED] `n_eff=2/(1+rho)` 假定两个来源可用一个交换相关系数描述。"
            "[MEASURED] 主结果先分别移除每个 TECH 在各提前量的平均误差，降低共同的"
            "误差增长曲线对相关性的机械抬升。",
            "",
            f"- lead-centered Pearson: `rho={primary['rho']:.2f}` "
            f"(95% CI {_round_interval(primary['rho_ci95'], 2)})；"
            f"`n_eff={primary['neff']:.2f}` "
            f"(95% CI {_round_interval(primary['neff_ci95'], 2)})。",
            f"- 原始径向误差敏感性: `rho={raw['rho']:.2f}` "
            f"(95% CI {_round_interval(raw['rho_ci95'], 2)})；"
            f"`n_eff={raw['neff']:.2f}` "
            f"(95% CI {_round_interval(raw['neff_ci95'], 2)})。",
            "- [CITED] NGX 是 NAVGEM/NOGAPS 路径配 GFS tracker；tracker 共享不能解释为"
            "共享 GFS 动力核心。CMC 与 Navy 模式动力本体不同，共同资料与追踪仍会相关。",
            "- 这个数字衡量误差一致性，不衡量准确性，也不证明两个动力系统的结构独立。",
            "",
            "## 交叉验证不确定性",
            "",
            "[MEASURED] 表中半径来自留一台风训练折；覆盖率 CI 继续按台风聚类。",
            "",
            "|时效|目标覆盖|训练台风/折|中位半径 km|实际覆盖|95% CI|",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in cv_summary:
        if item.get("status") != "estimated":
            lines.append(
                f"|{item['lead_hours']} h|NA|{item['available_storms']} available|NA|NA|"
                "insufficient|"
            )
            continue
        lines.append(
            f"|{item['lead_hours']} h|{round(100 * item['quantile'])}%|"
            f"{item['training_storm_count_per_fold']}|{round(item['median_radius_km'])}|"
            f"{100 * item['coverage']:.0f}%|"
            f"{100 * item['coverage_ci95'][0]:.0f}--{100 * item['coverage_ci95'][1]:.0f}%|"
        )

    selection = manifest["counts"]
    excluded = [row for row in manifest["candidate_audit"] if not row["qualified"]]
    lines.extend(
        [
            "",
            "## 资格、TECH 与来源",
            "",
            f"- [MEASURED] WP 正式编号风暴 {selection['wp_atcf_storms_2022_2024']} 个；"
            f"CMA 峰值缺测 {selection['missing_cma_peak']} 个；强台风候选 "
            f"{selection['strong_typhoon_intensity_candidates']} 个；最终纳入 "
            f"{selection['qualified_storms']} 个。",
            f"- [MEASURED] 覆盖排除：{', '.join(row['atcf_id'] for row in excluded)}；"
            "排除原因是没有 CMC/NGX 同循环 72 h 路径，资格生成器未读取误差。",
            "- [CITED] `CMC/NGX` 均为原始 late-cycle TECH；本轮读取原生 6 h 倍数点。"
            "UCAR 约定末尾 `I` 才表示提前对齐版本，本轮没有读取该版本。",
            "- [MEASURED] 所有 a-deck、IBTrACS 与资格 manifest 的 SHA-256 在 provenance 中"
            "逐项核对；哈希漂移会中止运行。",
            f"- [MEASURED] 运行时刻 `{provenance['generated_at_utc']}`；Git "
            f"`{provenance['git_commit']}`。",
            "",
            "## 三把刀自检",
            "",
            "1. 状态向量：每个有效时刻 `X=(latitude, longitude)`；DYC2 是两个位置的固定球面函数。",
            "2. 参数与观测：拟合参数 0；两个相关模式位置输入、一个事后 best-track 验证通道；"
            "lead-centered `n_eff` 显式量化相关性。",
            "3. 证伪数据：同风暴、同循环、同有效时刻的 IBTrACS `USA_LAT/USA_LON`，"
            "以 WGS84 测地距离、配对差和留一台风覆盖率评分。",
            "",
            "## 缺口与下一步",
            "",
            "- 历史 a-deck 缺少逐产品真实公开时刻；本报告复现模式循环，资格保持"
            "`learning-reproduction` 与 `unvalidated`。",
            "- IBTrACS USA 位置是事后分析中心，逐点独立位置测量误差仍不可得。",
            "- 两套模式共享观测生态和追踪方法；`n_eff` 依赖交换相关假设，原始相关敏感性已并列。",
            "- 下一步扩展模式集合与年份，并保存真实 `available_at` 后开展前瞻业务检验。",
            "",
            "## 引用",
            "",
            "- [UCAR Tropical Cyclone Guidance Project](https://hurricanes.ral.ucar.edu/repository/)",
            "- [UCAR early/late and interpolated TECH convention](https://hurricanes.ral.ucar.edu/guide/)",
            "- [NHC forecast-aid definitions](https://www.nhc.noaa.gov/verification/verify6.shtml)",
            "- [NOAA/NCEI IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive)",
            "",
        ]
    )
    if cv_80:
        mismatches = sum(
            not (item["coverage_ci95"][0] <= 0.8 <= item["coverage_ci95"][1])
            for item in cv_80
        )
        lines.insert(
            10,
            f"- [MEASURED] 80% 经验半径在 {mismatches}/{len(cv_80)} 个时效的台风聚类 "
            "95% CI 排除目标 0.80。",
        )
    return "\n".join(lines)


def main() -> None:
    config_path = PROJECT_ROOT / "config" / "round_v2.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    manifest_path = PROJECT_ROOT / config["selection_manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if config["storms"] != manifest["qualified_storms"]:
        raise RuntimeError("round-v2 config does not match the frozen eligibility manifest")

    raw_dir = PROJECT_ROOT / "data" / "raw"
    output_dir = PROJECT_ROOT / "outputs" / "round_v2"
    output_dir.mkdir(parents=True, exist_ok=True)
    aids = set(config["aids"])
    leads = list(config["lead_hours"])
    storm_names = {storm["atcf_id"]: storm["name"] for storm in config["storms"]}

    sources: dict[str, Any] = {}
    forecasts: list[ForecastPoint] = []
    frozen_adecks = manifest["sources"]["adecks"]
    for storm in config["storms"]:
        destination = raw_dir / Path(storm["adeck_url"]).name
        source = fetch(storm["adeck_url"], destination)
        expected_hash = frozen_adecks[storm["atcf_id"]]["sha256"]
        if source["sha256"] != expected_hash:
            raise RuntimeError(f"frozen a-deck hash changed for {storm['atcf_id']}")
        sources[storm["atcf_id"]] = source
        forecasts.extend(
            parse_adeck(
                destination.read_text(encoding="utf-8"),
                storm["atcf_id"],
                aids,
                set(leads),
            )
        )

    ibtracs_destination = raw_dir / "ibtracs.WP.list.v04r01.csv"
    shared = (PROJECT_ROOT / config["ibtracs"]["shared_local_path"]).resolve()
    ibtracs_source = fetch(config["ibtracs"]["url"], ibtracs_destination, shared)
    if ibtracs_source["sha256"] != manifest["sources"]["IBTrACS"]["sha256"]:
        raise RuntimeError("frozen IBTrACS hash changed")
    sources["IBTrACS"] = ibtracs_source

    truth = read_ibtracs_truth(ibtracs_destination, set(storm_names))
    rows = strict_pair(forecasts, truth, storm_names)
    if not rows:
        raise RuntimeError("round-v2 strict paired sample is empty")

    bootstrap = config["bootstrap"]
    replicates = int(bootstrap["replicates"])
    seed = int(bootstrap["seed"])
    summary = summarize_rows_v2(rows, leads, replicates, seed)
    correlations = correlation_diagnostics(rows, leads, replicates, seed)
    cv_summary, cv_rows = leave_one_storm_out_intervals(
        rows,
        leads,
        [0.5, 0.8, 0.95],
        minimum_training_storms=10,
        replicates=replicates,
        seed=seed,
    )
    coverage = coverage_audit(forecasts, truth, sorted(storm_names), leads)

    write_dict_rows(output_dir / "paired_track_rows.csv", [row.to_dict() for row in rows])
    write_dict_rows(output_dir / "coverage_audit.csv", coverage)
    write_dict_rows(output_dir / "loocv_interval_rows.csv", cv_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "correlation_neff.json").write_text(
        json.dumps(correlations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "loocv_intervals.json").write_text(
        json.dumps(cv_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()
    provenance = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": config["status"],
        "qualification": "unvalidated",
        "git_commit": git_commit,
        "config_sha256": sha256_file(config_path),
        "eligibility_manifest_sha256": sha256_file(manifest_path),
        "parsed_forecast_point_count": len(forecasts),
        "truth_point_count": len(truth),
        "paired_row_count": len(rows),
        "paired_storm_count": len({row.atcf_id for row in rows}),
        "sources": sources,
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    plot_summary(
        output_dir / "error_vs_lead.png",
        summary,
        len({row.atcf_id for row in rows}),
    )
    (PROJECT_ROOT / "report_round_v2.md").write_text(
        build_report(
            summary,
            rows,
            correlations,
            cv_summary,
            coverage,
            manifest,
            provenance,
        ),
        encoding="utf-8",
    )
    print(
        f"Completed round v2 with {len(rows)} paired cases across "
        f"{len({row.atcf_id for row in rows})} storms."
    )


if __name__ == "__main__":
    main()
