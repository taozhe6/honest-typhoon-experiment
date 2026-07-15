#!/usr/bin/env python3
"""Enforce the permanent semantic boundary between the two C tracks."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".py", ".sh", ".txt"}
PROXY_LABEL = "C-代理"
STRUCTURE_LABEL = "C-结构"
ACTIVE_STRUCTURE_FILES = {
    "PROJECT_REPORT_2026-07-15.md",
    "markov/README.md",
    "markov/docs/c-coverage-correction-protocol.md",
    "markov/report_c_branch.md",
    "markov/report_c_coverage_correction.md",
}


def tracked_text() -> dict[str, str]:
    paths = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8").split("\0")
    contents: dict[str, str] = {}
    for relative in paths:
        if not relative or Path(relative).suffix not in TEXT_SUFFIXES:
            continue
        contents[relative] = (ROOT / relative).read_text(encoding="utf-8")
    return contents


def require_exact(
    errors: list[str], relative: str, content: str, required: str
) -> None:
    if required not in content:
        errors.append(f"{relative}: missing required text: {required}")


def line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def main() -> None:
    files = tracked_text()
    errors: list[str] = []

    readme = files["README.md"]
    required_goal_lines = (
        '- C-代理:输出"未来24h强度波形(减弱-再增强)概率",用 Brier score + 可靠性图评分。',
        "- C-代理标签语义:这是强度波形,不是 ERC。ERC 是其成因之一,非唯一成因。",
        "- C-结构:用一手微波/SAR证据,对可观测时段确证/存疑双环结构。",
        '- C-结构覆盖纪律:只报"确证/存疑/无覆盖",不报次数。',
    )
    for required in required_goal_lines:
        require_exact(errors, "README.md", readme, required)

    if re.search(r"3\s*[-—–~至到]\s*4\s*次", readme):
        errors.append("README.md: the current /goal still contains a 3-4 count")

    report = files["PROJECT_REPORT_2026-07-15.md"]
    require_exact(
        errors,
        "PROJECT_REPORT_2026-07-15.md",
        report,
        "## C-代理：未来 24 h 强度波形概率",
    )
    require_exact(
        errors,
        "PROJECT_REPORT_2026-07-15.md",
        report,
        "## C-结构：一手双环结构观测",
    )
    require_exact(
        errors,
        "markov/report_c_event_label_v2.md",
        files["markov/report_c_event_label_v2.md"],
        "# C-代理：未来 24 h 减弱—再增强强度波形标签 v2",
    )
    require_exact(
        errors,
        "markov/report_c_coverage_correction.md",
        files["markov/report_c_coverage_correction.md"],
        "# C-结构：巴威一手微波覆盖与证据等级",
    )

    future_erc = re.compile(
        r"未来\s*24\s*(?:h|小时)\s*(?:内)?\s*(?:的)?\s*ERC",
        re.IGNORECASE,
    )
    direct_erc_prediction = re.compile(
        r"(?:预测\s*ERC|ERC\s*预测)", re.IGNORECASE
    )
    proxy_as_erc_probability = re.compile(
        r"(?:C-代理|强度波形)[^。！？!?\n]{0,100}ERC\s*概率"
        r"|ERC\s*概率[^。！？!?\n]{0,100}(?:C-代理|强度波形)",
        re.IGNORECASE,
    )
    prohibition_markers = ("不得", "禁止", "严禁", "不可", "不能", "不应")

    for relative, content in files.items():
        for match in future_erc.finditer(content):
            errors.append(
                f"{relative}:{line_number(content, match.start())}: "
                "future-24h output is labelled as ERC"
            )
        for pattern, label in (
            (direct_erc_prediction, "direct ERC-prediction wording"),
            (proxy_as_erc_probability, "C-proxy is labelled as ERC probability"),
        ):
            for match in pattern.finditer(content):
                start = content.rfind("\n", 0, match.start()) + 1
                end = content.find("\n", match.end())
                line = content[start:] if end < 0 else content[start:end]
                if any(marker in line for marker in prohibition_markers):
                    continue
                errors.append(
                    f"{relative}:{line_number(content, match.start())}: {label}"
                )

        for sentence_match in re.finditer(r"[^。！？!?\n]+", content):
            sentence = sentence_match.group(0)
            if PROXY_LABEL in sentence and STRUCTURE_LABEL in sentence:
                errors.append(
                    f"{relative}:{line_number(content, sentence_match.start())}: "
                    "both C-track labels share one sentence"
                )

        for match in re.finditer(r"3\s*[-—–~至到]\s*4\s*次", content):
            start = content.rfind("\n", 0, match.start()) + 1
            end = content.find("\n", match.end())
            line = content[start:] if end < 0 else content[start:end]
            if not any(
                marker in line
                for marker in ("旧", "撤回", "原任务", "历史", "否定")
            ):
                errors.append(
                    f"{relative}:{line_number(content, match.start())}: "
                    "unqualified historical 3-4 count"
                )

    aggregate_verdict = re.compile(
        r"(?:确证|存疑|无覆盖)\s*(?:[0-9]+|[NMK])\s*(?:个|次|段|窗口)?"
        r"|(?:支持|确认|构成|形成)\s*(?:至少)?\s*"
        r"[0-9一二三四五六七八九十]+\s*个[^。\n]{0,30}(?:双环|ERC)",
        re.IGNORECASE,
    )
    for relative in ACTIVE_STRUCTURE_FILES:
        content = files[relative]
        for match in aggregate_verdict.finditer(content):
            start = content.rfind("\n", 0, match.start()) + 1
            end = content.find("\n", match.end())
            line = content[start:] if end < 0 else content[start:end]
            if any(marker in line for marker in ("旧", "撤回", "历史")):
                continue
            errors.append(
                f"{relative}:{line_number(content, match.start())}: "
                "C-structure verdict is aggregated into a count"
            )

    if errors:
        raise SystemExit("C-track semantic check failed:\n- " + "\n- ".join(errors))

    print("C-track semantic check passed.")
    print("C-proxy waveform-probability wording passed.")
    print("C-structure observable-period wording passed.")


if __name__ == "__main__":
    main()
