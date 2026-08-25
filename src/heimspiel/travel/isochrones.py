"""H3-Hex-Isochronen aus Haltestellen-Erreichbarkeiten (SPEC §7, M5).

Zellzeit = min(Haltestellenzeit + Fußweg bei 5 km/h zur Zellmitte) über
alle Haltestellen in der Zelle und ihren Nachbarn. Braucht das Extra `geo` (h3)."""

import json
import math
from pathlib import Path

H3_RES = 6
CLASSES = [45, 60, 90, 120]
WALK_KMH = 5.0


def _walk_minutes(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    km = 2 * r * math.asin(math.sqrt(a))
    return km / WALK_KMH * 60


def build_hex_isochrone(stops: list[dict]) -> dict:
    """Aus [{lat, lon, minutes}] ein GeoJSON-FeatureCollection mit Hex-Zellen und Klasse."""
    try:
        import h3
    except ImportError as e:
        raise RuntimeError("h3 fehlt. Installieren mit: uv sync --extra geo") from e

    cell_minutes: dict[str, float] = {}
    for s in stops:
        if s.get("lat") is None or s.get("minutes") is None:
            continue
        cell = h3.latlng_to_cell(s["lat"], s["lon"], H3_RES)
        for c in h3.grid_disk(cell, 1):
            clat, clon = h3.cell_to_latlng(c)
            total = s["minutes"] + _walk_minutes(s["lat"], s["lon"], clat, clon)
            if c not in cell_minutes or total < cell_minutes[c]:
                cell_minutes[c] = total

    features = []
    for cell, minutes in cell_minutes.items():
        klass = next((k for k in CLASSES if minutes <= k), None)
        if klass is None:
            continue
        boundary = h3.cell_to_boundary(cell)
        ring = [[lon, lat] for lat, lon in boundary] + [[boundary[0][1], boundary[0][0]]]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"minutes": round(minutes), "class": klass, "h3": cell},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def write_isochrone(anchor_id: str, stops: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{anchor_id}.geojson"
    path.write_text(json.dumps(build_hex_isochrone(stops)), encoding="utf-8")
    return path
