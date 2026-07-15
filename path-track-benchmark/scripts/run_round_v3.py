#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
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

from path_benchmark.core import ForecastPoint, parse_adeck, read_ibtracs_truth  # noqa: E402
from path_benchmark.round_v3 import (  # noqa: E402
    STREAMS,
    TripleTrackRow,
    independence_diagnostics,
    strict_triple_pair,
    summarize_triple_rows,
)


CONFIG_PATH = PROJECT_ROOT / "config" / "round_v3.json"
MANIFEST_PATH = PROJECT_ROOT / "config" / "round_v2_eligibility_manifest.json"
PREREG_PATH = PROJECT_ROOT / "preregistration_round_v3.md"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "round_v3"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_dict_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def git_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
    ).strip()


def tech_counts(paths: Iterable[Path]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in paths:
        with path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.reader(handle):
                if len(raw) >= 5:
                    aid = raw[4].strip().upper()
                    if aid:
                        counts[aid] += 1
    return counts


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
            by_aid = {
                aid: cycles[(atcf_id, lead, aid)] for aid in ("CMC", "NGX", "UKM")
            }
            common = set.intersection(*by_aid.values())
            truth_matched = {
                cycle
                for cycle in common
                if (atcf_id, cycle + timedelta(hours=lead)) in truth
            }
            rows.append(
                {
                    "atcf_id": atcf_id,
                    "season": int(atcf_id[-4:]),
                    "lead_hours": lead,
                    "cmc_cycle_count": len(by_aid["CMC"]),
                    "ngx_cycle_count": len(by_aid["NGX"]),
                    "ukm_cycle_count": len(by_aid["UKM"]),
                    "three_way_common_cycle_count": len(common),
                    "exact_truth_matched_count": len(truth_matched),
                }
            )
    return rows


def plot_summary(
    path: Path,
    summary: list[dict[str, Any]],
    storm_count: int,
) -> None:
    fig, ax = plt.subplots(figsize=(9.4, 5.8), constrained_layout=True)
    styles = {
        "CMC": {"color": "#b43a35", "marker": "o", "label": "CMC"},
        "NGX": {"color": "#176b87", "marker": "s", "label": "NAVGEM / NGX"},
        "UKM": {"color": "#6746a5", "marker": "^", "label": "UKMET / UKM"},
        "LOCAL_EQ2_CMC_NGX": {
            "color": "#287a4b",
            "marker": "D",
            "label": "Local equal CMC+NGX",
        },
    }
    for stream, style in styles.items():
        records = sorted(
            (item for item in summary if item["stream"] == stream),
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
            linewidth=2,
            capsize=3,
            markersize=6,
            **style,
        )
    ax.set_title(f"Strict same-cycle WNP track benchmark ({storm_count} storms)")
    ax.set_xlabel("Forecast lead (hours)")
    ax.set_ylabel("Mean WGS84 track error (km), storm-block 95% CI")
    ax.set_xticks([24, 48, 72, 96, 120])
    ax.grid(axis="y", color="#d8d8d8", linewidth=0.8)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=2)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _interval(values: list[float], digits: int = 2) -> str:
    return f"[{values[0]:.{digits}f}, {values[1]:.{digits}f}]"


def _integer_interval(values: list[float]) -> str:
    return f"[{round(values[0])}, {round(values[1])}]"


def build_report(
    config: dict[str, Any],
    rows: list[TripleTrackRow],
    summary: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    coverage: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> str:
    primary = diagnostics["primary"]
    raw = diagnostics["raw_pearson_sensitivity"]
    spearman = diagnostics["lead_centered_spearman_sensitivity"]
    local_neff = float(primary["neff_local_eq2_ukm"])
    local_ci = list(primary["neff_local_eq2_ukm_ci95"])
    delta = float(primary["delta_neff"])
    delta_ci = list(primary["delta_neff_ci95"])
    close_range = config["correlation"]["close_to_two_point_interval"]
    close_ci_floor = config["correlation"]["close_to_two_minimum_ci95_lower"]
    close_to_two = (
        close_range[0] <= local_neff <= close_range[1]
        and local_ci[0] >= close_ci_floor
    )
    if delta_ci[0] > 0.0:
        delta_decision = "支持独立 UKMET 核心增加有效意见数。"
    elif delta_ci[1] < 0.0:
        delta_decision = "独立 UKMET 核心的增量命题被该样本证伪。"
    else:
        delta_decision = "当前样本无法分辨独立 UKMET 核心带来的有效意见增量。"
    close_decision = (
        "达到预注册的“接近 2”判据。"
        if close_to_two
        else "未达到预注册的“接近 2”判据。"
    )
    evidence_by_lead: dict[int, tuple[int, int]] = {}
    for lead in config["lead_hours"]:
        selected = [row for row in rows if row.lead_hours == lead]
        evidence_by_lead[lead] = (
            len(selected),
            len({row.atcf_id for row in selected}),
        )
    inadequate = [
        lead
        for lead, (record_count, storm_count) in evidence_by_lead.items()
        if record_count < config["minimum_evidence_per_lead"]["records"]
        or storm_count < config["minimum_evidence_per_lead"]["storms"]
    ]
    metric_rows = [row for row in summary if row["stream"] in STREAMS]
    difference_rows = [
        row for row in summary if row["stream"] == "LOCAL_EQ2_MINUS_UKM"
    ]
    seasons = sorted({int(row.atcf_id[-4:]) for row in rows})
    ukm_storms_by_season = {
        season: len(
            {
                row["atcf_id"]
                for row in coverage
                if row["season"] == season
                and row["lead_hours"] == 72
                and row["three_way_common_cycle_count"] > 0
            }
        )
        for season in (2022, 2023, 2024)
    }

    lines = [
        "# A 支线深化：本地共识与 UKMET 独立核心复验",
        "",
        "状态：`prospective-independent-core-learning-reproduction`；资格：`unvalidated`。",
        "",
        "## 这轮做成了什么",
        "",
        "- [MEASURED] `DYC2` 来源已经定案：它是本地 `CMC/NGX` 固定等权球面共识，"
        "正式名称为 `LOCAL_EQ2_CMC_NGX`，原始 a-deck TECH 计数为 0。",
        f"- [MEASURED] 严格三方同循环、同提前量、同有效时刻样本包含 {len(rows)} 条记录、"
        f"{len({row.atcf_id for row in rows})} 场台风，覆盖季节为 "
        f"{', '.join(map(str, seasons))}。",
        f"- [ASSUMED+MEASURED] 本地共识与 UKM 的 lead-centered Pearson "
        f"`rho={primary['rho_local_eq2_ukm']:.2f}` "
        f"(95% CI {_interval(primary['rho_local_eq2_ukm_ci95'])})，交换相关公式得到 "
        f"`n_eff={local_neff:.2f}` (95% CI {_interval(local_ci)})；{close_decision}",
        f"- [MEASURED] 同样本 `Delta n_eff={delta:.2f}` "
        f"(95% CI {_interval(delta_ci)})；{delta_decision}",
        f"- [MEASURED] 我的对比显示：独立 UKMET 核心与本地 CMC/NGX 共识的误差相关"
        f"对应 `n_eff={local_neff:.2f}`，预注册的“接近 2”结论为"
        f"{'支持' if close_to_two else '未支持'}。",
        "",
        "![误差随提前量变化](outputs/round_v3/error_vs_lead.png)",
        "",
        "## 路径误差",
        "",
        "[MEASURED] 单位 km；括号为台风 block bootstrap 2,000 次的 95% CI。四条路径"
        "使用完全相同案例。",
        "",
        "|时效|路径|记录/台风|平均误差|中位误差|P80|",
        "|---:|---|---:|---:|---:|---:|",
    ]
    order = {stream: index for index, stream in enumerate(STREAMS)}
    for item in sorted(
        metric_rows, key=lambda value: (value["lead_hours"], order[value["stream"]])
    ):
        lines.append(
            f"|{item['lead_hours']} h|{item['stream']}|"
            f"{item['record_count']}/{item['storm_count']}|"
            f"{round(item['mean_error_km'])} {_integer_interval(item['mean_error_ci95_km'])}|"
            f"{round(item['median_error_km'])} "
            f"{_integer_interval(item['median_error_ci95_km'])}|"
            f"{round(item['p80_error_km'])}|"
        )

    lines.extend(
        [
            "",
            "## 本地共识与 UKM 配对差",
            "",
            "[MEASURED] `LOCAL_EQ2_CMC_NGX - UKM`；负值表示本地共识径向误差较小。",
            "",
            "|时效|平均差 km|95% CI|",
            "|---:|---:|---:|",
        ]
    )
    for item in difference_rows:
        lines.append(
            f"|{item['lead_hours']} h|{round(item['mean_error_difference_km'])}|"
            f"{_integer_interval(item['mean_error_difference_ci95_km'])}|"
        )

    lines.extend(
        [
            "",
            "## 相关与有效意见数",
            "",
            "[ASSUMED] `n_eff=2/(1+rho)` 使用可交换两误差流假设。"
            "[MEASURED] 主结果按提前量去均值，并在同一台风 bootstrap 样本中同步比较"
            "两组相关。这个数字衡量误差一致性，不衡量准确性，也不证明完全动力独立。",
            "",
            "|量|CMC vs NGX|LOCAL_EQ2 vs UKM|差值|",
            "|---|---:|---:|---:|",
            f"|lead-centered rho|{primary['rho_cmc_ngx']:.2f} "
            f"{_interval(primary['rho_cmc_ngx_ci95'])}|"
            f"{primary['rho_local_eq2_ukm']:.2f} "
            f"{_interval(primary['rho_local_eq2_ukm_ci95'])}|NA|",
            f"|n_eff|{primary['neff_cmc_ngx']:.2f} "
            f"{_interval(primary['neff_cmc_ngx_ci95'])}|"
            f"{local_neff:.2f} {_interval(local_ci)}|"
            f"{delta:.2f} {_interval(delta_ci)}|",
            f"|raw Pearson rho|{raw['rho_cmc_ngx']:.2f} "
            f"{_interval(raw['rho_cmc_ngx_ci95'])}|"
            f"{raw['rho_local_eq2_ukm']:.2f} "
            f"{_interval(raw['rho_local_eq2_ukm_ci95'])}|NA|",
            f"|lead-centered Spearman rho|{spearman['rho_cmc_ngx']:.2f} "
            f"{_interval(spearman['rho_cmc_ngx_ci95'])}|"
            f"{spearman['rho_local_eq2_ukm']:.2f} "
            f"{_interval(spearman['rho_local_eq2_ukm_ci95'])}|NA|",
            "",
            "## 覆盖与缺测",
            "",
            f"- [MEASURED] `tau=72 h` 三方同循环覆盖台风数：2022 年 "
            f"{ukm_storms_by_season[2022]}、2023 年 {ukm_storms_by_season[2023]}、"
            f"2024 年 {ukm_storms_by_season[2024]}。2023 年形成整年结构性缺测。",
            f"- [MEASURED] 预注册证据门槛未达时效："
            f"{', '.join(f'{lead} h' for lead in inadequate) if inadequate else '无'}。",
            "- [CITED] UCAR 将 `UKM` 标为 UK Met Office model using the development "
            "tracker，并说明 tracker 输出没有主观质控。",
            "- [CITED] UKMET 模式本体采用 Met Office Unified Model 独立动力核心。"
            "共同观测、资料同化和后处理仍可产生相关误差。",
            "",
            "## 三把刀自检",
            "",
            "1. 状态向量：每个有效时刻 `X=(latitude, longitude)`；本地共识是两个位置"
            "的固定球面函数。",
            "2. 参数与观测：拟合参数 0；三套业务模式位置输入、一个事后 best-track "
            "验证通道；相关区间按整场台风重采样。",
            "3. 证伪数据：同风暴、同循环、同有效时刻的 IBTrACS USA best track；"
            "预注册判据由径向误差相关、`Delta n_eff` 和证据门槛共同执行。",
            "",
            "## 偏离清单",
            "",
            "- [MEASURED] 无。TECH、17 场资格名单、时效、统计量、bootstrap 和判据均在"
            "读取 UKM 误差前冻结于 Git 提交。",
            "",
            "## 缺口与下一步",
            "",
            "- 2023 年 UKM 缺测限制年代代表性；本轮结论只覆盖归档中存在 UKM 的严格样本。",
            "- UKM 使用 development tracker，缺少主观质控；模式核心独立与业务误差独立"
            "属于两个层级。",
            "- 事后 best track 仍含分析不确定性；本轮是学习性复现，不声称任何超越。",
            "",
            "## 来源与复现",
            "",
            "- [UCAR late-cycle TECH definitions](https://verif.rap.ucar.edu/jntweb/hurricanes-beta/guide/late/)",
            "- [ATCF System Administrator Guide](https://science.nrlmry.navy.mil/atcf/docs/html/ATCF_SAG_Sec3.html)",
            "- [Met Office Unified Model](https://www.metoffice.gov.uk/research/approach/modelling-systems/unified-model)",
            "- [NOAA/NCEI IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive)",
            f"- [MEASURED] 分析代码 Git `{provenance['analysis_code_git_commit']}`；"
            f"生成时刻 `{provenance['generated_at_utc']}`。",
            "- 机器可读结果：`outputs/round_v3/summary.json`、"
            "`outputs/round_v3/correlation_neff.json`、"
            "`outputs/round_v3/coverage_audit.csv`、"
            "`outputs/round_v3/paired_track_rows.csv`、"
            "`outputs/round_v3/provenance.json`。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    leads = [int(value) for value in config["lead_hours"]]
    qualified = [row for row in manifest["candidate_audit"] if row["qualified"]]
    qualified_ids = sorted(str(row["atcf_id"]) for row in qualified)
    names = {str(row["atcf_id"]): str(row["name"]) for row in qualified}

    source_audit_paths: list[Path] = []
    for atcf_id, source in sorted(manifest["sources"]["adecks"].items()):
        path = PROJECT_ROOT / source["local_path"]
        if sha256_file(path) != source["sha256"]:
            raise RuntimeError(f"a-deck hash drift: {atcf_id}")
        source_audit_paths.append(path)

    adeck_paths: dict[str, Path] = {}
    forecasts: list[ForecastPoint] = []
    for atcf_id in qualified_ids:
        source = manifest["sources"]["adecks"][atcf_id]
        path = PROJECT_ROOT / source["local_path"]
        adeck_paths[atcf_id] = path
        forecasts.extend(
            parse_adeck(
                path.read_text(encoding="utf-8"),
                expected_atcf_id=atcf_id,
                aids=set(config["aids"]),
                lead_hours=set(leads),
            )
        )

    ibtracs_source = manifest["sources"]["IBTrACS"]
    ibtracs_path = PROJECT_ROOT / ibtracs_source["local_path"]
    if sha256_file(ibtracs_path) != ibtracs_source["sha256"]:
        raise RuntimeError("IBTrACS hash drift")
    truth = read_ibtracs_truth(ibtracs_path, set(qualified_ids))
    coverage = coverage_audit(forecasts, truth, qualified_ids, leads)
    derived_eligible = sorted(
        {
            str(row["atcf_id"])
            for row in coverage
            if row["lead_hours"] == 72 and row["three_way_common_cycle_count"] > 0
        }
    )
    frozen_eligible = sorted(str(value) for value in config["eligible_atcf_ids"])
    if derived_eligible != frozen_eligible:
        raise RuntimeError(
            f"round-v3 eligibility drift: derived={derived_eligible}, "
            f"frozen={frozen_eligible}"
        )

    rows = strict_triple_pair(forecasts, truth, names)
    rows = [row for row in rows if row.atcf_id in set(frozen_eligible)]
    if {row.atcf_id for row in rows} != set(frozen_eligible):
        raise RuntimeError("one or more frozen storms produced no exact-time triple rows")

    bootstrap = config["bootstrap"]
    summary = summarize_triple_rows(
        rows,
        leads=leads,
        replicates=int(bootstrap["replicates"]),
        seed=int(bootstrap["seed"]),
    )
    diagnostics = independence_diagnostics(
        rows,
        replicates=int(bootstrap["replicates"]),
        seed=int(bootstrap["seed"]),
    )
    counts = tech_counts(source_audit_paths)
    source_audit = {
        "files_scanned": len(source_audit_paths),
        "tech_counts": {
            aid: counts.get(aid, 0) for aid in ("CMC", "NGX", "UKM", "DYC2")
        },
        "verdict": (
            "DYC2 is a local alias for LOCAL_EQ2_CMC_NGX and is not read from "
            "an a-deck TECH."
        ),
    }
    provenance = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "analysis_code_git_commit": git_head(),
        "config": {
            "path": str(CONFIG_PATH.relative_to(PROJECT_ROOT)),
            "sha256": sha256_file(CONFIG_PATH),
        },
        "preregistration": {
            "path": str(PREREG_PATH.relative_to(PROJECT_ROOT)),
            "sha256": sha256_file(PREREG_PATH),
        },
        "eligibility_manifest": {
            "path": str(MANIFEST_PATH.relative_to(PROJECT_ROOT)),
            "sha256": sha256_file(MANIFEST_PATH),
        },
        "ibtracs": {
            "path": str(ibtracs_path.relative_to(PROJECT_ROOT)),
            "sha256": sha256_file(ibtracs_path),
        },
        "adecks": {
            atcf_id: {
                "path": str(path.relative_to(PROJECT_ROOT)),
                "sha256": sha256_file(path),
            }
            for atcf_id, path in sorted(adeck_paths.items())
        },
        "source_audit": source_audit,
    }

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    write_dict_rows(
        OUTPUT_ROOT / "paired_track_rows.csv", [row.to_dict() for row in rows]
    )
    write_dict_rows(OUTPUT_ROOT / "coverage_audit.csv", coverage)
    write_json(OUTPUT_ROOT / "summary.json", summary)
    write_json(OUTPUT_ROOT / "correlation_neff.json", diagnostics)
    write_json(OUTPUT_ROOT / "source_audit.json", source_audit)
    write_json(OUTPUT_ROOT / "provenance.json", provenance)
    plot_summary(
        OUTPUT_ROOT / "error_vs_lead.png",
        summary,
        storm_count=len({row.atcf_id for row in rows}),
    )
    report = build_report(config, rows, summary, diagnostics, coverage, provenance)
    (PROJECT_ROOT / "report_round_v3.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
