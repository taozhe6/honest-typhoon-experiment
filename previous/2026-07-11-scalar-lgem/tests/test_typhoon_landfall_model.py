from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
if (TEST_DIR.parent / "scripts" / "typhoon_landfall_core.py").exists():
    ROOT = TEST_DIR.parent
    SCRIPT_DIR = ROOT / "scripts"
    CONFIG_PATH = ROOT / "config" / "typhoon_landfall_model.json"
else:
    ROOT = TEST_DIR
    SCRIPT_DIR = ROOT
    CONFIG_PATH = ROOT / "typhoon_landfall_model.json"
sys.path.insert(0, str(SCRIPT_DIR))

from typhoon_landfall_core import (  # noqa: E402
    EnvironmentState,
    SourceTrack,
    StormIdentity,
    TrackPoint,
    align_and_validate_tracks,
    build_repair_evidence,
    build_official_guidance,
    calibrate_lgem_leave_one_out,
    discover_tracks,
    find_first_landfall,
    grade_ms,
    integrate_ohc_kj_cm2,
    intensity_envelope,
    kaplan_demaria_decay,
    kaplan_demaria_tendency,
    lgem_growth_rate,
    load_natural_earth_polygons,
    load_config,
    parse_cwa_active_names,
    parse_cwa_detail,
    parse_hko_catalog,
    parse_hko_detail,
    parse_jma_targets,
    parse_jtwc_detail,
    parse_jtwc_catalog,
    parse_nmc_catalog,
    parse_hycom_profile_csv,
    potential_intensity_ms,
    resolve_storm_identity,
    rk45_solve,
    spatial_environment_qc,
    validate_hindcast_archive,
)


UTC = dt.timezone.utc


def point(hour: float, lat: float, lon: float, wind: float, *, kind: str = "analysis", category: str | None = None) -> TrackPoint:
    return TrackPoint(
        valid_utc=dt.datetime(2026, 7, 9, tzinfo=UTC) + dt.timedelta(hours=hour),
        lat=lat,
        lon=lon,
        wind_ms=wind,
        kind=kind,
        category=category,
    )


def track(source: str, points: list[TrackPoint], *, name: str = "BAVI") -> SourceTrack:
    identity = StormIdentity(name=name, number="2609", aliases=("BAVI", "巴威"))
    return SourceTrack(
        source=source,
        identity=identity,
        issue_utc=points[0].valid_utc,
        points=points,
        resolved_url=f"https://example.test/{source}",
        averaging_period_minutes=10,
        identity_evidence=[f"{source} fixture identity"],
    )


def environment(
    *,
    hour: float = 0.0,
    lat: float = 20.0,
    lon: float = 130.0,
    sst: float = 29.0,
    shear: float = 10.0,
) -> EnvironmentState:
    valid = dt.datetime(2026, 7, 9, tzinfo=UTC) + dt.timedelta(hours=hour)
    pressure = (1000.0, 925.0, 850.0, 700.0, 500.0, 300.0, 200.0, 100.0, 50.0)
    temperature = (27.0, 23.0, 19.0, 10.0, -5.0, -32.0, -52.0, -73.0, -78.0)
    mixing_ratio = (0.018, 0.014, 0.011, 0.006, 0.002, 0.0005, 0.0002, 0.00002, 0.000005)
    return EnvironmentState(
        requested_utc=valid,
        sampled_utc=valid,
        coastal_backtrack_minutes=0.0,
        atmospheric_valid_utc=valid,
        ocean_valid_utc=valid,
        lat=lat,
        lon=lon,
        air_temperature_c=temperature[0],
        relative_humidity_pct=80.0,
        surface_pressure_hpa=1005.0,
        sea_level_pressure_hpa=1007.0,
        deep_layer_shear_ms=shear,
        sst_c=sst,
        ohc_kj_cm2=80.0,
        profile_pressure_hpa=pressure,
        profile_temperature_c=temperature,
        profile_mixing_ratio_kg_kg=mixing_ratio,
        atmosphere_url="https://example.test/gfs",
        ocean_url="https://example.test/hycom",
    )


class ConfigTests(unittest.TestCase):
    def test_configuration_contains_no_storm_specific_endpoint(self) -> None:
        config = load_config(CONFIG_PATH)
        serialized = str(config["sources"])
        self.assertNotIn("3257931", serialized)
        self.assertNotIn("TC2611", serialized)
        self.assertEqual(config["physics"]["land_decay_alpha_per_hour"], 0.095)
        self.assertEqual(config["sources"]["jtwc"]["minimum_same_host_request_spacing_seconds"], 6)
        self.assertEqual(config["environment"]["ocean"]["minimum_same_host_request_spacing_seconds"], 2.0)
        self.assertEqual(config["environment"]["ocean"]["coastal_profile_backtrack_step_minutes"], 10)
        self.assertEqual(config["sources"]["nmc"]["wind_averaging_minutes"], 2)
        self.assertEqual(config["sources"]["jtwc"]["one_to_ten_minute_factor"], 0.93)
        self.assertEqual(config["calibration"]["minimum_hindcast_storms"], 5)


class DiscoveryTests(unittest.TestCase):
    def test_wmo_yy_nn_number_resolves_year_and_ordinal(self) -> None:
        identity = StormIdentity(name="BAVI", number="2609")

        self.assertEqual(identity.year, 2026)
        self.assertEqual(identity.ordinal, 9)

    def test_dynamic_catalogs_resolve_cross_agency_ids(self) -> None:
        nmc = 'cb(({"typhoonList":[[3257931,"BAVI","巴威","2609","2609",null,"meaning","start"]]}))'
        self.assertEqual(parse_nmc_catalog(nmc)[0]["storm_id"], "3257931")

        jma = '[{"tropicalCyclone":"TC2611","typhoonNumber":"2609","category":"TY"}]'
        self.assertEqual(parse_jma_targets(jma)[0]["tropical_cyclone"], "TC2611")

        jtwc = "(1) TYPHOON 09W (BAVI) WAS LOCATED NEAR 19.0N 128.0E."
        self.assertEqual(parse_jtwc_catalog(jtwc)[0]["number"], "09W")

        hko = 'var tc=[]; tc[0]="2614,BAVI,巴威";'
        self.assertEqual(parse_hko_catalog(hko)[0]["hko_id"], "2614")

        cwa = "var TYPHOON = {'BAVI':{'Name':{'C':'巴威','E':'BAVI'}}};"
        self.assertEqual(parse_cwa_active_names(cwa)[0], "BAVI")

    def test_hko_gis_fix_uses_ten_digit_utc_hour(self) -> None:
        hko = "2614\nA,HKOO,ST,2026070918,2026071002,0,20.10,128.30,175,-9999\nF,HKOO,ST,2026071018,2026071102,24,24.00,125.10,175,-9999\n"
        config = load_config(CONFIG_PATH)
        identity = StormIdentity(name="BAVI", number="2609", aliases=("BAVI", "巴威"))

        parsed = parse_hko_detail(hko, identity, "2614", config, "https://example.test/hko")

        self.assertEqual(parsed.points[0].valid_utc.hour, 18)
        self.assertAlmostEqual(parsed.points[0].wind_ms or 0, 175 / 3.6)

    def test_identity_can_resolve_when_nmc_catalog_is_unavailable(self) -> None:
        identity = resolve_storm_identity(
            "BAVI",
            nmc_records=None,
            jtwc_records=[{"number": "09W", "name": "BAVI"}],
            reference_year=2026,
        )

        self.assertEqual(identity.name, "BAVI")
        self.assertEqual(identity.number, "2609")
        self.assertIsNone(identity.nmc_id)

    def test_jtwc_keeps_native_one_minute_wind_and_labeled_comparison(self) -> None:
        warning = (
            "100000Z - JUL2026\n"
            "WARNING POSITION: 100000Z --- NEAR 20.0N 130.0E\n"
            "MAX SUSTAINED WINDS - 100 KT\n"
            "12 HRS, VALID AT: 101200Z --- 21.0N 129.0E\n"
            "MAX SUSTAINED WINDS - 90 KT\n"
        )
        config = load_config(CONFIG_PATH)
        identity = StormIdentity(name="BAVI", number="2609")

        parsed = parse_jtwc_detail(warning, identity, "09W", config, "https://example.test/jtwc")

        self.assertEqual(parsed.averaging_period_minutes, 1)
        self.assertAlmostEqual(parsed.points[0].wind_ms or 0.0, 100 * 0.514444, places=5)
        self.assertAlmostEqual(parsed.points[0].metadata["comparison_10min_wind_ms"], 100 * 0.514444 * 0.93, places=5)

    def test_nmc_catalog_outage_does_not_stop_other_adapters(self) -> None:
        cwa = """
var TY_TIME = {'C':'x','E':'2026/07/09 00:00 UTC'};
var TYPHOON = {'BAVI':{'Name':{'C':'巴威','E':'BAVI'}}};
var TY_LIST_2 = new Array();
TY_LIST_2['E'] = ''+'<div class="panel panel-default"><span class="fa-blue">BAVI (202609)</span><div class="typ-path"><span class="now">Analysis</span><p>0000UTC 9 July 2026</p><li>Center Location 20.0N 130.0E</li><li>Maximum Wind Speed 40 m/s</li></div><div class="typ-path"><span class="prediction">Forecast</span><li>1200UTC 9 July 2026</li><li>Center Position 21.0N 129.0E</li><li>Maximum Wind Speed 38 m/s</li></div></div>';
"""
        hko_detail = "2614\nA,HKOO,ST,2026070900,2026070900,0,20.0,130.0,144,-9999\nF,HKOO,ST,2026070912,2026070900,12,21.0,129.0,137,-9999\n"

        class Fetcher:
            def text(self, url: str, **_kwargs: object) -> str:
                if "list_default" in url:
                    raise OSError("synthetic NMC outage")
                if "targetTc" in url:
                    return "[]"
                if "abpwweb" in url:
                    return "TYPHOON 09W (BAVI) WAS LOCATED NEAR 20.0N 130.0E."
                if "wp0926web" in url:
                    raise OSError("synthetic JTWC detail outage")
                if "tc_gis_list" in url:
                    return 'var tc=[]; tc[0]="2614,BAVI,巴威";'
                if "tc_gis_posinfo" in url:
                    return hko_detail
                if "TY_NEWS-Data" in url:
                    return cwa
                raise AssertionError(url)

        config = load_config(CONFIG_PATH)
        identity, tracks, statuses = discover_tracks(Fetcher(), config, "BAVI")  # type: ignore[arg-type]

        self.assertEqual(identity.number, "2609")
        self.assertFalse(statuses["NMC"]["discovery_ok"])
        self.assertEqual({item.source for item in tracks}, {"HKO", "CWA"})


class P0AuditRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)
        self.identity = StormIdentity(name="BAVI", number="2609", aliases=("BAVI", "巴威"))

    def test_cwa_single_storm_keeps_analysis_and_first_forecast_separate(self) -> None:
        payload = """
var TY_TIME = {'C':'x','E':'2026/07/09 00:00 UTC'};
var TYPHOON = {'BAVI':{'Name':{'C':'巴威','E':'BAVI'}}};
var TY_LIST_2 = new Array();
TY_LIST_2['E'] = ''+
'<div class="panel panel-default"><span class="fa-blue">BAVI (202609)</span>'+
'<div class="typ-path"><span class="now">Analysis</span><p>0000UTC 9 July 2026</p><li>Center Location 20.0N 130.0E</li><li>Maximum Wind Speed 40 m/s</li></div>'+
'<div class="typ-path"><span class="prediction">Forecast</span><li>1200UTC 9 July 2026</li><li>Center Position 21.0N 129.0E</li><li>Maximum Wind Speed 38 m/s</li></div></div>';
"""

        parsed = parse_cwa_detail(payload, self.identity, self.config, "https://example.test/cwa")

        self.assertEqual(len(parsed.points), 2)
        self.assertEqual(parsed.points[0].kind, "analysis")
        self.assertEqual((parsed.points[0].lat, parsed.points[0].lon, parsed.points[0].wind_ms), (20.0, 130.0, 40.0))
        self.assertEqual(parsed.points[1].kind, "official_forecast")
        self.assertEqual((parsed.points[1].lat, parsed.points[1].lon, parsed.points[1].wind_ms), (21.0, 129.0, 38.0))

    def test_cwa_multi_storm_payload_cannot_cross_splice_tracks(self) -> None:
        payload = """
var TY_TIME = {'C':'x','E':'2026/07/09 00:00 UTC'};
var TYPHOON = {'BAVI':{'Name':{'C':'巴威','E':'BAVI'}},'OTHER':{'Name':{'C':'甲','E':'OTHER'}}};
var TY_LIST_2 = new Array();
TY_LIST_2['E'] = ''+
'<div class="panel panel-default"><span class="fa-blue">BAVI (202609)</span><div class="typ-path"><span class="now">Analysis</span><p>0000UTC 9 July 2026</p><li>Center Location 20.0N 130.0E</li><li>Maximum Wind Speed 40 m/s</li></div><div class="typ-path"><span class="prediction">Forecast</span><li>1200UTC 9 July 2026</li><li>Center Position 21.0N 129.0E</li><li>Maximum Wind Speed 38 m/s</li></div></div>'+
'<div class="panel panel-default"><span class="fa-blue">OTHER (202610)</span><div class="typ-path"><span class="now">Analysis</span><p>0000UTC 9 July 2026</p><li>Center Location 10.0N 150.0E</li><li>Maximum Wind Speed 20 m/s</li></div><div class="typ-path"><span class="prediction">Forecast</span><li>1200UTC 9 July 2026</li><li>Center Position 11.0N 149.0E</li><li>Maximum Wind Speed 18 m/s</li></div></div>';
"""

        parsed = parse_cwa_detail(payload, self.identity, self.config, "https://example.test/cwa")

        self.assertEqual([(item.lat, item.lon) for item in parsed.points], [(20.0, 130.0), (21.0, 129.0)])

    def test_full_column_pi_fails_closed_for_inconsistent_surface_thermodynamics(self) -> None:
        called = False

        def backend(*_args: object, **_kwargs: object) -> tuple[float, float, int, float, float]:
            nonlocal called
            called = True
            return 55.0, 930.0, 1, 200.0, 100.0

        result = potential_intensity_ms(
            sst_c=25.0,
            sea_level_pressure_hpa=1005.0,
            pressure_profile_hpa=[1000, 850, 500, 200, 100, 50],
            temperature_profile_c=[27, 18, -5, -52, -73, -78],
            mixing_ratio_profile_kg_kg=[0.018, 0.01, 0.002, 0.0002, 0.00002, 0.000005],
            physics=self.config["physics"],
            backend=backend,
        )

        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "sst_below_lowest_profile_air_temperature")
        self.assertFalse(called)
        self.assertNotIn("wind_ms", result)

    def test_full_column_pi_uses_the_validated_backend(self) -> None:
        def backend(*_args: object, **_kwargs: object) -> tuple[float, float, int, float, float]:
            return 55.0, 930.0, 1, 200.0, 100.0

        node = environment()
        result = potential_intensity_ms(
            sst_c=node.sst_c,
            sea_level_pressure_hpa=node.surface_pressure_hpa,
            pressure_profile_hpa=node.profile_pressure_hpa,
            temperature_profile_c=node.profile_temperature_c,
            mixing_ratio_profile_kg_kg=node.profile_mixing_ratio_kg_kg,
            physics=self.config["physics"],
            backend=backend,
        )

        self.assertTrue(result["valid"])
        self.assertEqual(result["wind_ms"], 55.0)
        self.assertEqual(result["minimum_pressure_hpa"], 930.0)

    def test_tcpyPI_1_4_full_column_reference_profile(self) -> None:
        node = environment()
        result = potential_intensity_ms(
            sst_c=node.sst_c,
            sea_level_pressure_hpa=node.sea_level_pressure_hpa,
            pressure_profile_hpa=node.profile_pressure_hpa,
            temperature_profile_c=node.profile_temperature_c,
            mixing_ratio_profile_kg_kg=node.profile_mixing_ratio_kg_kg,
            physics=self.config["physics"],
        )
        if result.get("reason") == "tcpyPI_1_4_dependency_unavailable":
            self.skipTest("tcpyPI 1.4 is an optional fail-closed runtime dependency")

        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["wind_ms"], 82.9582316, places=5)
        self.assertEqual(result["backend"], "tcpyPI.pi")

    def test_low_wind_land_state_decays_monotonically(self) -> None:
        values = [kaplan_demaria_decay(12.681, hour, 13.7, 0.095, 0.9) for hour in range(7)]

        self.assertTrue(all(right <= left for left, right in zip(values, values[1:])))
        self.assertLess(values[-1], values[0])

    def test_repair_evidence_exposes_baseline_and_repaired_values(self) -> None:
        evidence = build_repair_evidence(self.config)

        self.assertAlmostEqual(evidence["ohc_threshold_crossing"]["before_kj_cm2"], 8.1795, places=4)
        self.assertAlmostEqual(evidence["ohc_threshold_crossing"]["after_kj_cm2"], 4.08975, places=5)
        self.assertAlmostEqual(evidence["kaplan_demaria_r_factor"]["before_ms"], 28.573, places=3)
        self.assertAlmostEqual(evidence["kaplan_demaria_r_factor"]["after_ms"], 26.311, places=3)
        self.assertTrue(all(item["passed"] for item in evidence.values()))


class AlignmentTests(unittest.TestCase):
    def test_terminal_zero_wind_td_is_kept_as_dissipation_marker(self) -> None:
        dissipating = track(
            "JMA",
            [
                point(0, 19.0, 129.0, 35.0, category="TY"),
                point(12, 21.0, 127.0, 25.0, kind="official_forecast", category="STS"),
                point(72, 28.0, 122.0, 0.0, kind="official_forecast", category="TD"),
            ],
        )
        config = load_config(CONFIG_PATH)

        result = align_and_validate_tracks([dissipating], config)

        self.assertTrue(result.status_by_source["JMA"]["usable"])

    def test_positions_and_winds_align_at_common_effective_time(self) -> None:
        early = track("EARLY", [point(0, 19.0, 129.0, 40.0), point(3, 20.0, 128.0, 43.0, kind="forecast")])
        late = track("LATE", [point(3, 20.0, 128.0, 42.0), point(6, 21.0, 127.0, 40.0, kind="forecast")])
        config = load_config(CONFIG_PATH)

        result = align_and_validate_tracks([early, late], config)

        self.assertEqual(result.alignment_time_utc, point(3, 0, 0, 0).valid_utc)
        self.assertAlmostEqual(result.aligned["EARLY"].wind_ms or 0, 43.0)
        self.assertAlmostEqual(result.aligned["LATE"].wind_ms or 0, 42.0)

    def test_impossible_and_relative_malformed_winds_are_rejected(self) -> None:
        valid_a = track("A", [point(0, 19.0, 129.0, 45.0, category="TY"), point(3, 20.0, 128.0, 44.0, kind="forecast")])
        valid_b = track("B", [point(0, 19.1, 129.1, 46.0, category="TY"), point(3, 20.1, 128.1, 44.0, kind="forecast")])
        bad_high = track("HIGH", [point(0, 19.0, 129.0, 500.0, category="TY"), point(3, 20.0, 128.0, 40.0, kind="forecast")])
        bad_low = track("LOW", [point(0, 19.0, 129.0, 5.0, category="TY"), point(3, 20.0, 128.0, 40.0, kind="forecast")])
        config = load_config(CONFIG_PATH)

        result = align_and_validate_tracks([valid_a, valid_b, bad_high, bad_low], config)

        self.assertFalse(result.status_by_source["HIGH"]["usable"])
        self.assertFalse(result.status_by_source["LOW"]["usable"])
        self.assertEqual({item.source for item in result.usable_tracks}, {"A", "B"})

    def test_structured_category_is_normalized_before_quality_control(self) -> None:
        structured = TrackPoint(
            valid_utc=dt.datetime(2026, 7, 9, tzinfo=UTC),
            lat=19.0,
            lon=129.0,
            wind_ms=45.0,
            kind="analysis",
            category={"en": "TY"},  # type: ignore[arg-type]
        )
        future = point(3, 20.0, 128.0, 44.0, kind="official_forecast")
        config = load_config(CONFIG_PATH)

        result = align_and_validate_tracks([track("STRUCTURED", [structured, future])], config)

        self.assertTrue(result.status_by_source["STRUCTURED"]["usable"])


class GeometryTests(unittest.TestCase):
    def test_time_domain_landfall_crossing(self) -> None:
        land = [
            {
                "exterior": [(120.0, 20.0), (121.0, 20.0), (121.0, 21.0), (120.0, 21.0)],
                "holes": [],
            }
        ]
        crossing = track("A", [point(0, 20.5, 119.0, 35.0), point(3, 20.5, 122.0, 35.0, kind="forecast")])
        config = load_config(CONFIG_PATH)

        result = find_first_landfall(crossing, point(0, 0, 0, 0).valid_utc, land, config)

        self.assertEqual(result["status"], "landfall")
        self.assertAlmostEqual(result["time_utc"].hour + result["time_utc"].minute / 60, 1.0, delta=0.05)

    def test_narrow_island_crossing_is_found_between_sample_endpoints(self) -> None:
        island = [
            {
                "exterior": [(120.0, 20.0), (120.05, 20.0), (120.05, 21.0), (120.0, 21.0)],
                "holes": [],
            }
        ]
        crossing = track("A", [point(0, 20.5, 119.9, 35.0), point(1 / 6, 20.5, 120.2, 35.0, kind="forecast")])
        config = load_config(CONFIG_PATH)

        result = find_first_landfall(crossing, point(0, 0, 0, 0).valid_utc, island, config)

        self.assertEqual(result["status"], "landfall")
        elapsed_minutes = (result["time_utc"] - point(0, 0, 0, 0).valid_utc).total_seconds() / 60
        self.assertAlmostEqual(elapsed_minutes, 10 / 3, delta=0.15)

    def test_global_land_loader_is_separate_from_target_country(self) -> None:
        payload = {
            "features": [
                {"properties": {"ISO_A3": "CHN"}, "geometry": {"type": "Polygon", "coordinates": [[(120, 20), (121, 20), (121, 21), (120, 20)]]}},
                {"properties": {"ISO_A3": "TWN"}, "geometry": {"type": "Polygon", "coordinates": [[(122, 22), (123, 22), (123, 23), (122, 22)]]}},
            ]
        }

        class Fetcher:
            def json(self, _url: str) -> dict[str, object]:
                return payload

        config = load_config(CONFIG_PATH)
        target = load_natural_earth_polygons(Fetcher(), config, country_codes={"CHN"})  # type: ignore[arg-type]
        global_land = load_natural_earth_polygons(Fetcher(), config, country_codes=None)  # type: ignore[arg-type]

        self.assertEqual(len(target), 1)
        self.assertEqual(len(global_land), 2)


class PhysicsTests(unittest.TestCase):
    def test_hycom_csv_parser_accepts_a_real_profile_shape(self) -> None:
        csv_text = (
            "time,latitude,longitude,vertCoord,water_temp\n"
            "2026-07-10T00:00:00Z,20.4,128.0,0.0,28.1\n"
            "2026-07-10T00:00:00Z,20.4,128.0,50.0,26.5\n"
        )
        profile, times = parse_hycom_profile_csv(csv_text, "water_temp")

        self.assertEqual(profile, [(0.0, 28.1), (50.0, 26.5)])
        self.assertEqual(times[0], dt.datetime(2026, 7, 10, tzinfo=UTC))

    def test_ohc_exactly_integrates_a_threshold_crossing(self) -> None:
        profile = [(0.0, 28.0), (20.0, 24.0)]
        config = load_config(CONFIG_PATH)

        ohc = integrate_ohc_kj_cm2(profile, config["physics"], 26.0)

        self.assertAlmostEqual(ohc, 4.08975, places=5)

    def test_ohc_is_positive_for_a_warm_profile(self) -> None:
        profile = [(0.0, 29.0), (20.0, 28.0), (50.0, 25.0)]
        config = load_config(CONFIG_PATH)
        ohc = integrate_ohc_kj_cm2(profile, config["physics"], 26.0)

        self.assertGreater(ohc, 0.0)

    def test_land_decay_and_rk45(self) -> None:
        self.assertAlmostEqual(kaplan_demaria_tendency(40.0, 13.7, 0.095), -2.4985)
        self.assertAlmostEqual(kaplan_demaria_decay(40.0, 6.0, 13.7, 0.095, 0.9), 26.311, places=3)
        solution = rk45_solve(lambda _t, _y: [2.0], 0.0, [0.0], 5.0, rtol=1e-8, atol=1e-10)
        self.assertAlmostEqual(solution["y"][0], 10.0, places=6)
        config = load_config(CONFIG_PATH)
        self.assertEqual(grade_ms(41.5, config["wind_scales"]["cma_2min"]), "14")

    def test_grade_61_point_2_is_level_17(self) -> None:
        config = load_config(CONFIG_PATH)
        self.assertEqual(grade_ms(61.2, config["wind_scales"]["cma_2min"]), "17")
        self.assertEqual(grade_ms(61.201, config["wind_scales"]["cma_2min"]), "17+")

    def test_shear_changes_lgem_growth_rate(self) -> None:
        calibration = {
            "coefficients": {"shear": -0.0085, "convective_instability": 0.0005, "interaction": -0.0041, "intercept": 0.0063},
            "normalization": {"shear_mean_ms": 9.0, "shear_sd_ms": 5.6, "convective_mean_ms": 7.5, "convective_sd_ms": 4.1},
        }

        low = lgem_growth_rate(5.0, 10.0, calibration)
        high = lgem_growth_rate(25.0, 10.0, calibration)

        self.assertNotEqual(low, high)
        self.assertLess(high, low)

    def test_spatial_sst_qc_rejects_an_isolated_jump(self) -> None:
        paths = {"A": [environment(sst=29.5)], "B": [environment(lat=20.2, lon=130.1, sst=25.7)]}
        config = load_config(CONFIG_PATH)

        result = spatial_environment_qc(paths, config)

        self.assertEqual(result["status"], "failed")
        self.assertGreater(result["comparisons"][0]["sst_difference_c"], 3.0)


class OutputContractTests(unittest.TestCase):
    def test_envelope_keeps_range_without_pseudo_probability_statistics(self) -> None:
        envelope = intensity_envelope([30.0, 37.0, 46.0])

        self.assertEqual(envelope["spread_ms"], 16)
        self.assertNotIn("descriptive_median_ms", envelope)
        self.assertNotIn("p25_ms", envelope)
        self.assertNotIn("p75_ms", envelope)
        self.assertNotIn("mean_grade", envelope)
        self.assertNotIn("mean_ms", envelope)

    def test_official_guidance_has_one_intensity_field_per_source(self) -> None:
        source = track("ONE", [point(0, 20.0, 120.0, 40.0), point(3, 21.0, 121.0, 34.0, kind="official_forecast")])
        landfall = {
            "ONE": {
                "status": "landfall",
                "time_utc": point(1.5, 0, 0, 0).valid_utc,
                "lat": 20.5,
                "lon": 120.5,
            }
        }

        product = build_official_guidance([source], landfall)
        entry = product["by_source"][0]

        self.assertIn("official_guidance_native_wind_ms", entry)
        self.assertEqual(entry["native_wind_averaging_minutes"], 10)
        self.assertNotIn("landfall_interpolated_ms", entry)
        self.assertNotIn("landfall_lge_ms", entry)


class HindcastCalibrationTests(unittest.TestCase):
    def cases(self, count: int) -> list[dict[str, object]]:
        base = dt.datetime(2026, 1, 1, tzinfo=UTC)
        return [
            {
                "storm_id": f"26{index + 1:02d}",
                "issue_time_utc": (base + dt.timedelta(days=index)).isoformat(),
                "landfall_time_utc": (base + dt.timedelta(days=index, hours=40)).isoformat(),
                "lead_hours": 40.0,
                "initial_wind_ms": 35.0 + index,
                "landfall_wind_ms": 31.0 + index,
                "potential_intensity_ms": 60.0,
                "deep_layer_shear_ms": 8.0 + index,
                "convective_instability_ms": 20.0,
                "wind_averaging_minutes": 10,
                "predictor_cutoff_utc": (base + dt.timedelta(days=index)).isoformat(),
                "provenance": ["frozen forecast cycle", "best track"],
            }
            for index in range(count)
        ]

    def test_fewer_than_five_storms_fail_the_calibration_gate(self) -> None:
        config = load_config(CONFIG_PATH)

        result = validate_hindcast_archive(self.cases(4), config)

        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "insufficient_unique_storms")

    def test_five_storms_produce_leave_one_storm_out_metrics(self) -> None:
        config = load_config(CONFIG_PATH)

        artifact = calibrate_lgem_leave_one_out(self.cases(5), config)

        self.assertEqual(artifact["status"], "calibrated_hindcast_research")
        self.assertEqual(artifact["unique_storm_count"], 5)
        self.assertEqual(artifact["validation"]["fold_count"], 5)
        self.assertIn("landfall_wind_mae_ms", artifact["validation"])
        json.dumps(artifact)


if __name__ == "__main__":
    unittest.main()
