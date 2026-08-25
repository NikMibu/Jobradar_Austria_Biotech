"""Engine v0: Transitous (public MOTIS-API), SPEC §7.

Usage-Policy: Open-Source-Projekt, Last klein halten, Ergebnisse cachen —
darum nur fehlende (site, anchor)-Paare, 1 Request pro ~1.5 s, Referenzzeit
nächster Dienstag 07:00 lokal."""

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import requests

from ..config import Profile

API = "https://api.transitous.org/api/v1/plan"
REQUEST_DELAY_S = 1.5
ENGINE = "transitous"


def next_tuesday_7am() -> datetime:
    now = datetime.now().astimezone()
    days_ahead = (1 - now.weekday()) % 7 or 7  # weekday 1 = Dienstag
    d = (now + timedelta(days=days_ahead)).replace(hour=7, minute=0, second=0, microsecond=0)
    return d


def plan_minutes(
    from_lat: float, from_lon: float, to_lat: float, to_lon: float, when: datetime
) -> tuple[int, int] | None:
    """Beste Verbindung (Minuten, Umstiege) oder None."""
    try:
        resp = requests.get(
            API,
            params={
                "fromPlace": f"{from_lat},{from_lon}",
                "toPlace": f"{to_lat},{to_lon}",
                "time": when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            headers={"User-Agent": "heimspiel/0.1 (open-source job radar)"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    best: tuple[int, int] | None = None
    for it in data.get("itineraries", []):
        minutes = round(it.get("duration", 0) / 60)
        transfers = it.get("transfers", 0)
        if minutes and (best is None or minutes < best[0]):
            best = (minutes, transfers)
    return best


def sync_anchors(conn: sqlite3.Connection, profile: Profile) -> None:
    for a in profile.anchors:
        if a.lat is None or a.lon is None:
            print(f"  Anker '{a.id}' hat keine lat/lon in profile.local.yaml — übersprungen")
            continue
        conn.execute(
            "INSERT OR REPLACE INTO anchors (id, label, lat, lon) VALUES (?,?,?,?)",
            (a.id, a.label, a.lat, a.lon),
        )
    conn.commit()


def compute_missing(conn: sqlite3.Connection, profile: Profile) -> int:
    """Berechnet Fahrzeiten für alle (site, anchor)-Paare ohne Cache-Eintrag."""
    sync_anchors(conn, profile)
    pairs = conn.execute(
        """SELECT s.id AS site_id, s.lat AS slat, s.lon AS slon,
                  a.id AS anchor_id, a.lat AS alat, a.lon AS alon
           FROM sites s CROSS JOIN anchors a
           LEFT JOIN travel_times t ON t.site_id = s.id AND t.anchor_id = a.id
           WHERE t.site_id IS NULL AND s.lat IS NOT NULL AND s.lon IS NOT NULL"""
    ).fetchall()
    when = next_tuesday_7am()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    done = 0
    for p in pairs:
        best = plan_minutes(p["alat"], p["alon"], p["slat"], p["slon"], when)
        conn.execute(
            "INSERT OR REPLACE INTO travel_times (site_id, anchor_id, minutes, transfers, engine, computed_at) VALUES (?,?,?,?,?,?)",
            (
                p["site_id"],
                p["anchor_id"],
                best[0] if best else None,
                best[1] if best else None,
                ENGINE,
                now,
            ),
        )
        conn.commit()
        done += 1
        time.sleep(REQUEST_DELAY_S)
    return done


def rebuild(conn: sqlite3.Connection, profile: Profile) -> int:
    """`heimspiel travel --rebuild` nach GTFS-/Fahrplanwechsel (Dezember)."""
    conn.execute("DELETE FROM travel_times")
    conn.commit()
    return compute_missing(conn, profile)
