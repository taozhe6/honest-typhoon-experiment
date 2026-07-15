"""Published concentric-eyewall/ERC resource audit and table parsing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
import urllib.request


ROW_PATTERN = re.compile(
    r"^\s*(?P<tc_number>\d{4}-\d{2}[WC](?:\(2\))?)\s+"
    r"(?P<name>[A-Za-z-]+)\s+"
    r"(?P<vmax>\d+)\s+"
    r"(?P<vmax_time>\d{6}Z)\s+"
    r"(?P<latitude>[0-9.]+N)\s+"
    r"(?P<longitude>[0-9.]+E)\s+"
    r"(?P<formation_intensity>\d+)\s+"
    r"(?P<satellite_time>\d{4}-\d{4}Z?\((?:SSMI|TMI)\))\s+"
    r"(?P<hours_from_vmax>-?\d+)\s+"
    r"(?P<intensity_change>PN|PP|NN|NP|landfall)\s+"
    r"(?P<inner_radius>[0-9.]+)\s+"
    r"(?P<moat_width>[0-9.]+)\s*$"
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def extract_pdf_text(path: Path) -> str:
    completed = subprocess.run(
        ("pdftotext", "-layout", str(path), "-"),
        check=True,
        capture_output=True,
    )
    return completed.stdout.decode("utf-8", errors="replace")


def parse_kuo_ce_table(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = ROW_PATTERN.match(line)
        if not match:
            continue
        values = match.groupdict()
        tc_number = values["tc_number"]
        second = tc_number.endswith("(2)")
        rows.append(
            {
                "published_case_id": tc_number,
                "tc_number": tc_number.removesuffix("(2)"),
                "occurrence_for_tc": 2 if second else 1,
                "tc_name": values["name"],
                "vmax_kt": int(values["vmax"]),
                "vmax_time_mmddhhz": values["vmax_time"],
                "formation_latitude_deg_n": float(values["latitude"][:-1]),
                "formation_longitude_deg_e": float(values["longitude"][:-1]),
                "formation_intensity_kt": int(values["formation_intensity"]),
                "satellite_image_time": values["satellite_time"],
                "sensor": values["satellite_time"].split("(")[-1].rstrip(")"),
                "formation_minus_vmax_hours": int(values["hours_from_vmax"]),
                "intensity_change_class": values["intensity_change"],
                "inner_eyewall_radius_km": float(values["inner_radius"]),
                "moat_width_km": float(values["moat_width"]),
                "label_semantics": "published concentric-eyewall formation case",
                "erc_onset_label": None,
                "source": "Kuo et al. 2009 supplementary CE table",
            }
        )
    return rows


def fetch_bytes(url: str, timeout_seconds: float = 60.0) -> tuple[bytes, dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "typhoon-erc-published-resource-audit/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read()
        return payload, {
            "status": getattr(response, "status", 200),
            "final_url": response.geturl(),
            "content_type": response.headers.get("Content-Type"),
            "bytes": len(payload),
            "sha256": sha256_bytes(payload),
        }


def payload_text(payload: bytes, suffix: str) -> str:
    if suffix.lower() == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
            handle.write(payload)
            handle.flush()
            return extract_pdf_text(Path(handle.name))
    return payload.decode("utf-8", errors="replace")


def audit_resource_registry(
    registry_path: Path,
    *,
    cache_directory: Path,
) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    cache_directory.mkdir(parents=True, exist_ok=True)
    audited: list[dict[str, Any]] = []
    for resource in registry["resources"]:
        result = dict(resource)
        cache_name = resource.get("cache_filename")
        cache_path = cache_directory / cache_name if cache_name else None
        try:
            if cache_path is not None and cache_path.exists():
                payload = cache_path.read_bytes()
                access = {
                    "status": "cached",
                    "final_url": resource["url"],
                    "content_type": resource.get("media_type"),
                    "bytes": len(payload),
                    "sha256": sha256_bytes(payload),
                }
            else:
                payload, access = fetch_bytes(resource["url"])
                if cache_path is not None:
                    cache_path.write_bytes(payload)
            if resource.get("media_type") == "application/pdf":
                suffix = ".pdf"
            else:
                suffix = cache_path.suffix if cache_path is not None else Path(
                    resource["url"].split("?", 1)[0]
                ).suffix
            text = payload_text(payload, suffix)
            phrases = resource.get("expected_phrases", [])
            access["expected_phrase_checks"] = {
                phrase: phrase.lower() in text.lower() for phrase in phrases
            }
            access["all_expected_phrases_found"] = all(
                access["expected_phrase_checks"].values()
            )
            if cache_path is not None:
                access["cache_path"] = str(cache_path.resolve())
            result["access"] = access
        except Exception as error:  # Each public endpoint is independently audited.
            result["access"] = {
                "status": "error",
                "error_type": type(error).__name__,
                "message": str(error),
            }
        audited.append(result)
    return {
        "schema_version": registry["schema_version"],
        "registry_path": str(registry_path.resolve()),
        "registry_sha256": sha256_file(registry_path),
        "resources": audited,
    }
