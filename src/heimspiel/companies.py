"""companies.yaml → DB-Sync und Nominatim-Geocoding als Vorschlag (SPEC §10).

Werk ≠ Firmensitz ist der häufigste Fehler in Inseraten — Standorte werden
händisch in companies.yaml gepflegt, Geocoding füllt nur Lücken (1 Req/s, gecacht
über die DB: einmal geschriebene Koordinaten werden nicht erneut angefragt)."""

import sqlite3
import time

import requests

from .sources.base import USER_AGENT

NOMINATIM = "https://nominatim.openstreetmap.org/search"


def sync_companies(conn: sqlite3.Connection, entries: list[dict]) -> int:
    n = 0
    for e in entries:
        conn.execute(
            """INSERT INTO companies (name, website, career_url, seed_source, notes)
               VALUES (?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 website=excluded.website, career_url=excluded.career_url,
                 seed_source=excluded.seed_source""",
            (e["name"], e.get("website"), e.get("career_url"), e.get("seed_source"), e.get("notes")),
        )
        cid = conn.execute("SELECT id FROM companies WHERE name=?", (e["name"],)).fetchone()[0]
        for s in e.get("sites", []):
            conn.execute(
                """INSERT INTO sites (company_id, label, lat, lon, address_text, is_hq, geocode_source)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(company_id, label) DO UPDATE SET
                     lat=COALESCE(excluded.lat, sites.lat),
                     lon=COALESCE(excluded.lon, sites.lon),
                     address_text=excluded.address_text, is_hq=excluded.is_hq""",
                (
                    cid,
                    s["label"],
                    s.get("lat"),
                    s.get("lon"),
                    s.get("address"),
                    int(bool(s.get("is_hq"))),
                    "companies.yaml" if s.get("lat") else None,
                ),
            )
        n += 1
    conn.commit()
    return n


def geocode_missing(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id, label, address_text FROM sites WHERE lat IS NULL AND address_text IS NOT NULL"
    ).fetchall()
    done = 0
    for row in rows:
        try:
            resp = requests.get(
                NOMINATIM,
                params={"q": row["address_text"], "format": "json", "limit": 1, "countrycodes": "at"},
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
            hits = resp.json()
        except (requests.RequestException, ValueError):
            hits = []
        if hits:
            conn.execute(
                "UPDATE sites SET lat=?, lon=?, geocode_source='nominatim' WHERE id=?",
                (float(hits[0]["lat"]), float(hits[0]["lon"]), row["id"]),
            )
            conn.commit()
            done += 1
            print(f"  geokodiert (Vorschlag, prüfen!): {row['label']} → {hits[0]['lat']},{hits[0]['lon']}")
        time.sleep(1.1)
    return done
