"""location_text → sites.site_id: LLM-Normalisierung auf eine Stadt, gecacht.

Läuft unbeaufsichtigt (wie extract/score), anders als der manuelle, mensch-geprüfte
companies.yaml-Flow. Geokodiert selbst nichts — legt nur generische Sites mit
address_text an, die companies.geocode_missing() unverändert aufgreift."""

import sqlite3
from datetime import UTC, datetime

from pydantic import BaseModel
from rapidfuzz import fuzz

from . import llm
from .extract import Extraction
from .normalize import norm_text

LOCATION_SCHEMA_VERSION = 1
COMPANY_SITE_MATCH_THRESHOLD = 85


class LocationResolution(BaseModel):
    city: str | None


SYSTEM_PROMPT = """Du bekommst einen freien Standort-Text aus einer österreichischen
Stellenanzeige und reduzierst ihn auf eine einzelne kanonische Stadt/Gemeinde in Österreich.

Regeln:
- Gib den offiziellen Namen der Stadt/Gemeinde zurück, ohne PLZ (z. B. "Klosterneuburg"
  statt "3400 Klosterneuburg", "Wien" statt "1010 Wien").
- Bei "Raum <Stadt>", "Großraum <Stadt>", "bei <Stadt>", "Homeoffice möglich, Raum <Stadt>"
  gib <Stadt> zurück.
- Bei mehreren möglichen Orten (z. B. "Wien oder Graz", "Standort Wien/Linz") gib null zurück.
- Bei reinem Homeoffice/Remote ohne konkrete Ortsangabe gib null zurück.
- Bei Orten außerhalb Österreichs gib null zurück.
- Bei leerem, generischem ("Österreich", "diverse Standorte") oder nicht auflösbarem Text
  gib null zurück.
- Erfinde nichts. Im Zweifel: null."""


def _cache_get(conn: sqlite3.Connection, key: str) -> tuple[bool, str | None]:
    """(hit, city). hit=False bedeutet: noch nicht (in aktueller Version) aufgelöst."""
    row = conn.execute(
        "SELECT city FROM location_cache WHERE location_key=? AND schema_version=?",
        (key, LOCATION_SCHEMA_VERSION),
    ).fetchone()
    return (True, row["city"]) if row else (False, None)


def _cache_put(conn: sqlite3.Connection, key: str, raw: str, city: str | None) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO location_cache
           (location_key, location_text_raw, city, schema_version, model, resolved_at)
           VALUES (?,?,?,?,?,?)""",
        (
            key,
            raw,
            city,
            LOCATION_SCHEMA_VERSION,
            llm.EXTRACT_MODEL,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


_NULL_WORDS = {"null", "none", "n/a", "-", ""}


def _clean_city(city: str | None) -> str | None:
    # Kleine lokale Modelle geben bei Optional-Feldern gelegentlich das Wort "null"
    # als String statt JSON null zurück — nicht als echten Ortsnamen behandeln.
    if city is None:
        return None
    city = city.strip()
    return city if city.lower() not in _NULL_WORDS else None


def resolve_city(conn: sqlite3.Connection, location_text: str) -> str | None:
    """Kanonische Stadt für einen freien Standort-Text, gecacht über den normalisierten Text."""
    key = norm_text(location_text)
    if not key:
        return None
    hit, cached = _cache_get(conn, key)
    if hit:
        return cached
    result = llm.parse_structured(SYSTEM_PROMPT, location_text, LocationResolution, max_tokens=100)
    city = _clean_city(result.city)
    _cache_put(conn, key, location_text, city)
    return city


def _match_company_site(conn: sqlite3.Connection, company_id: int, city: str) -> int | None:
    """Kuratierte Firmen-Site bevorzugen (SPEC: Werk ≠ Firmensitz), sonst None."""
    target = norm_text(city)
    best_id, best_score = None, 0.0
    for row in conn.execute("SELECT id, label FROM sites WHERE company_id=?", (company_id,)):
        score = fuzz.partial_ratio(target, norm_text(row["label"]))
        if score > best_score:
            best_id, best_score = row["id"], score
    return best_id if best_score >= COMPANY_SITE_MATCH_THRESHOLD else None


def _get_or_create_generic_site(conn: sqlite3.Connection, city: str) -> int:
    conn.execute(
        """INSERT INTO sites (company_id, label, address_text, is_hq)
           VALUES (NULL, ?, ?, 0)
           ON CONFLICT(label) WHERE company_id IS NULL DO NOTHING""",
        (city, f"{city}, Österreich"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM sites WHERE company_id IS NULL AND label=?", (city,)
    ).fetchone()
    return row["id"]


def resolve_locations(conn: sqlite3.Connection, limit: int | None = None) -> int:
    """Ordnet Postings ohne site_id eine Site zu (Firmen-Site > generische Site > unbekannt)."""
    rows = conn.execute(
        "SELECT id, company_id, extracted_json FROM postings WHERE site_id IS NULL ORDER BY id"
    ).fetchall()
    if limit:
        rows = rows[:limit]
    done = 0
    for row in rows:
        ex = Extraction.model_validate_json(row["extracted_json"])
        city = resolve_city(conn, ex.location_text)
        if not city:
            continue
        site_id = (
            _match_company_site(conn, row["company_id"], city) if row["company_id"] else None
        )
        if site_id is None:
            site_id = _get_or_create_generic_site(conn, city)
        conn.execute("UPDATE postings SET site_id=? WHERE id=?", (site_id, row["id"]))
        conn.commit()
        done += 1
    return done
