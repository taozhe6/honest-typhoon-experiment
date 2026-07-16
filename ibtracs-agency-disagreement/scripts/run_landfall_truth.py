#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd
import shapefile
from shapely.geometry import Point, shape


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ibtracs_measurement.data import AGENCIES  # noqa: E402
from ibtracs_measurement.landfall_truth import (  # noqa: E402
    BOOTSTRAP_SEED,
    SEARCH_RADIUS_KM,
    SEARCH_WINDOW_HOURS,
    Event,
    bootstrap_error_correlations,
    coverage_table,
    cwa_station_type,
    finalize_records,
    haversine_km,
    hko_impact_records,
    load_events,
    parse_jma_hourly_csv,
    parse_jma_station_html,
    score_agencies,
    select_cwa_station_records,
    select_isd_event_records,
    select_jma_station_records,
    sha256_bytes,
    sha256_file,
    source_event_status_table,
    utc_now_iso,
    write_json,
)


LANDFALL_PATH = ROOT / "outputs" / "b_branch" / "landfall_cma_reference_rows.csv"
OUTPUT_DIR = ROOT / "outputs" / "b_branch"
RAW_DIR = ROOT / "data" / "raw" / "landfall_truth"
REPORT_PATH = ROOT / "landfall_truth_report.md"
CWA_EYEWALL_REVIEW_PATH = ROOT / "data" / "cwa_tdb_eyewall_review.csv"

USER_AGENT = "honest-typhoon-landfall-truth/1.0 (+public research audit)"
ISD_HISTORY_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
ISD_BASE_URL = "https://www.ncei.noaa.gov/data/global-hourly/access"
CWA_STATION_LIST_URL = "https://codis.cwa.gov.tw/api/station_list"
CWA_STATION_API_URL = "https://codis.cwa.gov.tw/api/station"
JMA_PREFECTURE_INDEX_URL = (
    "https://www.data.jma.go.jp/stats/etrn/select/prefecture00.php"
)
JMA_STATION_URL = "https://ds.data.jma.go.jp/risk/obsdl/top/station"
JMA_DOWNLOAD_ROOT = "https://ds.data.jma.go.jp/risk/obsdl/"
JMA_DOWNLOAD_URL = "https://ds.data.jma.go.jp/risk/obsdl/show/table"
HKO_IMPACT_URL = "https://www.hko.gov.hk/en/informtc/files/TC_Impact_Data_HKO.xlsx"
HKO_STATION_URL = "https://www.hko.gov.hk/en/cis/stn.htm"
CWA_TDB_BASE_URL = "https://rdc28.cwa.gov.tw/TDB/public"
CWA_TDB_WARNING_LIST_URL = f"{CWA_TDB_BASE_URL}/warning_typhoon_list/"
CWA_TDB_DETAIL_URL = f"{CWA_TDB_BASE_URL}/typhoon_detail"
CWA_TDB_PRODUCT_URL = f"{CWA_TDB_DETAIL_URL}/get_product"
CWA_TDB_IMAGE_URL = f"{CWA_TDB_DETAIL_URL}/get_image"
CWA_TDB_PDF_URL = f"{CWA_TDB_DETAIL_URL}/get_pdf"
CWA_TDB_WIND_URL = f"{CWA_TDB_BASE_URL}/wind_gust_statistics/"
CWA_TDB_PRODUCTS = ("Radar", "WeatherInfo", "GustHis", "RainInfo")
NATURAL_EARTH_COUNTRIES_URL = (
    "https://naturalearth.s3.amazonaws.com/10m_cultural/"
    "ne_10m_admin_0_countries.zip"
)


SOURCE_AUDIT = [
    {
        "source_id": "NOAA_ISD",
        "source": "NOAA/NCEI Global Hourly (ISD/GTS)",
        "availability": "available",
        "evidence": "[验证过] direct anonymous station-year CSV downloads succeeded",
        "access": "anonymous HTTPS WAF",
        "format": "CSV; WND field with report type and QC",
        "independence": "raw station/GTS measurement; operational information-set overlap disclosed",
        "window": "WND type N has no universal encoded averaging period; H/R/T encode 5/60/180 min",
        "url": "https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database",
    },
    {
        "source_id": "CWA_CODIS",
        "source": "Taiwan CWA CODiS station archive",
        "availability": "available",
        "evidence": "[验证过] anonymous station-list and multi-station historical POST requests succeeded",
        "access": "anonymous HTTPS web API used by CODiS",
        "format": "JSON; hourly bins with TenMinutelyMaximum/Mean/PeakGust and flags",
        "independence": "instrument records; CWA and exchanged operational analyses can ingest them",
        "window": "CWA conventional stations expose native 10-minute maximum within each hour",
        "url": "https://codis.cwa.gov.tw/StationData",
    },
    {
        "source_id": "CWA_RADAR",
        "source": "Taiwan CWA Typhoon Database radar and station support archive",
        "availability": "partially_available",
        "evidence": "[验证过] anonymous warning registry, station time series, 6-minute reflectivity images and official PDFs were enumerated for every frozen Taiwan case",
        "access": "anonymous HTTPS endpoints used by the public database",
        "format": "JSON metadata and station rows; JPEG/GIF/PDF products",
        "independence": "station rows duplicate CODiS instruments; reflectivity locates structure but is not a surface-wind observation operator",
        "window": "official-station WSMax is native 10-minute within hourly bins; automatic-station WS window remains undocumented; radar cadence is nominally 6 minutes",
        "url": "https://rdc28.cwa.gov.tw/TDB/public/",
    },
    {
        "source_id": "HKO_TC_IMPACT",
        "source": "HKO Tropical Cyclone Impact Dataset and annual publications",
        "availability": "partially_available",
        "evidence": "[验证过] anonymous official XLSX download contains station passage maxima through 2025",
        "access": "anonymous HTTPS",
        "format": "XLSX; gust and maximum hourly mean wind by station",
        "independence": "instrument summaries; HKO operational information-set overlap disclosed",
        "window": "60-minute means and passage-wide maxima; exact peak time absent in workbook",
        "url": "https://www.hko.gov.hk/en/informtc/tc_impact.html",
    },
    {
        "source_id": "HKO_10MIN_LATEST",
        "source": "HKO regional 10-minute wind open data",
        "availability": "partially_available",
        "evidence": "[据文档] public feed is explicitly the latest value; historical 2015-2024 snapshots are not supplied",
        "access": "anonymous latest-only CSV",
        "format": "CSV",
        "independence": "instrument observation",
        "window": "native 10-minute",
        "url": "https://www.hko.gov.hk/en/abouthko/opendata_intro.htm",
    },
    {
        "source_id": "JMA_AMEDAS",
        "source": "JMA historical AMeDAS/surface observation download",
        "availability": "available",
        "evidence": "[验证过] anonymous multi-station historical CSV POST requests succeeded",
        "access": "anonymous official download service with session cookie",
        "format": "Shift-JIS CSV with values, QC and homogeneity number",
        "independence": "instrument records; JMA and exchanged operational analyses can ingest them",
        "window": "hourly samples are the preceding 10-minute mean wind",
        "url": "https://ds.data.jma.go.jp/risk/obsdl/",
    },
    {
        "source_id": "CMA_GROUND",
        "source": "CMA national ground observation archive",
        "availability": "unavailable",
        "evidence": "[验证过] dataset page requires personal/unit real-name registration",
        "access": "real-name registered users",
        "format": "hourly/three-hourly/daily datasets",
        "independence": "instrument records would be independent measurements with CMA home advantage",
        "window": "dataset-dependent",
        "url": "https://m.data.cma.cn/data/cdcindex/cid/6d1b5efbdcbf9a58.html",
    },
    {
        "source_id": "CMA_RADAR",
        "source": "China coastal Doppler radar archive/products",
        "availability": "unavailable",
        "evidence": "[验证过] CMA radar product pages require real-name registration; public pages expose derived images",
        "access": "real-name registration; raw archive not anonymously downloadable",
        "format": "radar base/product files or public derived images",
        "independence": "raw radial velocity is observational; surface wind fields are inversions and grade C",
        "window": "typically 6-minute scans",
        "url": "https://k.data.cma.cn/mekb/?dataCode=J.0019.0010.S001&r=data%2Fdetail",
    },
    {
        "source_id": "KMA_ASOS",
        "source": "KMA ASOS historical observations",
        "availability": "unavailable",
        "evidence": "[据文档] official OpenAPI requires a utilization application/key",
        "access": "registration/application",
        "format": "API",
        "independence": "instrument observation",
        "window": "dataset-dependent",
        "url": "https://data.kma.go.kr/api/selectApiDetail.do?openApiNo=241",
    },
    {
        "source_id": "PAGASA_GROUND",
        "source": "PAGASA historical station observations",
        "availability": "unavailable",
        "evidence": "[据文档] official request path requires forms/supporting documents and can charge fees",
        "access": "application and fee schedule",
        "format": "requested tables/files",
        "independence": "instrument observation",
        "window": "dataset-dependent",
        "url": "https://www.pagasa.dost.gov.ph/climate/climate-data",
    },
    {
        "source_id": "TMD_GROUND",
        "source": "Thai Meteorological Department historical observations",
        "availability": "unavailable",
        "evidence": "[验证过] official service lists login/request and per-station hourly fees",
        "access": "registration, application and payment",
        "format": "download after approval/payment",
        "independence": "instrument observation",
        "window": "hourly/three-hourly",
        "url": "https://tmd.go.th/service/tmdData",
    },
    {
        "source_id": "VIETNAM_GROUND",
        "source": "Vietnam national hydrometeorological database",
        "availability": "unavailable",
        "evidence": "[据文档] official procedure requires a written request and payment where applicable",
        "access": "formal request and possible fee",
        "format": "provided on request",
        "independence": "instrument observation",
        "window": "dataset-dependent",
        "url": "https://dichvucong.gov.vn/p/home/dvc-chi-tiet-cau-hoi.html?id=15399&row_limit=1",
    },
    {
        "source_id": "OFFICIAL_REVIEWS",
        "source": "Official post-storm reviews and peer-reviewed papers",
        "availability": "partially_available",
        "evidence": "[验证过] case-specific public station/gust/radar values exist; coverage and observation operators vary",
        "access": "anonymous HTML/PDF where published",
        "format": "HTML/PDF",
        "independence": "raw quoted instrument values can be grade B; analyzed Vmax and inversion products are grade C",
        "window": "case-specific",
        "url": "https://www.cma.gov.cn/2011xwzx/zdbk/jdbktp/202307/t20230728_5678350.html",
    },
]


EXCLUSIONS = [
    {
        "source_id": "IBTRACS_AGENCY_ANALYSIS",
        "item_type": "best-track or agency Vmax",
        "case_sid": "",
        "source_url": "https://www.ncei.noaa.gov/products/international-best-track-archive",
        "reason": "Agency analysis is the estimate being evaluated and is not independent truth",
        "evidence_label": "[据文档]",
    },
    {
        "source_id": "CMA_LANDFALL_BULLETIN",
        "item_type": "analyzed center maximum wind at landfall",
        "case_sid": "",
        "source_url": "https://www.cma.gov.cn/2011xwzx/zdbk/jdbktp/202307/t20230728_5678350.html",
        "reason": "Landfall Vmax is an agency analysis; separately quoted station gusts use a different observation operator",
        "evidence_label": "[验证过]",
    },
    {
        "source_id": "CMA_RADAR_DERIVED",
        "item_type": "radar-derived surface/multisource wind field",
        "case_sid": "",
        "source_url": "https://k.data.cma.cn/mekb/?dataCode=J.0019.0010.S001&r=data%2Fdetail",
        "reason": "Derived or inverted wind field lacks a raw surface-wind observation chain",
        "evidence_label": "[据文档]",
    },
    {
        "source_id": "CWA_TYPHOON_DB_ANALYSIS",
        "item_type": "CWA best-track intensity and typhoon analysis",
        "case_sid": "",
        "source_url": "https://rdc28.cwa.gov.tw/TDB/public/",
        "reason": "Agency analysis is not an independent measurement",
        "evidence_label": "[据文档]",
    },
    {
        "source_id": "HKO_BEST_TRACK",
        "item_type": "HKO post-analysis maximum sustained wind",
        "case_sid": "",
        "source_url": "https://data.gov.hk/en-data/dataset/hk-hko-rss-tropical-cyclone-best-track-data",
        "reason": "Post-analysis wind is an agency estimate",
        "evidence_label": "[据文档]",
    },
    {
        "source_id": "JMA_BEST_TRACK",
        "item_type": "JMA best-track intensity",
        "case_sid": "",
        "source_url": "https://www.jma.go.jp/jma/jma-eng/jma-center/rsmc-hp-pub-eg/besttrack.html",
        "reason": "Best-track intensity is an agency estimate",
        "evidence_label": "[据文档]",
    },
    {
        "source_id": "PUBLIC_RADAR_RADIAL_VELOCITY",
        "item_type": "single-beam radial velocity",
        "case_sid": "",
        "source_url": "https://www.cma.gov.cn/2011xwzx/zdbk/jdbktp/202307/t20230728_5678350.html",
        "reason": "Radial velocity aloft is not a 10-m sustained surface wind and cannot be directly scored against Vmax",
        "evidence_label": "[验证过]",
    },
]


COLLECTED_SOURCE_IDS = (
    "NOAA_ISD",
    "CWA_CODIS",
    "JMA_AMEDAS",
    "HKO_TC_IMPACT",
)

ABSENT_SOURCE_STATUS = {
    "CWA_RADAR": {
        "status": "not_applicable_outside_taiwan_warning_archive",
        "reason": "The CWA warning-event archive is applicable to Taiwan warning cases; reflectivity remains supporting structure evidence",
        "searched_for_event": False,
    },
    "HKO_10MIN_LATEST": {
        "status": "historical_archive_unavailable",
        "reason": "The anonymous feed exposes the latest 10-minute value and no 2015-2024 snapshot archive",
        "searched_for_event": False,
    },
    "CMA_GROUND": {
        "status": "source_unavailable_access_barrier",
        "reason": "Official archive requires real-name registration",
        "searched_for_event": False,
    },
    "CMA_RADAR": {
        "status": "source_unavailable_access_barrier",
        "reason": "Official raw/product archive requires real-name registration",
        "searched_for_event": False,
    },
    "KMA_ASOS": {
        "status": "source_unavailable_access_barrier",
        "reason": "Official API requires an approved utilization application and key",
        "searched_for_event": False,
    },
    "PAGASA_GROUND": {
        "status": "source_unavailable_access_barrier",
        "reason": "Official historical data path requires an application and can require payment",
        "searched_for_event": False,
    },
    "TMD_GROUND": {
        "status": "source_unavailable_access_barrier",
        "reason": "Official historical data service requires login, request and payment",
        "searched_for_event": False,
    },
    "VIETNAM_GROUND": {
        "status": "source_unavailable_access_barrier",
        "reason": "Official database requires a formal request and possible payment",
        "searched_for_event": False,
    },
    "OFFICIAL_REVIEWS": {
        "status": "case_specific_non_enumerable_source",
        "reason": "Public reviews are case-specific and lack a complete machine-queryable index of raw sustained-wind observations",
        "searched_for_event": False,
    },
}


def request_bytes(
    url: str,
    *,
    data: bytes | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
    retries: int = 3,
) -> bytes:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers)
    open_request = opener.open if opener is not None else urllib.request.urlopen
    for attempt in range(retries):
        try:
            with open_request(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt + 1 == retries:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def cached_request(
    url: str,
    path: Path,
    *,
    data: bytes | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    headers: dict[str, str] | None = None,
    offline: bool = False,
    allow_404: bool = False,
    allow_http_status: Sequence[int] = (),
) -> tuple[bytes | None, str]:
    if path.exists():
        payload = path.read_bytes()
        retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(
            microsecond=0
        ).isoformat()
        return payload, retrieved
    missing = path.with_suffix(path.suffix + ".missing")
    if missing.exists():
        return None, missing.read_text(encoding="utf-8").strip()
    if offline:
        raise FileNotFoundError(f"Offline cache miss: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = request_bytes(url, data=data, opener=opener, headers=headers)
    except urllib.error.HTTPError as error:
        allowed = set(allow_http_status)
        if allow_404:
            allowed.add(404)
        if error.code in allowed:
            retrieved = utc_now_iso()
            missing.write_text(
                f"{retrieved}\nHTTP {error.code}\n", encoding="utf-8"
            )
            return None, retrieved
        raise
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return payload, datetime.fromtimestamp(path.stat().st_mtime, UTC).replace(
        microsecond=0
    ).isoformat()


def normalize_isd_history(payload: bytes) -> pd.DataFrame:
    history = pd.read_csv(
        io.BytesIO(payload), dtype={"USAF": str, "WBAN": str}, keep_default_na=False
    )
    history["USAF"] = history["USAF"].str.zfill(6)
    history["WBAN"] = history["WBAN"].str.zfill(5)
    history["LAT"] = pd.to_numeric(history["LAT"], errors="coerce")
    history["LON"] = pd.to_numeric(history["LON"], errors="coerce")
    history["ELEV(M)"] = pd.to_numeric(history["ELEV(M)"], errors="coerce")
    history["BEGIN_TIME"] = pd.to_datetime(
        history["BEGIN"].astype(str), format="%Y%m%d", errors="coerce", utc=True
    )
    history["END_TIME"] = pd.to_datetime(
        history["END"].astype(str), format="%Y%m%d", errors="coerce", utc=True
    )
    return history.dropna(subset=["LAT", "LON", "BEGIN_TIME", "END_TIME"])


def natural_earth_country_context(
    events: Sequence[Event], *, offline: bool
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    archive_path = RAW_DIR / "natural_earth" / "ne_10m_admin_0_countries.zip"
    payload, retrieved = cached_request(
        NATURAL_EARTH_COUNTRIES_URL, archive_path, offline=offline
    )
    if payload is None:
        raise RuntimeError("Natural Earth country archive unexpectedly unavailable")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        base = "ne_10m_admin_0_countries"
        required = {f"{base}.shp", f"{base}.shx", f"{base}.dbf"}
        if not required.issubset(names):
            raise RuntimeError("Natural Earth country archive has an unexpected schema")
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(f"{base}.shp")),
            shx=io.BytesIO(archive.read(f"{base}.shx")),
            dbf=io.BytesIO(archive.read(f"{base}.dbf")),
        )
        countries = [
            (shape(item.shape.__geo_interface__), item.record.as_dict())
            for item in reader.iterShapeRecords()
        ]

    context: dict[str, dict[str, Any]] = {}
    assignment_distances: list[float] = []
    taiwan_codes: dict[str, str] = {}
    for geometry, attributes in countries:
        if attributes.get("ADMIN") == "Taiwan":
            taiwan_codes = {
                field: str(attributes.get(field, ""))
                for field in ("ISO_A3", "ADM0_A3", "SOV_A3", "GU_A3")
            }
            break
    for event in events:
        point = Point(event.longitude, event.latitude)
        geometry, attributes = min(
            countries,
            key=lambda item: item[0].distance(point),
        )
        planar_distance = float(geometry.distance(point))
        codes = [
            str(attributes.get(field, ""))
            for field in ("ISO_A3", "ADM0_A3", "SOV_A3", "GU_A3")
        ]
        if "TWN" in codes:
            country_code = "TWN"
        else:
            country_code = next(
                (code for code in codes if code and code != "-99"),
                "UNK",
            )
        context[event.sid] = {
            "landfall_year": event.crossing_time.year,
            "landfall_country_code": country_code,
            "landfall_country_name": str(
                attributes.get("ADMIN") or attributes.get("NAME_EN") or "Unknown"
            ),
            "country_assignment_distance_degrees": planar_distance,
            "country_assignment_method": "nearest Natural Earth admin-0 polygon",
        }
        assignment_distances.append(planar_distance)
    audit = {
        "url": NATURAL_EARTH_COUNTRIES_URL,
        "sha256": sha256_file(archive_path),
        "retrieved_at_utc": retrieved,
        "country_polygons": len(countries),
        "taiwan_code_check": taiwan_codes,
        "events_assigned": len(context),
        "maximum_assignment_distance_degrees": max(assignment_distances),
    }
    return context, audit


def collect_isd(
    events: Sequence[Event], *, offline: bool, workers: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    history_path = RAW_DIR / "isd" / "isd-history.csv"
    payload, history_retrieved = cached_request(
        ISD_HISTORY_URL, history_path, offline=offline
    )
    if payload is None:
        raise RuntimeError("ISD station history unexpectedly unavailable")
    history = normalize_isd_history(payload)
    station_event_pairs: list[tuple[int, Event, pd.Series]] = []
    for event_index, event in enumerate(events):
        distance = haversine_km(
            event.latitude,
            event.longitude,
            history["LAT"].to_numpy(float),
            history["LON"].to_numpy(float),
        )
        active = history["BEGIN_TIME"].le(event.crossing_time) & history[
            "END_TIME"
        ].ge(event.crossing_time)
        selected = history.loc[active & (distance <= SEARCH_RADIUS_KM)]
        for _, station in selected.iterrows():
            station_event_pairs.append((event_index, event, station))

    keys: dict[tuple[str, str, int], str] = {}
    for _, event, station in station_event_pairs:
        key = (str(station["USAF"]), str(station["WBAN"]), event.crossing_time.year)
        keys[key] = f"{ISD_BASE_URL}/{key[2]}/{key[0]}{key[1]}.csv"

    results: dict[tuple[str, str, int], tuple[Path, bytes | None, str]] = {}

    def fetch_one(item: tuple[tuple[str, str, int], str]) -> tuple[Any, ...]:
        key, url = item
        path = RAW_DIR / "isd" / str(key[2]) / f"{key[0]}{key[1]}.csv"
        data, retrieved = cached_request(
            url, path, offline=offline, allow_404=True
        )
        return key, path, data, retrieved

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for key, path, data, retrieved in executor.map(fetch_one, keys.items()):
            results[key] = (path, data, retrieved)

    pairs_by_key: dict[tuple[str, str, int], list[tuple[Event, pd.Series]]] = defaultdict(list)
    for _, event, station in station_event_pairs:
        key = (str(station["USAF"]), str(station["WBAN"]), event.crossing_time.year)
        pairs_by_key[key].append((event, station))
    records: list[dict[str, Any]] = []
    for key, pairs in pairs_by_key.items():
        path, data, retrieved = results[key]
        if data is None:
            continue
        try:
            observations = pd.read_csv(io.BytesIO(data), low_memory=False)
        except pd.errors.EmptyDataError:
            continue
        raw_hash = sha256_file(path)
        for event, station in pairs:
            records.extend(
                select_isd_event_records(
                    event,
                    station,
                    observations,
                    source_url=keys[key],
                    retrieved_at=retrieved,
                    raw_sha256=raw_hash,
                )
            )
    audit = {
        "history_url": ISD_HISTORY_URL,
        "history_sha256": sha256_file(history_path),
        "history_retrieved_at_utc": history_retrieved,
        "station_event_pairs_within_250km": len(station_event_pairs),
        "unique_station_year_files_requested": len(keys),
        "station_year_files_available": sum(data is not None for _, data, _ in results.values()),
        "station_year_files_missing": sum(data is None for _, data, _ in results.values()),
        "records_retained": len(records),
        "events_with_records": len({record["SID"] for record in records}),
    }
    return records, audit


def cwa_active(station: dict[str, Any], event: Event) -> bool:
    start = pd.Timestamp(station.get("stationStartDate") or "1900-01-01", tz="UTC")
    end_text = station.get("stationEndDate")
    end = (
        pd.Timestamp(end_text, tz="UTC") + pd.Timedelta(1, unit="D")
        if end_text
        else pd.Timestamp("2262-04-11", tz="UTC")
    )
    return start <= event.crossing_time <= end


def chunks(values: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def collect_cwa(
    events: Sequence[Event], *, offline: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    station_path = RAW_DIR / "cwa" / "station_list.json"
    payload, station_retrieved = cached_request(
        CWA_STATION_LIST_URL, station_path, offline=offline
    )
    if payload is None:
        raise RuntimeError("CWA station list unexpectedly unavailable")
    station_json = json.loads(payload)
    stations: list[dict[str, Any]] = []
    for group in station_json["data"]:
        for item in group["item"]:
            station = {
                key: value for key, value in item.items() if key != "log"
            }
            station["station_type"] = cwa_station_type(
                group["stationAttribute"], station["stationID"]
            )
            if station["station_type"] != "auto_unknown":
                stations.append(station)

    records: list[dict[str, Any]] = []
    event_queries = 0
    events_applicable = 0
    for event in events:
        selected: list[dict[str, Any]] = []
        for station in stations:
            if not cwa_active(station, event):
                continue
            distance = float(
                haversine_km(
                    event.latitude,
                    event.longitude,
                    float(station["latitude"]),
                    float(station["longitude"]),
                )
            )
            if distance <= SEARCH_RADIUS_KM:
                selected.append(station)
        if not selected:
            continue
        events_applicable += 1
        local = event.crossing_time.tz_convert("Asia/Taipei")
        start = (local - pd.Timedelta(13, unit="h")).floor("h").strftime("%Y-%m-%dT%H:%M:%S")
        end = (local + pd.Timedelta(13, unit="h")).ceil("h").strftime("%Y-%m-%dT%H:%M:%S")
        for station_type in sorted({item["station_type"] for item in selected}):
            typed = [item for item in selected if item["station_type"] == station_type]
            for chunk_index, station_chunk in enumerate(chunks(typed, 150)):
                station_ids = ",".join(item["stationID"] for item in station_chunk)
                form = urllib.parse.urlencode(
                    {
                        "type": "report_date",
                        "stn_type": station_type,
                        "stn_ID": station_ids,
                        "start": start,
                        "end": end,
                        "more": "",
                    }
                ).encode()
                path = (
                    RAW_DIR
                    / "cwa"
                    / "events"
                    / event.sid
                    / f"{station_type}_{chunk_index:02d}.json"
                )
                data, retrieved = cached_request(
                    CWA_STATION_API_URL,
                    path,
                    data=form,
                    offline=offline,
                )
                event_queries += 1
                if data is None:
                    continue
                response = json.loads(data)
                if response.get("code") != 200:
                    raise RuntimeError(
                        f"CWA query failed for {event.sid}/{station_type}: {response.get('message')}"
                    )
                station_by_id = {item["stationID"]: item for item in station_chunk}
                for station_data in response.get("data") or []:
                    station = station_by_id.get(str(station_data.get("StationID")))
                    if station is None:
                        continue
                    records.extend(
                        select_cwa_station_records(
                            event,
                            station,
                            station_data.get("dts") or [],
                            station_type=station_type,
                            source_url=CWA_STATION_API_URL,
                            retrieved_at=retrieved,
                            raw_sha256=sha256_file(path),
                        )
                    )
    audit = {
        "station_list_url": CWA_STATION_LIST_URL,
        "station_list_sha256": sha256_file(station_path),
        "station_list_retrieved_at_utc": station_retrieved,
        "stations_without_ephemeral_log_token": len(stations),
        "events_with_station_within_250km": events_applicable,
        "multi_station_requests": event_queries,
        "records_retained": len(records),
        "events_with_records": len({record["SID"] for record in records}),
    }
    return records, audit


def canonical_storm_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def cwa_tdb_opener(offline: bool) -> urllib.request.OpenerDirector | None:
    if offline:
        return None
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request_bytes(CWA_TDB_WARNING_LIST_URL, opener=opener)
    request_bytes(CWA_TDB_WIND_URL, opener=opener)
    return opener


def cwa_tdb_headers(referer: str) -> dict[str, str]:
    return {
        "Referer": referer,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def cwa_tdb_station_registry(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stations: dict[str, dict[str, Any]] = {}
    for station_class, areas in payload.items():
        for area, districts in (areas or {}).items():
            for district, items in (districts or {}).items():
                for item in items or []:
                    if len(item) < 4:
                        continue
                    station_id = str(item[0])
                    stations[station_id] = {
                        "station_id": station_id,
                        "station_name": str(item[1]).strip(),
                        "station_lon": float(item[2]),
                        "station_lat": float(item[3]),
                        "station_class": station_class,
                        "area": area,
                        "district": district,
                    }
    return stations


def cwa_tdb_product_items(product: dict[str, Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for group in product.get("subdatatype") or []:
        for item in group.get("sets") or []:
            if isinstance(item, list) and len(item) >= 2:
                items.append((str(item[0]), str(item[1])))
    return items


def cwa_tdb_declared_products(detail_html: bytes) -> set[str]:
    text = detail_html.decode("utf-8", "replace")
    match = re.search(
        r"global\s*=\s*window\.global\s*\|\|\s*\{\};\s*d\s*=\s*(\{.*?\});for\(var k in d\)",
        text,
    )
    if match is None:
        raise RuntimeError("CWA TDB detail page lacks parseable product metadata")
    metadata = json.loads(match.group(1))
    return set(
        metadata.get("meta", {})
        .get("meta", {})
        .get("Group", {})
        .get("OBS", {})
        .get("datatype_e", [])
    )


def cwa_tdb_reference_files(product: dict[str, Any]) -> list[str]:
    value = product.get("refurl")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        files: list[str] = []
        for item in value:
            if isinstance(item, str):
                files.append(item)
            elif isinstance(item, list) and item:
                files.append(str(item[0]))
        return files
    return []


def cwa_tdb_support_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    columns = (
        "SID",
        "NAME",
        "crossing_time_utc",
        "typhoon_id",
        "cwa_english_name",
        "evidence_type",
        "product",
        "archive_item_count",
        "archive_start_local",
        "archive_end_local",
        "nearest_item_time_local",
        "nearest_item_time_utc",
        "nearest_item_offset_hours",
        "nearest_item_file",
        "station_id",
        "station_name",
        "station_lat",
        "station_lon",
        "distance_to_landfall_km",
        "observed_wind_ms",
        "averaging_window_minutes",
        "evidence_result",
        "score_effect",
        "source_url",
        "raw_sha256",
        "evidence_label",
    )
    frame = pd.DataFrame(rows)
    for column in columns:
        if column not in frame:
            frame[column] = np.nan
    return frame.loc[:, columns].sort_values(
        ["SID", "evidence_type", "product"], kind="stable"
    ).reset_index(drop=True)


def collect_cwa_tdb(
    events: Sequence[Event],
    event_context: dict[str, dict[str, Any]],
    cwa_records: Sequence[dict[str, Any]],
    *,
    offline: bool,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    applicable = [
        event
        for event in events
        if event_context[event.sid]["landfall_country_code"] == "TWN"
    ]
    opener = cwa_tdb_opener(offline)
    support_rows: list[dict[str, Any]] = []
    crosscheck_rows: list[dict[str, Any]] = []
    registry_matches: dict[str, dict[str, Any]] = {}

    for year in sorted({event.crossing_time.year for event in applicable}):
        path = RAW_DIR / "cwa_tdb" / "warning_lists" / f"{year}.json"
        form = urllib.parse.urlencode({"year": year}).encode()
        payload, _ = cached_request(
            f"{CWA_TDB_WARNING_LIST_URL}get_warning_typhoon",
            path,
            data=form,
            opener=opener,
            headers=cwa_tdb_headers(CWA_TDB_WARNING_LIST_URL),
            offline=offline,
        )
        if payload is None:
            raise RuntimeError(f"CWA TDB warning list unavailable for {year}")
        rows = json.loads(payload.decode("utf-8-sig"))
        by_name = {
            canonical_storm_name(item["eng_name"]): item for item in rows
        }
        for event in applicable:
            if event.crossing_time.year != year:
                continue
            match = by_name.get(canonical_storm_name(event.name))
            if match is None:
                raise RuntimeError(
                    f"CWA TDB warning registry has no match for {event.sid}/{event.name}"
                )
            registry_matches[event.sid] = match

    station_registry_path = RAW_DIR / "cwa_tdb" / "station_registry.json"
    station_form = urllib.parse.urlencode(
        {"station_cwa": "cwa", "station_autoprec_wind": "autoprec"}
    ).encode()
    station_payload, _ = cached_request(
        f"{CWA_TDB_WIND_URL}get_station_data/",
        station_registry_path,
        data=station_form,
        opener=opener,
        headers=cwa_tdb_headers(CWA_TDB_WIND_URL),
        offline=offline,
    )
    if station_payload is None:
        raise RuntimeError("CWA TDB station registry unavailable")
    station_json = json.loads(station_payload.decode("utf-8-sig"))
    station_lookup = cwa_tdb_station_registry(station_json)

    codis = pd.DataFrame(cwa_records)

    for event in applicable:
        match = registry_matches[event.sid]
        year = event.crossing_time.year
        english_name = str(match["eng_name"]).strip()
        typhoon_id = str(match["id"])
        detail_url = (
            f"{CWA_TDB_DETAIL_URL}?"
            + urllib.parse.urlencode({"typhoon_id": typhoon_id})
        )
        detail_path = RAW_DIR / "cwa_tdb" / event.sid / "detail.html"
        detail_payload, _ = cached_request(
            detail_url,
            detail_path,
            opener=opener,
            offline=offline,
        )
        if detail_payload is None:
            raise RuntimeError(f"CWA TDB detail page unavailable for {event.sid}")
        declared_products = cwa_tdb_declared_products(detail_payload)
        if not offline:
            request_bytes(detail_url, opener=opener)

        support_rows.append(
            {
                "SID": event.sid,
                "NAME": event.name,
                "crossing_time_utc": event.crossing_time.isoformat(),
                "typhoon_id": typhoon_id,
                "cwa_english_name": english_name,
                "evidence_type": "warning_registry_match",
                "product": "registry",
                "archive_item_count": 1,
                "evidence_result": "exact normalized year/name match discovered automatically",
                "score_effect": "provenance only",
                "source_url": detail_url,
                "raw_sha256": sha256_file(detail_path),
                "evidence_label": "[验证过]",
            }
        )

        radar_timed_items: list[tuple[pd.Timestamp, str]] = []
        for product_name in CWA_TDB_PRODUCTS:
            if product_name not in declared_products:
                support_rows.append(
                    {
                        "SID": event.sid,
                        "NAME": event.name,
                        "crossing_time_utc": event.crossing_time.isoformat(),
                        "typhoon_id": typhoon_id,
                        "cwa_english_name": english_name,
                        "evidence_type": "product_archive",
                        "product": product_name,
                        "archive_item_count": 0,
                        "evidence_result": "product absent from the event page's declared product metadata",
                        "score_effect": "documented product-level missingness",
                        "source_url": detail_url,
                        "raw_sha256": sha256_file(detail_path),
                        "evidence_label": "[验证过]",
                    }
                )
                continue
            product_path = (
                RAW_DIR
                / "cwa_tdb"
                / event.sid
                / "products"
                / f"{product_name}.json"
            )
            form = urllib.parse.urlencode(
                {"year": year, "name": english_name, "product": product_name}
            ).encode()
            product_payload, _ = cached_request(
                CWA_TDB_PRODUCT_URL,
                product_path,
                data=form,
                opener=opener,
                headers=cwa_tdb_headers(detail_url),
                offline=offline,
                allow_http_status=(400, 404),
            )
            if product_payload is None:
                missing_path = product_path.with_suffix(
                    product_path.suffix + ".missing"
                )
                support_rows.append(
                    {
                        "SID": event.sid,
                        "NAME": event.name,
                        "crossing_time_utc": event.crossing_time.isoformat(),
                        "typhoon_id": typhoon_id,
                        "cwa_english_name": english_name,
                        "evidence_type": "product_archive",
                        "product": product_name,
                        "archive_item_count": 0,
                        "evidence_result": "event metadata declared the product; endpoint returned HTTP 400/404",
                        "score_effect": "documented product-level missingness",
                        "source_url": CWA_TDB_PRODUCT_URL,
                        "raw_sha256": sha256_file(missing_path),
                        "evidence_label": "[验证过]",
                    }
                )
                continue
            product_json = json.loads(product_payload.decode("utf-8-sig"))
            product = product_json.get(product_name) or {}
            items = cwa_tdb_product_items(product)
            reference_files = cwa_tdb_reference_files(product)
            local_times: list[pd.Timestamp] = []
            timed_items: list[tuple[pd.Timestamp, str]] = []
            for label, filename in items:
                if not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?", label
                ):
                    continue
                parsed = pd.to_datetime(label, errors="coerce")
                if pd.isna(parsed):
                    continue
                local_time = pd.Timestamp(parsed).tz_localize("Asia/Taipei")
                local_times.append(local_time)
                timed_items.append((local_time, filename))
            if product_name == "Radar":
                radar_timed_items = list(timed_items)

            nearest_local: pd.Timestamp | None = None
            nearest_file = ""
            nearest_utc: pd.Timestamp | None = None
            nearest_offset = np.nan
            nearest_hash = ""
            source_url = CWA_TDB_PRODUCT_URL
            if timed_items:
                crossing_local = event.crossing_time.tz_convert("Asia/Taipei")
                nearest_local, nearest_file = min(
                    timed_items,
                    key=lambda item: abs((item[0] - crossing_local).total_seconds()),
                )
                nearest_utc = nearest_local.tz_convert("UTC")
                nearest_offset = (
                    nearest_utc - event.crossing_time
                ).total_seconds() / 3600.0
                image_path_value = (
                    f"{year}/{english_name}/OBS/{product_name}/{nearest_file}"
                )
                source_url = (
                    f"{CWA_TDB_IMAGE_URL}?"
                    + urllib.parse.urlencode({"image": image_path_value})
                )
                local_image_path = (
                    RAW_DIR
                    / "cwa_tdb"
                    / event.sid
                    / "nearest"
                    / nearest_file
                )
                image_payload, _ = cached_request(
                    source_url,
                    local_image_path,
                    opener=opener,
                    headers={"Referer": detail_url},
                    offline=offline,
                )
                if image_payload:
                    nearest_hash = sha256_file(local_image_path)
            elif product_name == "RainInfo" and reference_files:
                nearest_file = reference_files[0]
                pdf_path_value = (
                    f"{year}/{english_name}/OBS/{product_name}/{nearest_file}"
                )
                source_url = (
                    f"{CWA_TDB_PDF_URL}?"
                    + urllib.parse.urlencode({"pdf": pdf_path_value})
                )
                local_pdf_path = (
                    RAW_DIR
                    / "cwa_tdb"
                    / event.sid
                    / "reports"
                    / nearest_file
                )
                pdf_payload, _ = cached_request(
                    source_url,
                    local_pdf_path,
                    opener=opener,
                    headers={"Referer": detail_url},
                    offline=offline,
                )
                if pdf_payload and pdf_payload.startswith(b"%PDF"):
                    nearest_hash = sha256_file(local_pdf_path)

            item_count = len(items) or len(reference_files)
            result = {
                "Radar": "6-minute composite reflectivity archive enumerated; nearest crossing scan cached as structural support",
                "WeatherInfo": "official-station weather charts enumerated; structured station endpoint used for numeric cross-check",
                "GustHis": "passage maximum-average and gust chart files enumerated; values duplicate station instruments",
                "RainInfo": "official event review PDF enumerated and cached where supplied",
            }[product_name]
            score_effect = (
                "grade C as a direct wind operator; may support eye-wall location"
                if product_name == "Radar"
                else "support only; duplicate or documentary evidence"
            )
            support_rows.append(
                {
                    "SID": event.sid,
                    "NAME": event.name,
                    "crossing_time_utc": event.crossing_time.isoformat(),
                    "typhoon_id": typhoon_id,
                    "cwa_english_name": english_name,
                    "evidence_type": "product_archive",
                    "product": product_name,
                    "archive_item_count": item_count,
                    "archive_start_local": (
                        min(local_times).isoformat() if local_times else ""
                    ),
                    "archive_end_local": (
                        max(local_times).isoformat() if local_times else ""
                    ),
                    "nearest_item_time_local": (
                        nearest_local.isoformat() if nearest_local is not None else ""
                    ),
                    "nearest_item_time_utc": (
                        nearest_utc.isoformat() if nearest_utc is not None else ""
                    ),
                    "nearest_item_offset_hours": nearest_offset,
                    "nearest_item_file": nearest_file,
                    "evidence_result": result,
                    "score_effect": score_effect,
                    "source_url": source_url,
                    "raw_sha256": nearest_hash or sha256_file(product_path),
                    "evidence_label": "[验证过]",
                }
            )

        for measure_type, registry_key, value_key, averaging_window, codis_kind in (
            (
                "CWA",
                "cwa",
                "WSMax",
                10.0,
                "ten_minute_sustained_maximum",
            ),
            (
                "AUTOPRECP_WIND",
                "autoprec_wind",
                "WS",
                np.nan,
                "reported_mean",
            ),
        ):
            station_ids = sorted(
                station_id
                for station_id, station in station_lookup.items()
                if station["station_class"] == registry_key
            )
            station_batches = list(chunks(station_ids, 100))
            wind_rows: list[dict[str, Any]] = []
            wind_hashes: list[str] = []
            for batch_index, station_batch in enumerate(station_batches):
                form_values: list[tuple[str, Any]] = [
                    ("search_type", "HR"),
                    ("wind_type[]", "WSMax"),
                    ("WSMax_value", "0"),
                    ("WSMax_value_ms", "0"),
                    ("radio_typhoon_year", "typhoon_year"),
                    ("typhoon_year", str(year)),
                    ("typhoon_name", f"{year}{english_name}"),
                    ("measure_type", measure_type),
                ]
                form_values.extend(
                    ("stno[]", station_id) for station_id in station_batch
                )
                filename = (
                    f"{measure_type.lower()}.json"
                    if len(station_batches) == 1
                    else f"{measure_type.lower()}_{batch_index:02d}.json"
                )
                wind_path = (
                    RAW_DIR
                    / "cwa_tdb"
                    / event.sid
                    / "station"
                    / filename
                )
                wind_payload, _ = cached_request(
                    f"{CWA_TDB_WIND_URL}search",
                    wind_path,
                    data=urllib.parse.urlencode(form_values).encode(),
                    opener=opener,
                    headers=cwa_tdb_headers(CWA_TDB_WIND_URL),
                    offline=offline,
                )
                if wind_payload is None:
                    raise RuntimeError(
                        f"CWA TDB station time series unavailable for {event.sid}/{measure_type}/{batch_index}"
                    )
                try:
                    batch_rows = json.loads(wind_payload.decode("utf-8-sig"))
                except json.JSONDecodeError as error:
                    preview = wind_payload[:240].decode("utf-8", "replace")
                    raise RuntimeError(
                        f"CWA TDB returned non-JSON for {event.sid}/{measure_type}/{batch_index}: {preview}"
                    ) from error
                batch_hash = sha256_file(wind_path)
                wind_hashes.append(batch_hash)
                for item in batch_rows:
                    item["_raw_sha256"] = batch_hash
                wind_rows.extend(batch_rows)
            candidates: list[dict[str, Any]] = []
            for item in wind_rows:
                station = station_lookup.get(str(item.get("stno")))
                if station is None or not item.get("ObsTime"):
                    continue
                local_time = pd.Timestamp(item["ObsTime"]).tz_localize(
                    "Asia/Taipei"
                )
                utc_time = local_time.tz_convert("UTC")
                offset = (utc_time - event.crossing_time).total_seconds() / 3600.0
                if abs(offset) > SEARCH_WINDOW_HOURS:
                    continue
                distance = float(
                    haversine_km(
                        event.latitude,
                        event.longitude,
                        station["station_lat"],
                        station["station_lon"],
                    )
                )
                if distance > SEARCH_RADIUS_KM:
                    continue
                value = pd.to_numeric(item.get(value_key), errors="coerce")
                if not np.isfinite(value):
                    continue
                candidates.append(
                    {
                        **station,
                        "observation_time_local": local_time.isoformat(),
                        "observation_time_utc": utc_time.isoformat(),
                        "time_offset_hours": offset,
                        "distance_to_landfall_km": distance,
                        "tdb_wind_ms": float(value),
                        "raw_sha256": str(item["_raw_sha256"]),
                    }
                )

            maxima: dict[str, dict[str, Any]] = {}
            for item in candidates:
                station_id = item["station_id"]
                if station_id not in maxima or item["tdb_wind_ms"] > maxima[station_id]["tdb_wind_ms"]:
                    maxima[station_id] = item
            matched_count = 0
            for item in maxima.values():
                codis_value = np.nan
                codis_time = ""
                if not codis.empty:
                    selected_codis = codis.loc[
                        codis["SID"].eq(event.sid)
                        & codis["station_id"].astype(str).eq(item["station_id"])
                        & codis["wind_kind"].eq(codis_kind)
                    ]
                    if len(selected_codis):
                        codis_row = selected_codis.iloc[
                            pd.to_numeric(
                                selected_codis["observed_wind_ms"], errors="coerce"
                            ).argmax()
                        ]
                        codis_value = float(codis_row["observed_wind_ms"])
                        codis_time = str(codis_row["observation_time_utc"])
                        matched_count += 1
                crosscheck_rows.append(
                    {
                        "SID": event.sid,
                        "NAME": event.name,
                        "typhoon_id": typhoon_id,
                        "measure_type": measure_type,
                        "station_id": item["station_id"],
                        "station_name": item["station_name"],
                        "station_lat": item["station_lat"],
                        "station_lon": item["station_lon"],
                        "distance_to_landfall_km": item["distance_to_landfall_km"],
                        "observation_time_local": item["observation_time_local"],
                        "observation_time_utc": item["observation_time_utc"],
                        "time_offset_hours": item["time_offset_hours"],
                        "tdb_wind_ms": item["tdb_wind_ms"],
                        "averaging_window_minutes": averaging_window,
                        "codis_wind_ms": codis_value,
                        "codis_observation_time_utc": codis_time,
                        "tdb_minus_codis_ms": (
                            item["tdb_wind_ms"] - codis_value
                            if np.isfinite(codis_value)
                            else np.nan
                        ),
                        "comparison_status": (
                            (
                                "same_underlying_station_crosscheck_native_10min"
                                if measure_type == "CWA"
                                else "same_underlying_station_crosscheck_window_undocumented"
                            )
                            if np.isfinite(codis_value)
                            else "tdb_station_not_in_codis_retained_set"
                        ),
                        "source_url": f"{CWA_TDB_WIND_URL}search",
                        "raw_sha256": item["raw_sha256"],
                        "evidence_label": "[验证过]",
                    }
                )
            support_rows.append(
                {
                    "SID": event.sid,
                    "NAME": event.name,
                    "crossing_time_utc": event.crossing_time.isoformat(),
                    "typhoon_id": typhoon_id,
                    "cwa_english_name": english_name,
                    "evidence_type": "station_time_series_crosscheck",
                    "product": measure_type,
                    "archive_item_count": len(wind_rows),
                    "evidence_result": (
                        f"{len(candidates)} hourly rows within 250 km/+/-12 h; "
                        f"{len(maxima)} station maxima; {matched_count} matched CODiS retained maxima"
                    ),
                    "score_effect": "duplicate instrument archive; retained as cross-check and not duplicated in landfall_truth.csv",
                    "source_url": f"{CWA_TDB_WIND_URL}search",
                    "raw_sha256": sha256_bytes(
                        "".join(wind_hashes).encode("ascii")
                    ),
                    "evidence_label": "[验证过]",
                }
            )

        event_official_rows = [
            row
            for row in crosscheck_rows
            if row["SID"] == event.sid
            and row["measure_type"] == "CWA"
            and abs(float(row["time_offset_hours"])) <= 6.0
        ]
        if event_official_rows and radar_timed_items:
            peak = max(event_official_rows, key=lambda row: row["tdb_wind_ms"])
            peak_local = pd.Timestamp(peak["observation_time_local"])
            scan_local, scan_file = min(
                radar_timed_items,
                key=lambda item: abs((item[0] - peak_local).total_seconds()),
            )
            scan_utc = scan_local.tz_convert("UTC")
            scan_offset = (
                scan_utc - event.crossing_time
            ).total_seconds() / 3600.0
            radar_path_value = (
                f"{year}/{english_name}/OBS/Radar/{scan_file}"
            )
            radar_url = (
                f"{CWA_TDB_IMAGE_URL}?"
                + urllib.parse.urlencode({"image": radar_path_value})
            )
            peak_scan_path = (
                RAW_DIR
                / "cwa_tdb"
                / event.sid
                / "peak_station"
                / f"{peak['station_id']}_{scan_file}"
            )
            radar_payload, _ = cached_request(
                radar_url,
                peak_scan_path,
                opener=opener,
                headers={"Referer": detail_url},
                offline=offline,
            )
            if radar_payload:
                support_rows.append(
                    {
                        "SID": event.sid,
                        "NAME": event.name,
                        "crossing_time_utc": event.crossing_time.isoformat(),
                        "typhoon_id": typhoon_id,
                        "cwa_english_name": english_name,
                        "evidence_type": "official_station_peak_radar_pair",
                        "product": "Radar+WSMax",
                        "archive_item_count": 1,
                        "nearest_item_time_local": scan_local.isoformat(),
                        "nearest_item_time_utc": scan_utc.isoformat(),
                        "nearest_item_offset_hours": scan_offset,
                        "nearest_item_file": scan_file,
                        "station_id": peak["station_id"],
                        "station_name": peak["station_name"],
                        "station_lat": peak["station_lat"],
                        "station_lon": peak["station_lon"],
                        "distance_to_landfall_km": peak[
                            "distance_to_landfall_km"
                        ],
                        "observed_wind_ms": peak["tdb_wind_ms"],
                        "averaging_window_minutes": 10.0,
                        "evidence_result": "strongest official-station native 10-minute wind within +/-6 h paired with the nearest composite-reflectivity scan",
                        "score_effect": "A-grade candidate only; eye-wall co-location, exposure and instrument integrity require explicit review",
                        "source_url": radar_url,
                        "raw_sha256": sha256_file(peak_scan_path),
                        "evidence_label": "[验证过]",
                    }
                )

    support = cwa_tdb_support_frame(support_rows)
    crosscheck = pd.DataFrame(crosscheck_rows).sort_values(
        ["SID", "measure_type", "station_id"], kind="stable"
    ).reset_index(drop=True)
    audit = {
        "applicable_frozen_taiwan_events": len(applicable),
        "warning_registry_matches": len(registry_matches),
        "products_requested_per_event": list(CWA_TDB_PRODUCTS),
        "support_rows": len(support),
        "station_crosscheck_rows": len(crosscheck),
        "station_crosscheck_matches": int(
            crosscheck["codis_wind_ms"].notna().sum()
        ),
        "events_with_radar_metadata": int(
            support.loc[support["product"].eq("Radar"), "SID"].nunique()
        ),
        "events_with_official_pdf": int(
            support.loc[
                support["product"].eq("RainInfo")
                & support["nearest_item_file"].astype(str).ne(""),
                "SID",
            ].nunique()
        ),
    }
    return support, crosscheck, audit


def apply_cwa_tdb_source_status(
    source_status: pd.DataFrame,
    support: pd.DataFrame,
    event_context: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    result = source_status.copy()
    matched_events = set(
        support.loc[
            support["evidence_type"].eq("warning_registry_match"), "SID"
        ].astype(str)
    )
    review_events = set(
        support.loc[
            support["product"].eq("RainInfo")
            & support["nearest_item_file"].fillna("").astype(str).ne(""),
            "SID",
        ].astype(str)
    )
    for index, row in result.iterrows():
        sid = str(row["SID"])
        source_id = str(row["source_id"])
        if source_id == "CWA_RADAR" and sid in matched_events:
            result.loc[index, "status"] = "support_archive_screened"
            result.loc[index, "reason"] = (
                "Warning registry, station time series, radar metadata/nearest scan and event products were enumerated; reflectivity is structural support rather than a direct surface-wind truth operator"
            )
            result.loc[index, "searched_for_event"] = True
        elif (
            source_id == "CWA_RADAR"
            and event_context[sid]["landfall_country_code"] != "TWN"
        ):
            result.loc[index, "status"] = (
                "not_applicable_outside_taiwan_warning_archive"
            )
            result.loc[index, "searched_for_event"] = False
        if source_id == "OFFICIAL_REVIEWS" and sid in review_events:
            result.loc[index, "status"] = "official_cwa_event_pdf_screened"
            result.loc[index, "reason"] = (
                "The automatically enumerated CWA event review PDF was downloaded and hashed; documentary values remain support unless the frozen A-grade operator closes"
            )
            result.loc[index, "searched_for_event"] = True
    return result


def apply_cwa_eyewall_review(
    truth: pd.DataFrame,
    support: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    review = pd.read_csv(
        CWA_EYEWALL_REVIEW_PATH,
        dtype={"SID": str, "station_id": str},
    )
    if len(review) != 11 or review["SID"].nunique() != 11:
        raise RuntimeError("CWA eye-wall review must contain 11 unique Taiwan events")
    if not review["final_grade"].isin(["A", "B"]).all():
        raise RuntimeError("CWA eye-wall review contains an invalid final grade")
    review_scoreable = review["scoreable"].astype(str).str.lower().eq("true")
    if not review_scoreable.eq(review["final_grade"].eq("A")).all():
        raise RuntimeError("CWA eye-wall review scoreable flags disagree with grades")

    candidates = support.loc[
        support["evidence_type"].eq("official_station_peak_radar_pair")
    ].copy()
    candidates["station_id"] = (
        candidates["station_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    )
    candidate_columns = [
        "SID",
        "NAME",
        "station_id",
        "station_name",
        "station_lat",
        "station_lon",
        "distance_to_landfall_km",
        "observed_wind_ms",
        "averaging_window_minutes",
        "nearest_item_time_local",
        "nearest_item_time_utc",
        "nearest_item_offset_hours",
        "nearest_item_file",
        "source_url",
        "raw_sha256",
    ]
    enriched = review.merge(
        candidates[candidate_columns],
        on=["SID", "station_id"],
        how="left",
        validate="one_to_one",
    )
    if enriched["raw_sha256"].isna().any():
        raise RuntimeError("CWA eye-wall review does not match every radar candidate")

    revised = truth.copy()
    revised_station_ids = (
        revised["station_id"].astype(str).str.replace(r"\.0$", "", regex=True)
    )
    for row in enriched.itertuples(index=False):
        mask = (
            revised["SID"].astype(str).eq(row.SID)
            & revised["source_id"].eq("CWA_CODIS")
            & revised_station_ids.eq(row.station_id)
            & revised["wind_kind"].eq("ten_minute_sustained_maximum")
        )
        if int(mask.sum()) != 1:
            raise RuntimeError(
                f"CWA eye-wall review expected one truth row for {row.SID}/{row.station_id}; found {int(mask.sum())}"
            )
        observed = float(revised.loc[mask, "observed_wind_ms"].iloc[0])
        if not np.isclose(observed, float(row.observed_wind_ms), atol=0.05):
            raise RuntimeError(
                f"CWA eye-wall review wind mismatch for {row.SID}: {observed} vs {row.observed_wind_ms}"
            )
        if not bool(revised.loc[mask, "quality_passed"].iloc[0]):
            raise RuntimeError(f"Reviewed CWA candidate failed source QC: {row.SID}")
        revised.loc[mask, "grade"] = row.final_grade
        revised.loc[mask, "scoreable"] = row.final_grade == "A"
        revised.loc[mask, "grade_reason"] = row.review_reason
        revised.loc[mask, "max_wind_region_evidence"] = (
            f"{row.eyewall_review}; {row.review_method}"
        )
        revised.loc[mask, "max_wind_region_evidence_url"] = row.source_url
        revised.loc[mask, "evidence_label"] = row.evidence_label

        support_mask = (
            support["SID"].astype(str).eq(row.SID)
            & support["evidence_type"].eq("official_station_peak_radar_pair")
        )
        support.loc[support_mask, "score_effect"] = (
            f"manual frozen-criterion review: grade {row.final_grade}; {row.review_reason}"
        )
        support.loc[support_mask, "evidence_label"] = row.evidence_label
    return revised, support, enriched


def jma_opener(offline: bool) -> urllib.request.OpenerDirector | None:
    if offline:
        return None
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    cache_buster = int(time.time())
    request_bytes(f"{JMA_DOWNLOAD_ROOT}?audit={cache_buster}", opener=opener)
    return opener


def collect_jma(
    events: Sequence[Event], *, offline: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index_path = RAW_DIR / "jma" / "prefecture00.html"
    index_payload, index_retrieved = cached_request(
        JMA_PREFECTURE_INDEX_URL, index_path, offline=offline
    )
    if index_payload is None:
        raise RuntimeError("JMA prefecture index unexpectedly unavailable")
    prefectures = sorted(
        {
            int(value)
            for value in re.findall(
                rb"prefecture\.php\?prec_no=([0-9]+)", index_payload
            )
        }
    )
    stations_by_id: dict[str, dict[str, Any]] = {}
    for prefecture in prefectures:
        form = urllib.parse.urlencode({"pd": str(prefecture)}).encode()
        path = RAW_DIR / "jma" / "stations" / f"prefecture_{prefecture:02d}.html"
        payload, _ = cached_request(
            JMA_STATION_URL, path, data=form, offline=offline
        )
        if payload is None:
            continue
        for station in parse_jma_station_html(payload.decode("utf-8", errors="replace")):
            if station["wind_available"]:
                stations_by_id[station["station_id"]] = station
    stations = list(stations_by_id.values())
    opener = jma_opener(offline)
    records: list[dict[str, Any]] = []
    request_count = 0
    events_applicable = 0
    for event in events:
        if not stations:
            continue
        distance = haversine_km(
            event.latitude,
            event.longitude,
            np.asarray([item["latitude"] for item in stations]),
            np.asarray([item["longitude"] for item in stations]),
        )
        selected = [
            station
            for station, item_distance in zip(stations, distance, strict=True)
            if item_distance <= SEARCH_RADIUS_KM
        ]
        if not selected:
            continue
        events_applicable += 1
        local_start = (
            event.crossing_time.tz_convert("Asia/Tokyo")
            - pd.Timedelta(12, unit="h")
        ).date()
        local_end = (
            event.crossing_time.tz_convert("Asia/Tokyo")
            + pd.Timedelta(12, unit="h")
        ).date()
        for chunk_index, station_chunk in enumerate(chunks(selected, 80)):
            ids = [item["station_id"] for item in station_chunk]
            form_values = {
                "stationNumList": json.dumps(ids, ensure_ascii=False),
                "aggrgPeriod": "9",
                "elementNumList": '[["301",""]]',
                "interAnnualType": "1",
                "interAnnualFlag": "1",
                "ymdList": json.dumps(
                    [
                        str(local_start.year),
                        str(local_end.year),
                        str(local_start.month),
                        str(local_end.month),
                        str(local_start.day),
                        str(local_end.day),
                    ]
                ),
                "optionNumList": "[]",
                "downloadFlag": "true",
                "rmkFlag": "1",
                "disconnectFlag": "1",
                "youbiFlag": "0",
                "fukenFlag": "0",
                "kijiFlag": "0",
                "huukouFlag": "0",
                "csvFlag": "1",
                "jikantaiFlag": "0",
                "jikantaiList": "[1,24]",
                "ymdLiteral": "1",
            }
            form = urllib.parse.urlencode(form_values).encode()
            path = (
                RAW_DIR
                / "jma"
                / "events"
                / event.sid
                / f"hourly_{chunk_index:02d}.csv"
            )
            payload, retrieved = cached_request(
                JMA_DOWNLOAD_URL,
                path,
                data=form,
                opener=opener,
                offline=offline,
            )
            request_count += 1
            if payload is None:
                continue
            observations = parse_jma_hourly_csv(payload, station_chunk)
            if observations.empty:
                continue
            for station in station_chunk:
                records.extend(
                    select_jma_station_records(
                        event,
                        station,
                        observations,
                        source_url=JMA_DOWNLOAD_URL,
                        retrieved_at=retrieved,
                        raw_sha256=sha256_file(path),
                    )
                )
    audit = {
        "prefecture_index_url": JMA_PREFECTURE_INDEX_URL,
        "prefecture_index_sha256": sha256_file(index_path),
        "prefecture_index_retrieved_at_utc": index_retrieved,
        "prefectures_scanned": len(prefectures),
        "wind_stations_discovered": len(stations),
        "events_with_station_within_250km": events_applicable,
        "multi_station_requests": request_count,
        "records_retained": len(records),
        "events_with_records": len({record["SID"] for record in records}),
    }
    return records, audit


def collect_hko(
    events: Sequence[Event], *, offline: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workbook_path = RAW_DIR / "hko" / "TC_Impact_Data_HKO.xlsx"
    _, workbook_retrieved = cached_request(
        HKO_IMPACT_URL, workbook_path, offline=offline
    )
    station_path = RAW_DIR / "hko" / "station_metadata.html"
    station_payload, station_retrieved = cached_request(
        HKO_STATION_URL, station_path, offline=offline
    )
    if station_payload is None:
        raise RuntimeError("HKO station page unexpectedly unavailable")
    station_tables = pd.read_html(io.BytesIO(station_payload))
    records = hko_impact_records(
        events,
        workbook_path,
        station_tables,
        source_url=HKO_IMPACT_URL,
        retrieved_at=workbook_retrieved,
        raw_sha256=sha256_file(workbook_path),
    )
    audit = {
        "impact_url": HKO_IMPACT_URL,
        "impact_sha256": sha256_file(workbook_path),
        "impact_retrieved_at_utc": workbook_retrieved,
        "station_metadata_url": HKO_STATION_URL,
        "station_metadata_sha256": sha256_file(station_path),
        "station_metadata_retrieved_at_utc": station_retrieved,
        "records_retained": len(records),
        "events_with_records": len({record["SID"] for record in records}),
    }
    return records, audit


def sensitivity_coverage(truth: pd.DataFrame) -> list[dict[str, Any]]:
    timed = truth.copy()
    timed["distance_to_landfall_km"] = pd.to_numeric(
        timed["distance_to_landfall_km"], errors="coerce"
    )
    timed["time_offset_hours"] = pd.to_numeric(
        timed["time_offset_hours"], errors="coerce"
    )
    rows: list[dict[str, Any]] = []
    for radius in (50, 100, 150, 250):
        for hours in (3, 6, 12):
            selected = timed.loc[
                timed["distance_to_landfall_km"].le(radius)
                & timed["time_offset_hours"].abs().le(hours)
            ]
            rows.append(
                {
                    "radius_km": radius,
                    "window_hours": hours,
                    "grade_a_events": int(
                        selected.loc[selected["grade"] == "A", "SID"].nunique()
                    ),
                    "grade_a_or_b_events": int(selected["SID"].nunique()),
                }
            )
    return rows


def source_counts(truth: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_id, subset in truth.groupby("source_id", sort=True):
        rows.append(
            {
                "source_id": source_id,
                "records": len(subset),
                "events": subset["SID"].nunique(),
                "grade_a_records": int(subset["grade"].eq("A").sum()),
                "grade_b_records": int(subset["grade"].eq("B").sum()),
            }
        )
    return rows


def grouped_coverage(
    coverage: pd.DataFrame, columns: Sequence[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for keys, subset in coverage.groupby(list(columns), dropna=False, sort=True):
        values = keys if isinstance(keys, tuple) else (keys,)
        row = {column: value for column, value in zip(columns, values, strict=True)}
        row.update(
            {
                "events": len(subset),
                "grade_a_events": int(subset["grade_a_event"].sum()),
                "grade_a_or_b_events": int(subset["grade_a_or_b_event"].sum()),
                "grade_a_fraction": float(subset["grade_a_event"].mean()),
                "grade_a_or_b_fraction": float(
                    subset["grade_a_or_b_event"].mean()
                ),
            }
        )
        rows.append(row)
    return rows


def write_report(
    truth: pd.DataFrame,
    coverage: pd.DataFrame,
    error_table: pd.DataFrame,
    error_correlation_intervals: pd.DataFrame,
    support: pd.DataFrame,
    crosscheck: pd.DataFrame,
    audit: dict[str, Any],
) -> None:
    grade_a_events = int(coverage["grade_a_event"].sum())
    observed_events = int(coverage["grade_a_or_b_event"].sum())
    source_rows = source_counts(truth)
    tdb_events = int(
        support.loc[
            support["evidence_type"].eq("warning_registry_match"), "SID"
        ].nunique()
    )
    radar_events = int(
        support.loc[support["product"].eq("Radar"), "SID"].nunique()
    )
    report_events = int(
        support.loc[
            support["product"].eq("RainInfo")
            & support["nearest_item_file"].fillna("").astype(str).ne(""),
            "SID",
        ].nunique()
    )
    crosscheck_matches = int(crosscheck["codis_wind_ms"].notna().sum())
    source_table = "\n".join(
        f"|{row['source_id']}|{row['records']}|{row['events']}|{row['grade_a_records']}|{row['grade_b_records']}|"
        for row in source_rows
    )
    access_table = "\n".join(
        f"|{item['source']}|{item['availability']}|{item['evidence']}|{item['access']}|{item['format']}|"
        for item in SOURCE_AUDIT
    )
    country_table = "\n".join(
        "|{code}|{name}|{events}|{a}|{ab}|".format(
            code=row["landfall_country_code"],
            name=row["landfall_country_name"],
            events=row["events"],
            a=row["grade_a_events"],
            ab=row["grade_a_or_b_events"],
        )
        for row in grouped_coverage(
            coverage,
            ["landfall_country_code", "landfall_country_name"],
        )
    )
    if grade_a_events:
        score_rows = "\n".join(
            f"|{row.agency}|{int(row.matched_independent_truth_events)}|{row.bias_ms:.1f} [{row.bias_95ci_low:.1f}, {row.bias_95ci_high:.1f}]|{row.mae_ms:.1f} [{row.mae_95ci_low:.1f}, {row.mae_95ci_high:.1f}]|{row.rmse_ms:.1f} [{row.rmse_95ci_low:.1f}, {row.rmse_95ci_high:.1f}]|{row.status}|"
            for row in error_table.itertuples(index=False)
        )
    else:
        score_rows = "\n".join(
            f"|{row.agency}|0|NA|NA|NA|{row.status}|"
            for row in error_table.itertuples(index=False)
        )
    correlation_lookup = {
        (row.agency_i, row.agency_j): row
        for row in error_correlation_intervals.itertuples(index=False)
    }
    correlation_rows = []
    for agency_i in AGENCIES:
        cells = []
        for agency_j in AGENCIES:
            row = correlation_lookup[(agency_i, agency_j)]
            if np.isfinite(row.correlation):
                cells.append(
                    f"{row.correlation:.2f} [{row.correlation_95ci_low:.2f}, {row.correlation_95ci_high:.2f}]"
                )
            else:
                cells.append("NA")
        correlation_rows.append(f"|{agency_i}|" + "|".join(cells) + "|")
    correlation_table = "\n".join(correlation_rows)
    valid_correlation_bootstraps = int(
        error_correlation_intervals.loc[
            error_correlation_intervals["agency_i"].ne(
                error_correlation_intervals["agency_j"]
            ),
            "valid_bootstrap_replicates",
        ].min()
    )
    report = f"""# B 支线：外部登陆实测风审计

冻结协议：[`landfall_truth_protocol.md`](landfall_truth_protocol.md)
状态：`external-observation-audit-complete`

## 这轮做成了什么

1. **[验证过] 108 个冻结登陆事件已完成四个地面观测档案的逐事件检索；11 个台湾案例另完成 CWA TDB 结构化支持档案全量筛查，全部来源访问门槛已逐项裁决。** A 级可评分覆盖为
   `{grade_a_events}/108`（{grade_a_events / 108:.1%}）；A+B 原始实测覆盖为
   `{observed_events}/108`（{observed_events / 108:.1%}）。IBTrACS 包内真值仍为 0，
   包外地面观测层已经覆盖 {observed_events} 个事件。
2. **[验证过] `landfall_truth.csv` 保存 {len(truth)} 条事件关联的站点极值。**
   每条记录均保留风种、平均窗数值或未知标记、质量码、时空距离、来源 URL、原始文件哈希与逐条 A/B 裁决。
3. **[验证过] A 级评分闸门得到数据裁决。** 当前严格判据下 A 级事件数为
   {grade_a_events}；五家对独立中心 Vmax 的 bias、MAE、RMSE 与真误差相关状态见下表。
4. **[验证过] 注册、申请和付费路径已按停止规则封口。** CMA 国家站/雷达、KMA、
   PAGASA、TMD 与越南国家档案均保留访问门槛证据，未绕过访问控制。

### CWA TDB 支持证据

- [验证过] CWA 年度警报清单自动匹配 `{tdb_events}/11` 个冻结台湾案例，台风 ID 由公开
  清单发现，脚本未写死事件编号。
- [验证过] `{radar_events}/11` 个案例的完整雷达产品元数据已枚举，每案缓存最接近冻结
  登陆时刻及署属站峰值时刻的合成反射率图；反射率只承担结构定位支持，直接风速算子仍归 C 级。
- [验证过] `{report_events}/11` 个案例取得数据库提供的官方事件 PDF；逐站小时风接口形成
  `{len(crosscheck)}` 条站点极值交叉核对，其中 `{crosscheck_matches}` 条与 CODiS 保留站点相接。
- [验证过] `landfall_truth_support_evidence.csv` 与 `cwa_tdb_station_crosscheck.csv` 保存上述
  枚举结果。两表复核同一批 CWA 仪器，不重复计入 `landfall_truth.csv` 的事件覆盖。
- [推测] 单人统一坐标叠加审查把 4 个低海拔署属站判为眼墙共址 A 级，7 个案例保留 B 级；
  逐案理由、雷达哈希与站点暴露位于 `cwa_tdb_eyewall_review.csv`。该人工结构判读是本轮
  A 级结果的主要不确定性。

## A/B 观测采集

|来源|记录数|覆盖事件|A级记录|B级记录|
|---|---:|---:|---:|---:|
{source_table}

- **A 级**要求原生可比持续风、质量通过、登陆 +/-6 h、足够采样密度，以及独立证据证明
  测点处于眼墙/最大风区。
- **B 级**仍是仪器实测；风窗、时间采样或最大风区代表性至少有一项未闭合。
- **C 级**反演、机构分析和无法核实数字位于
  `outputs/b_branch/landfall_truth_exclusions.csv`。

### 登陆地区覆盖

登陆地区由 Natural Earth admin-0 最近多边形赋值，台湾四个 A3 字段已核验为 `TWN`。

|代码|地区|冻结事件|A级|A+B级|
|---|---|---:|---:|---:|
{country_table}

## 五家对 A 级真值

误差定义为 `agency_10min - external_truth_10min`，单位 m/s。数值格式为
`点估计 [95% CI 下限, 上限]`；区间按 SID bootstrap 2,000 次，样本为 0 时保持 NA。

|机构|A 级事件|bias|MAE|RMSE|状态|
|---|---:|---:|---:|---:|---|
{score_rows}

[验证过] 这个表衡量机构分析与满足预注册 A 级条件的外部观测之差。CMA/CWA/HKO/JMA
测站资料可能进入相应机构业务信息集，原始测量保持独立，业务信息集并非完全隔离；CMA
在中国案例还具有主场信息优势。

[推测] A 级仍是固定低海拔站点的 10 分钟最大平均风，机构量是中心附近空间最大值；眼墙
共址缩小了空间算子差异，仍无法把两者变成完全相同的量。当前误差区间只有 4 个事件，
用于测量这 4 案的差值，不能外推为五家长期业务准确率。

### A 级真误差相关

单元格为 `rho [95% CI]`。区间按 SID block bootstrap {audit['bootstrap_replicates']:,} 次；
每个 A 级事件只出现一行，故这里的 block 单位与事件单位一致。最少有效相关重采样数为
{valid_correlation_bootstraps:,}；重复抽中单一事件或零方差的重采样按未定义剔除。

|机构|JTWC|JMA|CMA|HKO|KMA|
|---|---:|---:|---:|---:|---:|
{correlation_table}

[验证过][MEASURED] 点值和区间完整保存于
`independent_truth_error_correlation.csv` 与
`independent_truth_error_correlation_intervals.csv`。4 个 A 级事件全部位于台湾，区间主要反映
这 4 案的有限重采样；矩阵可描述该子集中的误差同向性，长期机构误差相关仍需更多 A 级事件。

## 来源全面调查

|来源|可得性|依据|访问方式|格式|
|---|---|---|---|---|
{access_table}

### 独立性和平均窗

- [据文档] ISD `WND` 的 `N` 类型缺少统一编码平均窗；`H/R/T` 分别标识
  5/60/180 分钟。ISD 记录全部按 B 级处理。
- [据文档] JMA AMeDAS 风速是观测时刻前 10 分钟平均；本次匿名下载只取质量码 8。
  逐小时抽样可能漏掉时内峰值，因此保持 B 级。
- [验证过] CWA 署属站 JSON 提供 `TenMinutelyMaximum`；自动站 `Mean/WS` 的平均窗没有
  在公开字段中闭合，自动站继续保持 B 级。
- [推测] 署属站候选用同一时刻附近的官方 6 分钟合成反射率审查眼墙共址；4 个低海拔、
  QC 通过的候选升为 A，兰屿 324 m、玉山 3844.8 m 及外围站点保持 B。
- [验证过] HKO 影响数据集提供逐站阵风和最大 60 分钟平均风；精确峰值时刻未进入工作簿，
  两种量均保持 B 级。
- [验证过] CWA 检索纳入与 +/-12 h 边界相交的完整小时箱；箱结束时刻可落到 +/-13 h，
  `observation_period_start/end` 保存实际区间，敏感性按记录时刻严格裁剪。
- [验证过] HKO 工作簿是整个警告影响期极值，记录可远离冻结首次登陆点；距离和影响期边界
  均逐条保存，全部保持 B 级。

## 覆盖敏感性与缺测

[验证过] 检索主窗为 250 km、+/-12 h。`landfall_truth_source_audit.json` 保存
50/100/150/250 km 与 +/-3/6/12 h 的事件覆盖敏感性。HKO passage-wide 记录缺少精确峰值
时刻，因此不进入时窗敏感性计数。

[验证过] 每个事件的来源级记录数和最终状态位于
`landfall_truth_event_coverage.csv`；108 个事件乘全部 13 个来源的逐格裁决位于
`landfall_truth_source_event_status.csv`。缺测并非随机：国家档案访问政策、站网密度、风窗编码、
台风路径与岛陆位置共同决定覆盖。ISD 的全球 GTS 汇集显著提高 B 级覆盖，同时保持来源国
原档案门槛的独立披露。

## 真值边界

[验证过] 西北太平洋 1987 年后缺少常态飞机侦察；沿岸风速仪测得的是固定点、固定暴露的风，
机构 Vmax 描述中心附近空间最大持续风。原生平均窗一致只闭合时间算子，最大风区证据还需
雷达眼墙定位或密集站网的可审计空间包络。

当前数据支持“公开地面观测对这些登陆事件覆盖到什么程度”。A级闸门决定五家绝对误差
是否可识别；B 级记录不进入评分。

## 预注册偏离

- [验证过] 无 A/B/C 判据偏离。
- [验证过] CODiS 站点接口采用每批 150 站，CWA TDB 自动站接口采用每批 100 站；两项变化
  只减少网络请求数，不改变站点集合、时间窗或分级规则。
- [验证过] HKO 合并工作簿提供 60 分钟平均风，原先拟议的逐年网页表改由同一官方汇编
  全量读取；平均窗语义保持 60 分钟并归 B 级。
- [验证过] 预注册要求按国家/地区报告覆盖，执行阶段增加 Natural Earth admin-0 最近多边形
  赋值；该字段只分组描述覆盖，不参与 A/B 分级和机构评分。
- [验证过] CWA TDB 的年度警报清单提供可枚举事件 PDF，执行阶段据此补齐全部 11 个台湾
  案例；其他国家官方复盘与论文仍按逐案、非完整机器索引来源登记。
- [验证过] 预注册已指定雷达眼墙定位作为 A 级门槛；执行阶段增加一份 11 案单人雷达
  叠加标注表。判据未改动，人工判读统一标为 `[推测]` 并保留原图哈希。

## 三把刀

1. **状态向量。** 本任务没有预测状态；测量记录为
   `(station, time/window, location, wind, averaging window, QC, grade)`。
2. **参数与独立观测。** 拟合参数为 0；A 级独立事件为 {grade_a_events}，B 级覆盖事件为
   {observed_events}。访问半径和时窗属于预注册检索常量，并已做离散敏感性。
3. **证伪数据。** 带原始哈希的匿名站点档案可复查每条测量；独立雷达眼墙定位、明确
   10 分钟持续风和完整峰值时序可把具体 B 记录升级为 A，也可证伪当前的保守分级。

## 复现

```bash
cd "{ROOT}"
.venv/bin/python scripts/run_landfall_truth.py --check
.venv/bin/python scripts/run_landfall_truth.py --offline --check
```

原始下载位于被 Git 忽略的 `data/raw/landfall_truth/`。公开产物保存来源 URL、SHA-256、
逐条裁决和源级访问证据。
"""
    REPORT_PATH.write_text(report, encoding="utf-8")


def output_paths() -> list[Path]:
    return [
        OUTPUT_DIR / "landfall_truth.csv",
        OUTPUT_DIR / "landfall_truth_exclusions.csv",
        OUTPUT_DIR / "landfall_truth_event_coverage.csv",
        OUTPUT_DIR / "landfall_truth_source_event_status.csv",
        OUTPUT_DIR / "landfall_truth_support_evidence.csv",
        OUTPUT_DIR / "cwa_tdb_station_crosscheck.csv",
        OUTPUT_DIR / "cwa_tdb_eyewall_review.csv",
        OUTPUT_DIR / "landfall_truth_source_audit.json",
        OUTPUT_DIR / "landfall_truth_coverage_summary.json",
        OUTPUT_DIR / "independent_truth_error_rows.csv",
        OUTPUT_DIR / "independent_truth_error_table.csv",
        OUTPUT_DIR / "independent_truth_error_correlation.csv",
        OUTPUT_DIR / "independent_truth_error_correlation_intervals.csv",
        OUTPUT_DIR / "landfall_truth_manifest.json",
        REPORT_PATH,
    ]


def build(*, offline: bool, workers: int, replicates: int) -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    landfalls, events = load_events(LANDFALL_PATH)
    event_context, country_audit = natural_earth_country_context(
        events, offline=offline
    )
    records: list[dict[str, Any]] = []
    collector_audit: dict[str, Any] = {}

    isd_records, collector_audit["NOAA_ISD"] = collect_isd(
        events, offline=offline, workers=workers
    )
    records.extend(isd_records)
    cwa_records, collector_audit["CWA_CODIS"] = collect_cwa(
        events, offline=offline
    )
    records.extend(cwa_records)
    support, cwa_tdb_crosscheck, collector_audit["CWA_TDB_SUPPORT"] = (
        collect_cwa_tdb(
            events,
            event_context,
            cwa_records,
            offline=offline,
        )
    )
    jma_records, collector_audit["JMA_AMEDAS"] = collect_jma(
        events, offline=offline
    )
    records.extend(jma_records)
    hko_records, collector_audit["HKO_TC_IMPACT"] = collect_hko(
        events, offline=offline
    )
    records.extend(hko_records)

    truth = finalize_records(records)
    truth, support, eyewall_review = apply_cwa_eyewall_review(truth, support)
    if not truth.empty and not truth["grade"].isin(["A", "B"]).all():
        raise RuntimeError("Every truth-table record must be grade A or B")
    if not truth.loc[truth["grade"] == "B", "scoreable"].eq(False).all():
        raise RuntimeError("Every grade-B record must be non-scoreable")
    if not truth.loc[truth["grade"] == "A", "scoreable"].eq(True).all():
        raise RuntimeError("Every grade-A record must be scoreable")
    truth.to_csv(OUTPUT_DIR / "landfall_truth.csv", index=False)
    support.to_csv(
        OUTPUT_DIR / "landfall_truth_support_evidence.csv", index=False
    )
    cwa_tdb_crosscheck.to_csv(
        OUTPUT_DIR / "cwa_tdb_station_crosscheck.csv", index=False
    )
    eyewall_review.to_csv(
        OUTPUT_DIR / "cwa_tdb_eyewall_review.csv", index=False
    )
    pd.DataFrame(EXCLUSIONS).to_csv(
        OUTPUT_DIR / "landfall_truth_exclusions.csv", index=False
    )

    coverage = coverage_table(
        events,
        truth,
        COLLECTED_SOURCE_IDS,
        event_context=event_context,
    )
    coverage.to_csv(OUTPUT_DIR / "landfall_truth_event_coverage.csv", index=False)
    source_event_status = source_event_status_table(
        events,
        truth,
        SOURCE_AUDIT,
        collected_source_ids=COLLECTED_SOURCE_IDS,
        absent_status=ABSENT_SOURCE_STATUS,
    )
    source_event_status = apply_cwa_tdb_source_status(
        source_event_status,
        support,
        event_context,
    )
    source_event_status.to_csv(
        OUTPUT_DIR / "landfall_truth_source_event_status.csv", index=False
    )
    error_rows, error_table, error_correlation = score_agencies(
        landfalls,
        truth,
        AGENCIES,
        replicates=replicates,
        seed=BOOTSTRAP_SEED,
    )
    error_rows.to_csv(OUTPUT_DIR / "independent_truth_error_rows.csv", index=False)
    error_table.to_csv(OUTPUT_DIR / "independent_truth_error_table.csv", index=False)
    error_correlation.to_csv(
        OUTPUT_DIR / "independent_truth_error_correlation.csv"
    )
    error_correlation_intervals = bootstrap_error_correlations(
        error_rows,
        AGENCIES,
        replicates=replicates,
        seed=BOOTSTRAP_SEED + 100,
    )
    error_correlation_intervals.to_csv(
        OUTPUT_DIR / "independent_truth_error_correlation_intervals.csv",
        index=False,
    )

    source_audit = {
        "generated_at_utc": utc_now_iso(),
        "bootstrap_replicates": replicates,
        "protocol": "landfall_truth_protocol.md",
        "frozen_event_count": len(events),
        "source_catalog": SOURCE_AUDIT,
        "collector_results": collector_audit,
        "cwa_eyewall_review": {
            "path": str(CWA_EYEWALL_REVIEW_PATH.relative_to(ROOT)),
            "sha256": sha256_file(CWA_EYEWALL_REVIEW_PATH),
            "reviewed_events": len(eyewall_review),
            "grade_a_events": int(eyewall_review["final_grade"].eq("A").sum()),
            "method": "single-reviewer station overlay on official composite reflectivity; judgments labeled [推测]",
        },
        "landfall_country_geometry": country_audit,
        "evidence_labels": {
            "verified": "[验证过] directly accessed or computed in this run",
            "documented": "[据文档] stated by the official source documentation",
            "inferred": "[推测] interpretation requiring an explicit assumption",
        },
    }
    write_json(OUTPUT_DIR / "landfall_truth_source_audit.json", source_audit)
    summary = {
        "frozen_events": len(events),
        "truth_records": len(truth),
        "grade_a_records": int(truth["grade"].eq("A").sum()),
        "grade_b_records": int(truth["grade"].eq("B").sum()),
        "grade_a_events": int(coverage["grade_a_event"].sum()),
        "grade_a_event_coverage": float(coverage["grade_a_event"].mean()),
        "grade_a_or_b_events": int(coverage["grade_a_or_b_event"].sum()),
        "grade_a_or_b_event_coverage": float(
            coverage["grade_a_or_b_event"].mean()
        ),
        "source_counts": source_counts(truth),
        "coverage_by_year": grouped_coverage(coverage, ["landfall_year"]),
        "coverage_by_country": grouped_coverage(
            coverage,
            ["landfall_country_code", "landfall_country_name"],
        ),
        "sensitivity": sensitivity_coverage(truth),
        "cwa_tdb_support_events": int(
            support.loc[
                support["evidence_type"].eq("warning_registry_match"), "SID"
            ].nunique()
        ),
        "cwa_tdb_station_crosscheck_rows": len(cwa_tdb_crosscheck),
        "grade_a_scoring_status": (
            "measured" if coverage["grade_a_event"].any() else "unidentifiable_zero_grade_a_coverage"
        ),
        "disclaimer": "This measures station-observation coverage and consistency with grade-A truth, not unobserved common agency error.",
    }
    write_json(OUTPUT_DIR / "landfall_truth_coverage_summary.json", summary)
    write_report(
        truth,
        coverage,
        error_table,
        error_correlation_intervals,
        support,
        cwa_tdb_crosscheck,
        source_audit,
    )

    manifest = {
        "generated_at_utc": utc_now_iso(),
        "bootstrap_replicates": replicates,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "search_radius_km": SEARCH_RADIUS_KM,
        "search_window_hours": SEARCH_WINDOW_HOURS,
        "inputs": {
            "frozen_landfalls": {
                "path": str(LANDFALL_PATH.relative_to(ROOT)),
                "sha256": sha256_file(LANDFALL_PATH),
            },
            "protocol": {
                "path": "landfall_truth_protocol.md",
                "sha256": sha256_file(ROOT / "landfall_truth_protocol.md"),
            },
            "cwa_eyewall_review": {
                "path": str(CWA_EYEWALL_REVIEW_PATH.relative_to(ROOT)),
                "sha256": sha256_file(CWA_EYEWALL_REVIEW_PATH),
            },
        },
        "outputs": {},
    }
    manifest_path = OUTPUT_DIR / "landfall_truth_manifest.json"
    for path in output_paths():
        if path == manifest_path or not path.exists():
            continue
        manifest["outputs"][str(path.relative_to(ROOT))] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    write_json(manifest_path, manifest)
    return summary


def check_outputs() -> None:
    truth = pd.read_csv(OUTPUT_DIR / "landfall_truth.csv")
    coverage = pd.read_csv(OUTPUT_DIR / "landfall_truth_event_coverage.csv")
    errors = pd.read_csv(OUTPUT_DIR / "independent_truth_error_table.csv")
    error_correlation_intervals = pd.read_csv(
        OUTPUT_DIR / "independent_truth_error_correlation_intervals.csv"
    )
    source_status = pd.read_csv(
        OUTPUT_DIR / "landfall_truth_source_event_status.csv"
    )
    support = pd.read_csv(OUTPUT_DIR / "landfall_truth_support_evidence.csv")
    crosscheck = pd.read_csv(OUTPUT_DIR / "cwa_tdb_station_crosscheck.csv")
    eyewall_review = pd.read_csv(OUTPUT_DIR / "cwa_tdb_eyewall_review.csv")
    summary = json.loads(
        (OUTPUT_DIR / "landfall_truth_coverage_summary.json").read_text(
            encoding="utf-8"
        )
    )
    if len(coverage) != 108 or coverage["SID"].duplicated().any():
        raise RuntimeError("Coverage output must contain 108 unique events")
    if not truth["grade"].isin(["A", "B"]).all():
        raise RuntimeError("Truth output contains an unclassified record")
    if not truth.loc[truth["grade"] == "B", "scoreable"].astype(str).str.lower().eq("false").all():
        raise RuntimeError("Grade-B output contains a scoreable record")
    if not truth.loc[truth["grade"] == "A", "scoreable"].astype(str).str.lower().eq("true").all():
        raise RuntimeError("Grade-A output contains a non-scoreable record")
    if len(errors) != 5:
        raise RuntimeError("Agency error table must contain five agencies")
    if len(error_correlation_intervals) != 25:
        raise RuntimeError("Agency error-correlation intervals must contain 25 cells")
    expected_source_rows = 108 * len(SOURCE_AUDIT)
    if len(source_status) != expected_source_rows:
        raise RuntimeError(
            f"Source-event table must contain {expected_source_rows} rows"
        )
    if source_status[["SID", "source_id"]].duplicated().any():
        raise RuntimeError("Source-event table contains duplicate SID/source rows")
    if source_status["status"].isna().any():
        raise RuntimeError("Source-event table contains an unclassified source")
    registry_events = support.loc[
        support["evidence_type"].eq("warning_registry_match"), "SID"
    ].nunique()
    if registry_events != 11:
        raise RuntimeError("CWA TDB registry must match all 11 frozen Taiwan events")
    if support.loc[support["product"].eq("Radar"), "SID"].nunique() != 11:
        raise RuntimeError("CWA TDB radar metadata must cover all Taiwan events")
    if crosscheck["SID"].nunique() != 11:
        raise RuntimeError("CWA TDB station cross-check must cover all Taiwan events")
    if len(eyewall_review) != 11 or int(eyewall_review["final_grade"].eq("A").sum()) != 4:
        raise RuntimeError("CWA TDB eye-wall review must retain 11 decisions and four A-grade events")
    country_audit = json.loads(
        (OUTPUT_DIR / "landfall_truth_source_audit.json").read_text(
            encoding="utf-8"
        )
    )["landfall_country_geometry"]
    if set(country_audit["taiwan_code_check"].values()) != {"TWN"}:
        raise RuntimeError("Natural Earth Taiwan A3 fields must all equal TWN")
    if summary["grade_a_events"] != int(coverage["grade_a_event"].sum()):
        raise RuntimeError("Grade-A coverage summary mismatch")
    if summary["grade_a_or_b_events"] != int(
        coverage["grade_a_or_b_event"].sum()
    ):
        raise RuntimeError("A+B coverage summary mismatch")
    expected_frozen_counts = {
        "truth_records": 16289,
        "grade_a_records": 4,
        "grade_b_records": 16285,
        "grade_a_events": 4,
        "grade_a_or_b_events": 108,
    }
    changed_counts = {
        key: (summary.get(key), expected)
        for key, expected in expected_frozen_counts.items()
        if summary.get(key) != expected
    }
    if changed_counts:
        raise RuntimeError(
            f"Frozen landfall-truth counts changed and require adjudication: {changed_counts}"
        )
    for path in output_paths():
        if not path.exists() or path.stat().st_size == 0:
            raise RuntimeError(f"Missing or empty deliverable: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect and grade external landfall wind observations"
    )
    parser.add_argument("--offline", action="store_true", help="Use only cached raw files")
    parser.add_argument("--check", action="store_true", help="Validate tracked outputs after the run")
    parser.add_argument("--workers", type=int, default=min(12, (os.cpu_count() or 4) * 2))
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = build(
        offline=args.offline,
        workers=args.workers,
        replicates=args.bootstrap_replicates,
    )
    if args.check:
        check_outputs()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
