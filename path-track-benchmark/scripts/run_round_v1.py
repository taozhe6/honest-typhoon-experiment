#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from path_benchmark.core import (  # noqa: E402
    PairedTrackRow,
    parse_adeck,
    read_ibtracs_truth,
    strict_pair,
    summarize_rows,
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
                url, headers={"User-Agent": "typhoon-path-learning-reproduction/1.0"}
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            destination.write_bytes(payload)
            source_mode = "download"
    return {
        "url": url,
        "local_path": str(destination.relative_to(PROJECT_ROOT)),
        "source_mode": source_mode,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def write_rows(path: Path, rows: list[PairedTrackRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dictionaries = [row.to_dict() for row in rows]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dictionaries[0]))
        writer.writeheader()
        writer.writerows(dictionaries)


def plot_summary(path: Path, summary: list[dict[str, Any]], storm_count: int) -> None:
    fig, ax = plt.subplots(figsize=(8.8, 5.4), constrained_layout=True)
    styles = {
        "CMC": {"color": "#b43a35", "marker": "o", "label": "CMC"},
        "NGX": {"color": "#176b87", "marker": "s", "label": "NAVGEM / NGX"},
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
        f"Western North Pacific track-error learning reproduction ({storm_count} storms)"
    )
    ax.set_xlabel("Forecast lead (hours)")
    ax.set_ylabel("Mean WGS84 track error (km), storm-block 95% CI")
    ax.set_xticks([24, 48, 72, 96, 120])
    ax.grid(axis="y", color="#d8d8d8", linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def build_report(
    summary: list[dict[str, Any]],
    rows: list[PairedTrackRow],
    provenance: dict[str, Any],
) -> str:
    aids = [item for item in summary if item["aid"] in {"CMC", "NGX"}]
    differences = {
        int(item["lead_hours"]): item
        for item in summary
        if item["aid"] == "CMC_MINUS_NGX"
    }
    lower_counts = {"CMC": 0, "NGX": 0}
    for lead in sorted(differences):
        difference = float(differences[lead]["mean_error_difference_km"])
        lower_counts["CMC" if difference < 0 else "NGX"] += 1
    descriptive_winner = max(lower_counts, key=lower_counts.get)
    sentence = (
        f"我的对比显示，在4个历史台风的严格配对样本中，{descriptive_winner}在"
        f"{lower_counts[descriptive_winner]}/5个预报时效上的平均路径误差较低；"
        "样本量与台风聚类区间决定这一结果只属于学习性复现。"
    )

    lines = [
        "# A 支线路径学习性复现",
        "",
        "状态：`learning-reproduction`。",
        "",
        "## 这轮做成了什么",
        "",
        f"- [MEASURED] 已复现 {len(rows)} 个严格配对的模式循环/时效案例，覆盖 "
        f"{len({row.atcf_id for row in rows})} 个台风。",
        "- [CITED] 两条业务模式路径分别为加拿大模式 `CMC` 与 NAVGEM/NOGAPS `NGX`；"
        "预报来自 UCAR ATCF a-deck，验证位置来自 IBTrACS `USA_LAT/USA_LON`。",
        "- [MEASURED] 图和表均使用相同案例配对，置信区间以台风为 block；记录数没有当作独立样本数。",
        "- [MEASURED] " + sentence,
        "",
        "![路径误差随时效变化](outputs/error_vs_lead.png)",
        "",
        "## 误差表",
        "",
        "[MEASURED] 单位为 km；括号为台风 block bootstrap 95% CI。",
        "",
        "|时效|模式|记录/台风|平均误差|中位误差|P80|",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for item in sorted(aids, key=lambda value: (value["lead_hours"], value["aid"])):
        mean_ci = item["mean_error_ci95_km"]
        median_ci = item["median_error_ci95_km"]
        lines.append(
            f"|{item['lead_hours']} h|{item['aid']}|{item['record_count']}/{item['storm_count']}|"
            f"{round(item['mean_error_km'])} ({round(mean_ci[0])}--{round(mean_ci[1])})|"
            f"{round(item['median_error_km'])} ({round(median_ci[0])}--{round(median_ci[1])})|"
            f"{round(item['p80_error_km'])}|"
        )

    lines.extend(
        [
            "",
            "## 三把刀自检",
            "",
            "1. 状态向量：每个模式在有效时刻的 `X=(latitude, longitude)`；本轮没有自研路径状态或动力方程。",
            "2. 参数与观测：拟合参数为 0；输入是两套业务模式位置，验证通道是事后最佳路径位置。两套模式误差不宣称独立。",
            "3. 证伪数据：同一风暴、循环和有效时刻的 IBTrACS `USA_LAT/USA_LON`，以 WGS84 测地距离评分。",
            "",
            "## 来源与口径",
            "",
            f"- [MEASURED] 数据运行时间：`{provenance['generated_at_utc']}`。",
            "- [CITED] a-deck 保存模式预报全历史；本轮读取 `CMC/NGX` 的24、48、72、96、120小时时效。",
            "- [CITED] IBTrACS 提供事后统一的 JTWC/USA 位置字段；该位置仍是分析估计，缺少逐点独立位置真值误差。",
            "- [ASSUMED] 历史 a-deck 缺少每条产品的真实公开时间，本轮按模式循环归档评分，资格标签保持 `learning-reproduction`。",
            "",
            "## 缺口与下一步",
            "",
            "- 当前只有4个预先指定台风，结论尚不能代表整个西北太平洋季节或长期模式水平。",
            "- `CMC/NGX` 属于 late guidance；下一步需要保存真实 `available_at` 才能完成前瞻业务时效审计。",
            "- 最佳路径中心缺少独立逐点测量误差；报告衡量相对事后分析的位置误差。",
            "- 下一步按冻结的全样本预注册扩展到2022--2024全部命名风暴，同时继续保留本轮4台风结果。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "config" / "round_v1.json"
    )
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    raw_dir = PROJECT_ROOT / "data" / "raw"
    output_dir = PROJECT_ROOT / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    provenance: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": config["status"],
        "config": config,
        "sources": {},
    }
    forecasts = []
    aids = set(config["aids"])
    leads = set(config["lead_hours"])
    storm_names = {item["atcf_id"]: item["name"] for item in config["storms"]}
    for storm in config["storms"]:
        destination = raw_dir / Path(storm["adeck_url"]).name
        source = fetch(storm["adeck_url"], destination)
        provenance["sources"][storm["atcf_id"]] = source
        forecasts.extend(
            parse_adeck(
                destination.read_text(encoding="utf-8"),
                storm["atcf_id"],
                aids,
                leads,
            )
        )

    ibtracs_destination = raw_dir / "ibtracs.WP.list.v04r01.csv"
    shared = (PROJECT_ROOT / config["ibtracs"]["shared_local_path"]).resolve()
    provenance["sources"]["IBTrACS"] = fetch(
        config["ibtracs"]["url"], ibtracs_destination, shared
    )
    truth = read_ibtracs_truth(ibtracs_destination, set(storm_names))
    rows = strict_pair(forecasts, truth, storm_names)
    if not rows:
        raise RuntimeError("strict paired sample is empty")

    bootstrap = config["bootstrap"]
    summary = summarize_rows(
        rows,
        config["lead_hours"],
        int(bootstrap["replicates"]),
        int(bootstrap["seed"]),
    )
    write_rows(output_dir / "paired_track_rows.csv", rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    provenance["parsed_forecast_point_count"] = len(forecasts)
    provenance["truth_point_count"] = len(truth)
    provenance["paired_row_count"] = len(rows)
    provenance["paired_storm_count"] = len({row.atcf_id for row in rows})
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    plot_summary(
        output_dir / "error_vs_lead.png",
        summary,
        len({row.atcf_id for row in rows}),
    )
    (PROJECT_ROOT / "report.md").write_text(
        build_report(summary, rows, provenance), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

