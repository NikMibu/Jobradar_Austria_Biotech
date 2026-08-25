"""Engine v1: MOTIS lokal für Isochronen (SPEC §7, Meilenstein M5).

Setup (einmalig, außerhalb dieses Moduls):
1. austria-latest.osm.pbf (Geofabrik) + GTFS-Feeds von mobilitaetsdaten.gv.at
   und ÖBB-GTFS (CC BY 4.0) herunterladen
2. MOTIS-Docker-Image, config.yml mit OSM + GTFS-Zips → `motis import` → `motis server`
3. Danach zeigt HEIMSPIEL_MOTIS_URL (default http://localhost:8080) auf den Server.

Dieses Modul spricht dieselbe /api/v1/plan-Schnittstelle wie Transitous; für
Isochronen liefert `one_to_all` die Transit-Erreichbarkeit aller Haltestellen
je Anker (Endpoint-Name je nach MOTIS-Version: one-to-all bzw. one-to-many —
OpenAPI der laufenden Instanz prüfen)."""

import os

import requests

MOTIS_URL = os.environ.get("HEIMSPIEL_MOTIS_URL", "http://localhost:8080")


def available() -> bool:
    try:
        return requests.get(f"{MOTIS_URL}/api/v1/plan", timeout=2).status_code < 500
    except requests.RequestException:
        return False


def one_to_all(lat: float, lon: float, max_minutes: int = 120) -> list[dict]:
    """Erreichbarkeit aller Haltestellen ab einem Anker: [{stop_id, lat, lon, minutes}]."""
    resp = requests.get(
        f"{MOTIS_URL}/api/v1/one-to-all",
        params={"one": f"{lat},{lon}", "maxTravelTime": max_minutes * 60},
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for stop in data.get("all", data.get("reachable", [])):
        pos = stop.get("place", stop)
        out.append(
            {
                "stop_id": pos.get("stopId", ""),
                "lat": pos.get("lat"),
                "lon": pos.get("lon"),
                "minutes": round(stop.get("duration", 0) / 60),
            }
        )
    return out
