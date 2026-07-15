#!/usr/bin/env python3
"""Build the frozen Bavi 2026 case from archived source messages."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Any
from urllib.request import Request, urlopen

from typhoon_markov.case_validation import wind_to_ms


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "cases" / "bavi_2026_reintensification_spec.json"
DEFAULT_OUTPUT = ROOT / "cases" / "bavi_2026_reintensification.json"
CONTRACT_PATH = ROOT / "config" / "wind_observation_contract.json"
USER_AGENT = "typhoon-markov-case-builder/1.0"

NMC_ANALYSIS_FIELDS = {
    "valid_time": 1,
    "classification": 3,
    "longitude": 4,
    "latitude": 5,
    "pressure_hpa": 6,
    "wind_ms": 7,
    "forecasts": 11,
}
NMC_FORECAST_FIELDS = {
    "lead_hours": 0,
    "base_time": 1,
    "longitude": 2,
    "latitude": 3,
    "pressure_hpa": 4,
    "wind_ms": 5,
    "center": 6,
    "classification": 7,
}


class GdacsMessageParser(HTMLParser):
    """Extract original GTS messages from GDACS without scraping display text."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: dict[str, str] = {}
        self._message_id: str | None = None
        self._div_depth = 0
        self._inside_message = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id") or ""
        if tag == "div" and element_id.startswith("gts_"):
            self._message_id = element_id.removeprefix("gts_")
            self._div_depth = 1
            self._parts = []
            return
        if self._message_id is not None and tag == "div":
            self._div_depth += 1
        if self._message_id is not None and tag == "pre_gts":
            self._inside_message = True
        if self._inside_message and tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._message_id is not None and tag == "pre_gts":
            self._inside_message = False
        if self._message_id is not None and tag == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self.messages[self._message_id] = "".join(self._parts).strip()
                self._message_id = None

    def handle_data(self, data: str) -> None:
        if self._inside_message:
            self._parts.append(data)


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compact_dtg(iso_time: str) -> str:
    value = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return value.strftime("%Y%m%d%H%M")


def atcf_dtg(iso_time: str) -> str:
    value = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    return value.strftime("%Y%m%d%H")


def extract_gdacs_messages(html: str) -> dict[str, str]:
    parser = GdacsMessageParser()
    parser.feed(html)
    return parser.messages


def parse_jma_advisory(message: str) -> dict[str, Any]:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    try:
        analysis_index = lines.index("ANALYSIS")
        forecast_index = lines.index("FORECAST")
    except ValueError as error:
        raise ValueError("JMA advisory lacks ANALYSIS or FORECAST section") from error

    analysis_lines = lines[analysis_index + 1 : forecast_index]
    position_line = next(line for line in analysis_lines if line.startswith("PSTN"))
    wind_line = next(line for line in analysis_lines if line.startswith("MXWD"))
    pressure_line = next(line for line in analysis_lines if line.startswith("PRES"))
    position = re.fullmatch(
        r"PSTN\s+(\d{6})UTC\s+([0-9.]+)([NS])\s+([0-9.]+)([EW])\s+.*",
        position_line,
    )
    if position is None:
        raise ValueError(f"cannot parse JMA analysis position: {position_line}")

    forecast_starts = [
        index
        for index in range(forecast_index + 1, len(lines))
        if re.match(r"^\d+HF\s+\d{6}UTC\b", lines[index])
    ]
    forecasts: dict[int, dict[str, Any]] = {}
    for ordinal, start in enumerate(forecast_starts):
        end = forecast_starts[ordinal + 1] if ordinal + 1 < len(forecast_starts) else len(lines)
        block = lines[start:end]
        header = re.match(
            r"^(\d+)HF\s+(\d{6})UTC\s+([0-9.]+)([NS])\s+([0-9.]+)([EW]).*",
            block[0],
        )
        if header is None:
            raise ValueError(f"cannot parse JMA forecast header: {block[0]}")
        lead = int(header.group(1))
        block_winds = [line for line in block if line.startswith("MXWD")]
        block_pressures = [line for line in block if line.startswith("PRES")]
        if not block_winds or not block_pressures:
            continue
        block_wind = block_winds[0]
        block_pressure = block_pressures[0]
        forecasts[lead] = {
            "valid_ddhhmm": header.group(2),
            "latitude": signed_coordinate(header.group(3), header.group(4)),
            "longitude": signed_coordinate(header.group(5), header.group(6)),
            "wind_kt": parse_number(block_wind, r"MXWD\s+(\d+)KT"),
            "pressure_hpa": parse_number(block_pressure, r"PRES\s+(\d+)HPA"),
        }
    return {
        "analysis": {
            "valid_ddhhmm": position.group(1),
            "latitude": signed_coordinate(position.group(2), position.group(3)),
            "longitude": signed_coordinate(position.group(4), position.group(5)),
            "wind_kt": parse_number(wind_line, r"MXWD\s+(\d+)KT"),
            "pressure_hpa": parse_number(pressure_line, r"PRES\s+(\d+)HPA"),
        },
        "forecasts": forecasts,
    }


def parse_jtwc_warning(message: str) -> dict[str, Any]:
    analysis_match = re.search(r"^(\d{10})\s+09W\s+BAVI\b", message, re.MULTILINE)
    if analysis_match is None:
        raise ValueError("cannot find JTWC ATCF analysis timestamp")
    forecasts: dict[int, dict[str, Any]] = {}
    pattern = re.compile(
        r"^T(?P<lead>\d{3})\s+"
        r"(?P<lat>\d{3})(?P<lat_hemi>[NS])\s+"
        r"(?P<lon>\d{4})(?P<lon_hemi>[EW])\s+"
        r"(?P<wind>\d{3})\b",
        re.MULTILINE,
    )
    for match in pattern.finditer(message):
        lead = int(match.group("lead"))
        forecasts[lead] = {
            "latitude": signed_coordinate(
                str(int(match.group("lat")) / 10.0), match.group("lat_hemi")
            ),
            "longitude": signed_coordinate(
                str(int(match.group("lon")) / 10.0), match.group("lon_hemi")
            ),
            "wind_kt": int(match.group("wind")),
        }
    if 0 not in forecasts:
        raise ValueError("JTWC warning lacks T000 analysis")
    return {"analysis_dtg": analysis_match.group(1), "forecasts": forecasts}


def parse_carq_analysis(adeck: str, dtg: str) -> dict[str, Any]:
    matches: list[list[str]] = []
    for row in csv.reader(adeck.splitlines()):
        fields = [field.strip() for field in row]
        if len(fields) < 12:
            continue
        if fields[2] == dtg and fields[4] == "CARQ" and fields[5] == "0":
            matches.append(fields)
    if not matches:
        raise ValueError(f"CARQ analysis {dtg} is absent")
    winds = {int(row[8]) for row in matches}
    pressures = {int(row[9]) for row in matches}
    positions = {(row[6], row[7]) for row in matches}
    if len(winds) != 1 or len(pressures) != 1 or len(positions) != 1:
        raise ValueError(f"CARQ duplicate rows disagree at {dtg}")
    row = matches[0]
    return {
        "dtg": dtg,
        "latitude": parse_atcf_coordinate(row[6]),
        "longitude": parse_atcf_coordinate(row[7]),
        "wind_kt": int(row[8]),
        "pressure_hpa": int(row[9]),
    }


def parse_nmc_detail(raw: str) -> tuple[list[Any], list[list[Any]]]:
    start = raw.find("(")
    end = raw.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("NMC callback wrapper is malformed")
    payload = json.loads(raw[start + 1 : end])
    storm = payload["typhoon"]
    candidates = [
        value
        for value in storm
        if isinstance(value, list)
        and value
        and isinstance(value[0], list)
        and len(value[0]) > NMC_ANALYSIS_FIELDS["forecasts"]
        and re.fullmatch(r"\d{12}", str(value[0][NMC_ANALYSIS_FIELDS["valid_time"]]))
    ]
    if len(candidates) != 1:
        raise ValueError("NMC track array discovery is ambiguous")
    return storm, candidates[0]


def nmc_analysis(track: list[list[Any]], dtg: str) -> dict[str, Any]:
    matches = [row for row in track if row[NMC_ANALYSIS_FIELDS["valid_time"]] == dtg]
    if len(matches) != 1:
        raise ValueError(f"expected one NMC analysis at {dtg}, found {len(matches)}")
    row = matches[0]
    return {
        "valid_dtg": row[NMC_ANALYSIS_FIELDS["valid_time"]],
        "classification": row[NMC_ANALYSIS_FIELDS["classification"]],
        "longitude": float(row[NMC_ANALYSIS_FIELDS["longitude"]]),
        "latitude": float(row[NMC_ANALYSIS_FIELDS["latitude"]]),
        "pressure_hpa": int(row[NMC_ANALYSIS_FIELDS["pressure_hpa"]]),
        "wind_ms": float(row[NMC_ANALYSIS_FIELDS["wind_ms"]]),
        "raw_record_id": row[0],
    }


def nmc_forecast(
    track: list[list[Any]], issue_dtg: str, center: str, lead_hours: int
) -> dict[str, Any]:
    rows = [row for row in track if row[NMC_ANALYSIS_FIELDS["valid_time"]] == issue_dtg]
    if len(rows) != 1:
        raise ValueError(f"expected one NMC forecast issue at {issue_dtg}")
    forecast_map = rows[0][NMC_ANALYSIS_FIELDS["forecasts"]]
    candidates = [
        row
        for row in forecast_map[center]
        if int(row[NMC_FORECAST_FIELDS["lead_hours"]]) == lead_hours
    ]
    if len(candidates) != 1:
        raise ValueError(f"expected one NMC {center} forecast at lead {lead_hours}")
    row = candidates[0]
    base = datetime.strptime(
        row[NMC_FORECAST_FIELDS["base_time"]], "%Y%m%d%H%M"
    ).replace(tzinfo=timezone.utc)
    return {
        "base_dtg": row[NMC_FORECAST_FIELDS["base_time"]],
        "valid_at_utc": iso_utc(base + timedelta(hours=lead_hours)),
        "lead_hours": int(row[NMC_FORECAST_FIELDS["lead_hours"]]),
        "longitude": float(row[NMC_FORECAST_FIELDS["longitude"]]),
        "latitude": float(row[NMC_FORECAST_FIELDS["latitude"]]),
        "pressure_hpa": int(row[NMC_FORECAST_FIELDS["pressure_hpa"]]),
        "wind_ms": float(row[NMC_FORECAST_FIELDS["wind_ms"]]),
        "center": row[NMC_FORECAST_FIELDS["center"]],
        "classification": row[NMC_FORECAST_FIELDS["classification"]],
    }


def parse_number(value: str, pattern: str) -> int:
    match = re.fullmatch(pattern, value)
    if match is None:
        raise ValueError(f"cannot parse numeric field: {value}")
    return int(match.group(1))


def signed_coordinate(value: str, hemisphere: str) -> float:
    coordinate = float(value)
    return -coordinate if hemisphere in {"S", "W"} else coordinate


def parse_atcf_coordinate(value: str) -> float:
    match = re.fullmatch(r"(\d+)([NSEW])", value)
    if match is None:
        raise ValueError(f"invalid ATCF coordinate: {value}")
    return signed_coordinate(str(int(match.group(1)) / 10.0), match.group(2))


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def make_record(
    *,
    agency: str,
    stage: str,
    issued_at: str,
    valid_at: str,
    wind_value: float,
    wind_unit: str,
    source_id: str,
    contract: dict[str, Any],
    latitude: float,
    longitude: float,
    pressure_hpa: int,
) -> dict[str, Any]:
    agency_contract = contract["agencies"][agency]
    return {
        "agency": agency,
        "stage": stage,
        "issued_at_utc": issued_at,
        "valid_at_utc": valid_at,
        "wind_value_native": wind_value,
        "wind_unit_native": wind_unit,
        "wind_value_ms": wind_to_ms(wind_value, wind_unit),
        "wind_averaging_period_seconds": agency_contract[
            "wind_averaging_period_seconds"
        ],
        "wind_height_m": agency_contract["wind_height_m"],
        "wind_definition": agency_contract["wind_definition"],
        "latitude_deg": latitude,
        "longitude_deg": longitude,
        "central_pressure_hpa": pressure_hpa,
        "source_id": source_id,
    }


def build_case(spec: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    source_text = {
        source_id: fetch_text(details["url"])
        for source_id, details in spec["sources"].items()
    }
    gdacs_messages = extract_gdacs_messages(source_text["gdacs_gts"])

    nmc_spec = spec["channels"]["NMC"]
    nmc_storm, nmc_track = parse_nmc_detail(source_text[nmc_spec["source_id"]])
    if str(nmc_storm[3]) != spec["storm"]["international_number"]:
        raise ValueError("NMC storm number does not match the frozen case specification")
    nmc_issue_dtg = compact_dtg(nmc_spec["initial_valid_at_utc"])
    nmc_target_dtg = compact_dtg(nmc_spec["target_valid_at_utc"])
    nmc_initial = nmc_analysis(nmc_track, nmc_issue_dtg)
    nmc_prediction = nmc_forecast(
        nmc_track,
        nmc_issue_dtg,
        nmc_spec["forecast_center"],
        nmc_spec["lead_hours"],
    )
    nmc_truth = nmc_analysis(nmc_track, nmc_target_dtg)
    if nmc_prediction["valid_at_utc"] != nmc_spec["target_valid_at_utc"]:
        raise ValueError("NMC forecast valid time does not match the case target")

    jma_spec = spec["channels"]["JMA"]
    jma_forecast_message = gdacs_messages[jma_spec["forecast_message_id"]]
    jma_truth_message = gdacs_messages[jma_spec["truth_message_id"]]
    jma_issue = parse_jma_advisory(jma_forecast_message)
    jma_later = parse_jma_advisory(jma_truth_message)
    jma_prediction = jma_issue["forecasts"][jma_spec["lead_hours"]]

    jtwc_spec = spec["channels"]["JTWC"]
    jtwc_message = gdacs_messages[jtwc_spec["forecast_message_id"]]
    jtwc_warning = parse_jtwc_warning(jtwc_message)
    jtwc_initial_carq = parse_carq_analysis(
        source_text[jtwc_spec["truth_source_id"]], jtwc_spec["initial_carq_dtg"]
    )
    jtwc_truth_carq = parse_carq_analysis(
        source_text[jtwc_spec["truth_source_id"]], jtwc_spec["target_carq_dtg"]
    )
    jtwc_t000 = jtwc_warning["forecasts"][0]
    jtwc_prediction = jtwc_warning["forecasts"][jtwc_spec["lead_hours"]]
    if jtwc_t000["wind_kt"] != jtwc_initial_carq["wind_kt"]:
        raise ValueError("JTWC warning T000 and CARQ initial winds disagree")
    if jtwc_warning["analysis_dtg"] != jtwc_spec["initial_carq_dtg"]:
        raise ValueError("JTWC warning analysis time does not match case specification")

    channels = {
        "NMC": {
            "agency": "NMC",
            "initial_analysis": make_record(
                agency="NMC",
                stage="ingest_initial_analysis",
                issued_at=nmc_spec["forecast_issued_at_utc"],
                valid_at=nmc_spec["initial_valid_at_utc"],
                wind_value=nmc_initial["wind_ms"],
                wind_unit=nmc_spec["wind_unit_native"],
                source_id=nmc_spec["source_id"],
                contract=contract,
                latitude=nmc_initial["latitude"],
                longitude=nmc_initial["longitude"],
                pressure_hpa=nmc_initial["pressure_hpa"],
            ),
            "forecast": make_record(
                agency="NMC",
                stage="forecast_output",
                issued_at=nmc_spec["forecast_issued_at_utc"],
                valid_at=nmc_spec["target_valid_at_utc"],
                wind_value=nmc_prediction["wind_ms"],
                wind_unit=nmc_spec["wind_unit_native"],
                source_id=nmc_spec["source_id"],
                contract=contract,
                latitude=nmc_prediction["latitude"],
                longitude=nmc_prediction["longitude"],
                pressure_hpa=nmc_prediction["pressure_hpa"],
            ),
            "truth": make_record(
                agency="NMC",
                stage="verification_truth",
                issued_at=nmc_spec["target_valid_at_utc"],
                valid_at=nmc_spec["target_valid_at_utc"],
                wind_value=nmc_truth["wind_ms"],
                wind_unit=nmc_spec["wind_unit_native"],
                source_id=nmc_spec["source_id"],
                contract=contract,
                latitude=nmc_truth["latitude"],
                longitude=nmc_truth["longitude"],
                pressure_hpa=nmc_truth["pressure_hpa"],
            ),
        },
        "JMA": {
            "agency": "JMA",
            "initial_analysis": make_record(
                agency="JMA",
                stage="ingest_initial_analysis",
                issued_at=jma_spec["forecast_issued_at_utc"],
                valid_at=jma_spec["initial_valid_at_utc"],
                wind_value=jma_issue["analysis"]["wind_kt"],
                wind_unit=jma_spec["wind_unit_native"],
                source_id=jma_spec["source_id"],
                contract=contract,
                latitude=jma_issue["analysis"]["latitude"],
                longitude=jma_issue["analysis"]["longitude"],
                pressure_hpa=jma_issue["analysis"]["pressure_hpa"],
            ),
            "forecast": make_record(
                agency="JMA",
                stage="forecast_output",
                issued_at=jma_spec["forecast_issued_at_utc"],
                valid_at=jma_spec["target_valid_at_utc"],
                wind_value=jma_prediction["wind_kt"],
                wind_unit=jma_spec["wind_unit_native"],
                source_id=jma_spec["source_id"],
                contract=contract,
                latitude=jma_prediction["latitude"],
                longitude=jma_prediction["longitude"],
                pressure_hpa=jma_prediction["pressure_hpa"],
            ),
            "truth": make_record(
                agency="JMA",
                stage="verification_truth",
                issued_at=jma_spec["target_valid_at_utc"],
                valid_at=jma_spec["target_valid_at_utc"],
                wind_value=jma_later["analysis"]["wind_kt"],
                wind_unit=jma_spec["wind_unit_native"],
                source_id=jma_spec["source_id"],
                contract=contract,
                latitude=jma_later["analysis"]["latitude"],
                longitude=jma_later["analysis"]["longitude"],
                pressure_hpa=jma_later["analysis"]["pressure_hpa"],
            ),
        },
        "JTWC": {
            "agency": "JTWC",
            "initial_analysis": make_record(
                agency="JTWC",
                stage="ingest_initial_analysis",
                issued_at=jtwc_spec["forecast_issued_at_utc"],
                valid_at=jtwc_spec["initial_valid_at_utc"],
                wind_value=jtwc_initial_carq["wind_kt"],
                wind_unit=jtwc_spec["wind_unit_native"],
                source_id=jtwc_spec["truth_source_id"],
                contract=contract,
                latitude=jtwc_initial_carq["latitude"],
                longitude=jtwc_initial_carq["longitude"],
                pressure_hpa=jtwc_initial_carq["pressure_hpa"],
            ),
            "forecast": make_record(
                agency="JTWC",
                stage="forecast_output",
                issued_at=jtwc_spec["forecast_issued_at_utc"],
                valid_at=jtwc_spec["target_valid_at_utc"],
                wind_value=jtwc_prediction["wind_kt"],
                wind_unit=jtwc_spec["wind_unit_native"],
                source_id=jtwc_spec["forecast_source_id"],
                contract=contract,
                latitude=jtwc_prediction["latitude"],
                longitude=jtwc_prediction["longitude"],
                pressure_hpa=jtwc_initial_carq["pressure_hpa"],
            ),
            "truth": make_record(
                agency="JTWC",
                stage="verification_truth",
                issued_at=jtwc_spec["target_valid_at_utc"],
                valid_at=jtwc_spec["target_valid_at_utc"],
                wind_value=jtwc_truth_carq["wind_kt"],
                wind_unit=jtwc_spec["wind_unit_native"],
                source_id=jtwc_spec["truth_source_id"],
                contract=contract,
                latitude=jtwc_truth_carq["latitude"],
                longitude=jtwc_truth_carq["longitude"],
                pressure_hpa=jtwc_truth_carq["pressure_hpa"],
            ),
        },
    }

    retrieved = iso_utc(datetime.now(timezone.utc))
    selected_evidence = {
        "nmc_detail": json.dumps(
            {
                "initial": nmc_initial,
                "forecast": nmc_prediction,
                "truth": nmc_truth,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        "gdacs_gts": "\n---MESSAGE---\n".join(
            (jma_forecast_message, jma_truth_message, jtwc_message)
        ),
        "ucar_adeck": json.dumps(
            {"initial": jtwc_initial_carq, "truth": jtwc_truth_carq},
            sort_keys=True,
            separators=(",", ":"),
        ),
    }
    return {
        "schema_version": 1,
        "case_id": spec["case_id"],
        "storm": spec["storm"],
        "purpose": spec["purpose"],
        "selection_rule": spec["selection_rule"],
        "generated_at_utc": retrieved,
        "observation_contract_path": "config/wind_observation_contract.json",
        "channels": channels,
        "model_evaluation": spec["model_evaluation"],
        "provenance": {
            "sources": {
                source_id: {
                    **details,
                    "retrieved_at_utc": retrieved,
                    "response_bytes_utf8": len(source_text[source_id].encode("utf-8")),
                    "response_sha256": sha256_text(source_text[source_id]),
                    "selected_evidence_bytes_utf8": len(
                        selected_evidence[source_id].encode("utf-8")
                    ),
                    "selected_evidence_sha256": sha256_text(
                        selected_evidence[source_id]
                    ),
                }
                for source_id, details in spec["sources"].items()
            },
            "message_excerpts": {
                jma_spec["forecast_message_id"]: jma_forecast_message,
                jma_spec["truth_message_id"]: jma_truth_message,
                jtwc_spec["forecast_message_id"]: "\n".join(
                    jtwc_message.splitlines()[:35]
                ),
            },
            "nmc_selected_records": {
                "initial": nmc_initial,
                "forecast": nmc_prediction,
                "truth": nmc_truth,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    case = build_case(spec, contract)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    for agency, channel in case["channels"].items():
        print(
            agency,
            channel["initial_analysis"]["wind_averaging_period_seconds"],
            channel["initial_analysis"]["wind_value_native"],
            channel["forecast"]["wind_value_native"],
            channel["truth"]["wind_value_native"],
            channel["initial_analysis"]["wind_unit_native"],
        )


if __name__ == "__main__":
    main()
