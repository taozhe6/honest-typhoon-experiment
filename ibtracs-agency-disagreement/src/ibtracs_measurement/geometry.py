from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import shapefile
from pyproj import CRS, Geod, Transformer
from scipy.spatial import cKDTree
from shapely import STRtree, contains_xy, union_all
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import transform

EARTH_MEAN_RADIUS_KM = 6371.0088


def _shape_parts(shapefile_path: Path, *, polygon: bool = False) -> list:
    reader = shapefile.Reader(str(shapefile_path))
    geometries = []
    for item in reader.shapes():
        if polygon:
            geometries.append(shape(item.__geo_interface__))
            continue
        points = np.asarray(item.points, dtype=float)
        boundaries = list(item.parts) + [len(points)]
        for start, end in zip(boundaries[:-1], boundaries[1:]):
            if end - start >= 2:
                geometries.append(LineString(points[start:end]))
    return geometries


def _densify_lines(lines: Sequence[LineString], spacing_km: float) -> np.ndarray:
    geod = Geod(ellps="WGS84")
    all_points: list[tuple[float, float]] = []
    max_distance_m = spacing_km * 1000.0
    for line in lines:
        coordinates = np.asarray(line.coords, dtype=float)
        lon0 = coordinates[:-1, 0]
        lat0 = coordinates[:-1, 1]
        lon1 = coordinates[1:, 0]
        lat1 = coordinates[1:, 1]
        _, _, distances = geod.inv(lon0, lat0, lon1, lat1)
        all_points.append((float(coordinates[0, 0]), float(coordinates[0, 1])))
        for index, distance in enumerate(distances):
            subdivisions = max(1, int(np.ceil(distance / max_distance_m)))
            if subdivisions > 1:
                inserted = geod.npts(
                    float(lon0[index]),
                    float(lat0[index]),
                    float(lon1[index]),
                    float(lat1[index]),
                    subdivisions - 1,
                )
                all_points.extend((float(lon), float(lat)) for lon, lat in inserted)
            all_points.append((float(lon1[index]), float(lat1[index])))
    return np.asarray(all_points, dtype=float)


def _unit_sphere_xyz(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    lon_rad = np.deg2rad(lon)
    lat_rad = np.deg2rad(lat)
    cos_lat = np.cos(lat_rad)
    return np.column_stack(
        (cos_lat * np.cos(lon_rad), cos_lat * np.sin(lon_rad), np.sin(lat_rad))
    )


def _wrapped_query_boxes(lon: float, lat: float, radius_km: float) -> list:
    lat_delta = min(90.0, radius_km / 110.574)
    lon_scale = max(111.320 * np.cos(np.deg2rad(lat)), 1.0)
    lon_delta = min(180.0, radius_km / lon_scale)
    min_lat = max(-90.0, lat - lat_delta)
    max_lat = min(90.0, lat + lat_delta)
    min_lon = lon - lon_delta
    max_lon = lon + lon_delta
    if min_lon < -180.0:
        return [
            box(min_lon + 360.0, min_lat, 180.0, max_lat),
            box(-180.0, min_lat, max_lon, max_lat),
        ]
    if max_lon > 180.0:
        return [
            box(min_lon, min_lat, 180.0, max_lat),
            box(-180.0, min_lat, max_lon - 360.0, max_lat),
        ]
    return [box(min_lon, min_lat, max_lon, max_lat)]


@dataclass
class CoastGeometry:
    lines: list[LineString]
    land: object
    densified_lonlat: np.ndarray
    spacing_km: float

    def __post_init__(self) -> None:
        self._point_tree = cKDTree(
            _unit_sphere_xyz(self.densified_lonlat[:, 0], self.densified_lonlat[:, 1])
        )
        self._line_tree = STRtree(self.lines)

    @classmethod
    def from_shapefiles(
        cls, coastline_path: Path, land_path: Path, *, spacing_km: float = 5.0
    ) -> "CoastGeometry":
        lines = _shape_parts(coastline_path)
        polygons = _shape_parts(land_path, polygon=True)
        land = union_all(polygons)
        points = _densify_lines(lines, spacing_km)
        return cls(lines=lines, land=land, densified_lonlat=points, spacing_km=spacing_km)

    def distance_km(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        query = _unit_sphere_xyz(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
        chord, _ = self._point_tree.query(query, k=1)
        angular = 2.0 * np.arcsin(np.clip(chord / 2.0, 0.0, 1.0))
        return angular * EARTH_MEAN_RADIUS_KM

    def is_land(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        return np.asarray(contains_xy(self.land, lon, lat), dtype=bool)

    def exact_local_distance_km(self, lon: float, lat: float, approximate_km: float) -> float:
        radius = min(max(approximate_km + 75.0, 150.0), 20000.0)
        candidate_indices: set[int] = set()
        for query_box in _wrapped_query_boxes(lon, lat, radius):
            candidate_indices.update(int(value) for value in self._line_tree.query(query_box))
        if not candidate_indices:
            candidate_indices = set(range(len(self.lines)))
        candidates = [self.lines[index] for index in sorted(candidate_indices)]
        local_crs = CRS.from_proj4(
            f"+proj=aeqd +lat_0={lat:.10f} +lon_0={lon:.10f} +datum=WGS84 +units=m +no_defs"
        )
        transformer = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
        projected = transform(transformer.transform, union_all(candidates))
        return float(projected.distance(Point(0.0, 0.0)) / 1000.0)

    def validate_distances(
        self,
        lon: np.ndarray,
        lat: np.ndarray,
        *,
        sample_size: int = 200,
        seed: int = 20260712,
    ) -> dict[str, object]:
        lon = np.asarray(lon, dtype=float)
        lat = np.asarray(lat, dtype=float)
        rng = np.random.default_rng(seed)
        indices = rng.choice(len(lon), size=min(sample_size, len(lon)), replace=False)
        approximate = self.distance_km(lon[indices], lat[indices])
        exact = np.array(
            [
                self.exact_local_distance_km(float(lon[index]), float(lat[index]), float(distance))
                for index, distance in zip(indices, approximate)
            ]
        )
        errors = np.abs(approximate - exact)
        return {
            "sample_size": int(len(indices)),
            "indices": indices,
            "approximate_km": approximate,
            "exact_local_aeqd_km": exact,
            "absolute_error_km": errors,
            "median_absolute_error_km": float(np.median(errors)),
            "p95_absolute_error_km": float(np.percentile(errors, 95)),
            "max_absolute_error_km": float(np.max(errors)),
        }

    def crossing_fraction(
        self, lon0: float, lat0: float, lon1: float, lat1: float, iterations: int = 45
    ) -> float:
        delta_lon = ((lon1 - lon0 + 180.0) % 360.0) - 180.0

        def coordinates(fraction: float) -> tuple[float, float]:
            lon = ((lon0 + fraction * delta_lon + 180.0) % 360.0) - 180.0
            lat = lat0 + fraction * (lat1 - lat0)
            return lon, lat

        low, high = 0.0, 1.0
        for _ in range(iterations):
            middle = (low + high) / 2.0
            lon, lat = coordinates(middle)
            if bool(contains_xy(self.land, lon, lat)):
                high = middle
            else:
                low = middle
        return high

    def taiwan_included(self) -> bool:
        return bool(contains_xy(self.land, 121.0, 23.5))
