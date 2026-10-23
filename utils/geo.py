"""
Geodesic helpers: distances, loop detection and polygon metrics.
All coordinates are WGS84; shapely geometries use (lng, lat) order.
"""
import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from pyproj import Geod
from shapely import wkt as shapely_wkt
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points
from shapely.validation import make_valid

GEOD = Geod(ellps="WGS84")
EARTH_RADIUS_KM = 6371.0088

LOOP_MIN_DISTANCE_KM = 0.3
LOOP_MAX_GAP_M = 50.0
MIN_TERRITORY_AREA_KM2 = 0.0001   # 100 m²
MAX_TERRITORY_AREA_KM2 = 10.0
SIMPLIFY_ABOVE_KM2 = 2.0
SIMPLIFY_TOLERANCE_DEG = 0.00005

LatLng = Tuple[float, float]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def path_length_km(points: Sequence[LatLng]) -> float:
    return sum(
        haversine_km(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
        for i in range(1, len(points))
    )


def _largest_polygon(geom: BaseGeometry) -> Optional[Polygon]:
    """make_valid can return a collection; keep the biggest polygonal piece."""
    if isinstance(geom, Polygon):
        return geom if not geom.is_empty else None
    candidates = []
    if isinstance(geom, MultiPolygon):
        candidates = list(geom.geoms)
    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            if isinstance(part, Polygon):
                candidates.append(part)
            elif isinstance(part, MultiPolygon):
                candidates.extend(part.geoms)
    candidates = [c for c in candidates if not c.is_empty]
    return max(candidates, key=lambda p: p.area) if candidates else None


@dataclass
class PolygonMetrics:
    polygon: Polygon
    area_km2: float
    perimeter_km: float

    @property
    def wkt(self) -> str:
        return self.polygon.wkt


def polygon_metrics(polygon: Polygon) -> PolygonMetrics:
    area_m2, perimeter_m = GEOD.geometry_area_perimeter(polygon)
    return PolygonMetrics(
        polygon=polygon,
        area_km2=round(abs(area_m2) / 1_000_000, 4),
        perimeter_km=round(abs(perimeter_m) / 1000, 3),
    )


def polygon_from_points(points: Sequence[LatLng]) -> Optional[PolygonMetrics]:
    """
    Close the ring, repair self-intersections and return geodesic metrics.
    Returns None when the points do not enclose any area.
    """
    coords = [(lng, lat) for lat, lng in points]
    if len(set(coords)) < 3:
        return None
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    try:
        raw = Polygon(coords)
        polygon = raw if raw.is_valid else _largest_polygon(make_valid(raw))
    except (ValueError, TypeError):
        return None
    if polygon is None or polygon.is_empty or polygon.area == 0:
        return None
    return polygon_metrics(polygon)


@dataclass
class LoopCheck:
    distance_km: float
    gap_m: float
    is_closed: bool
    polygon: Optional[PolygonMetrics]


def check_loop(points: Sequence[LatLng], distance_km: Optional[float] = None) -> LoopCheck:
    """
    A run can claim a territory when:
      1. the path is at least 300 m long,
      2. its first and last fixes are within 50 m,
      3. it has at least 3 distinct vertices enclosing a non-zero area.
    """
    if distance_km is None:
        distance_km = path_length_km(points)
    if len(points) < 3:
        return LoopCheck(distance_km, float("inf"), False, None)
    gap_m = haversine_km(points[0][0], points[0][1], points[-1][0], points[-1][1]) * 1000
    polygon = polygon_from_points(points)
    is_closed = (
        distance_km >= LOOP_MIN_DISTANCE_KM
        and gap_m <= LOOP_MAX_GAP_M
        and polygon is not None
        and polygon.area_km2 >= MIN_TERRITORY_AREA_KM2
    )
    return LoopCheck(distance_km, gap_m, is_closed, polygon)


def load_polygon(polygon_wkt: str) -> Optional[Polygon]:
    try:
        geom = shapely_wkt.loads(polygon_wkt)
    except Exception:
        return None
    if not geom.is_valid:
        geom = make_valid(geom)
    return _largest_polygon(geom)


def bbox_for_radius(lat: float, lng: float, radius_km: float) -> Tuple[float, float, float, float]:
    """(min_lat, min_lng, max_lat, max_lng) enclosing a circle; lng span widened near the poles."""
    dlat = radius_km / 111.32
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    dlng = min(radius_km / (111.32 * cos_lat), 180.0)
    return (max(lat - dlat, -90.0), max(lng - dlng, -180.0), min(lat + dlat, 90.0), min(lng + dlng, 180.0))


def distance_to_polygon_m(lat: float, lng: float, polygon: Polygon) -> float:
    """0 when inside, otherwise geodesic distance to the nearest edge point."""
    point = Point(lng, lat)
    if polygon.contains(point):
        return 0.0
    nearest = nearest_points(polygon, point)[0]
    return haversine_km(lat, lng, nearest.y, nearest.x) * 1000


def simplify_for_client(polygon_wkt: str, area_km2: float) -> str:
    if area_km2 <= SIMPLIFY_ABOVE_KM2:
        return polygon_wkt
    polygon = load_polygon(polygon_wkt)
    if polygon is None:
        return polygon_wkt
    simplified = polygon.simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
    return simplified.wkt if not simplified.is_empty else polygon_wkt


def overlap_ratio(new_polygon: Polygon, existing: Polygon) -> float:
    """Share of the existing polygon covered by the new one (0..1)."""
    if existing.area == 0:
        return 0.0
    try:
        return new_polygon.intersection(existing).area / existing.area
    except Exception:
        return 0.0
