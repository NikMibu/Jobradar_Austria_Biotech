"""location_text → sites.site_id: LLM-Normalisierung auf eine Stadt, gecacht.

Läuft unbeaufsichtigt (wie extract/score), anders als der manuelle, mensch-geprüfte
companies.yaml-Flow. Geokodiert selbst nichts — legt nur generische Sites mit
address_text an, die companies.geocode_missing() unverändert aufgreift."""

import re
import sqlite3
from datetime import UTC, datetime

from pydantic import BaseModel
from rapidfuzz import fuzz

from . import llm
from .extract import Extraction
from .normalize import norm_text

# v2: deterministischer Vorpass + Exonym-/Mehrfachort-Regeln. v1 ließ qwen3:8b
# "Vienna, Austria" / "GRAZ, Austria" auf null laufen und warf Mehrfachorte weg.
# v3: in_austria-Signal — city=None war bisher zweideutig ("unklar" vs. "eindeutig
# Ausland"); match.py braucht die Unterscheidung, um Auslands-Treffer (XING-Städte-
# Suche zieht auch Hamburg/München/Zürich mit) hart abzulehnen statt nur zu flaggen.
# v4: Auslands-Städte behalten ihren Namen (city="Hamburg", in_austria=false) statt
# null — sie sollen auf der Karte erscheinen, nur ohne Transitous-Fahrzeiten.
LOCATION_SCHEMA_VERSION = 4
COMPANY_SITE_MATCH_THRESHOLD = 85

_EXONYMS = {"vienna": "Wien"}
# Reine Länder-/Bundesland-Teile (Wien und Salzburg fehlen bewusst: auch Städte)
_DROP_PARTS = {
    "austria", "österreich", "at", "aut", "österreichweit",
    "upper austria", "oberösterreich", "oö",
    "lower austria", "niederösterreich", "nö", "n",
    "styria", "steiermark", "stmk",
    "carinthia", "kärnten", "ktn",
    "tyrol", "tirol", "t",
    "vorarlberg", "vbg",
    "burgenland", "bgld",
}
_REMOTE_RE = re.compile(r"home.?office|remote|hybrid|mobil", re.I)
_CITY_RE = re.compile(r"[A-Za-zÄÖÜäöüß.\- ]{2,40}")


def _static_city(location_text: str) -> str | None:
    """Deterministischer Vorpass: "Graz, Styria, Austria" → "Graz" ohne LLM-Call.

    Greift nur, wenn nach dem Entfernen von Länder-/Bundesland-Teilen genau ein
    Ortsname übrig bleibt UND mindestens ein Teil entfernt wurde (das belegt den
    Österreich-Kontext — ein nacktes Einzelwort geht weiter ans LLM)."""
    if _REMOTE_RE.search(location_text):
        return None
    parts = [p.strip() for p in location_text.split(",") if p.strip()]
    kept: list[str] = []
    for p in parts:
        if p.lower() in _DROP_PARTS:
            continue
        if p.lower() not in {k.lower() for k in kept}:
            kept.append(p)
    if len(kept) != 1 or len(kept) == len(parts):
        return None
    city = _EXONYMS.get(kept[0].lower(), kept[0])
    if not _CITY_RE.fullmatch(city):
        return None
    return city.title() if city.isupper() else city


class LocationResolution(BaseModel):
    city: str | None
    in_austria: bool = True


SYSTEM_PROMPT = """Du bekommst einen freien Standort-Text aus einer Stellenanzeige
(meist Österreich, teils DACH) und reduzierst ihn auf eine einzelne kanonische Stadt.

Regeln:
- Gib den offiziellen Namen der Stadt/Gemeinde zurück, ohne PLZ (z. B. "Klosterneuburg"
  statt "3400 Klosterneuburg", "Wien" statt "1010 Wien").
- Bei "Raum <Stadt>", "Großraum <Stadt>", "bei <Stadt>", "Homeoffice möglich, Raum <Stadt>"
  gib <Stadt> zurück.
- Englische Ortsnamen ins Deutsche übersetzen: "Vienna" → "Wien". "Vienna, Austria",
  "Wien, Austria", "Graz, Styria, Austria" sind Orte IN Österreich, nicht außerhalb.
- Bei mehreren möglichen Orten in Österreich (z. B. "Wien oder Graz", "Linz, Wels, Salzburg")
  gib Wien zurück, wenn Wien darunter ist, sonst den zuerst genannten Ort.
- Bei reinem Homeoffice/Remote ohne konkrete Ortsangabe gib city=null, in_austria=true zurück
  (unklar, nicht ausgeschlossen).
- in_austria=false, wenn der Ort erkennbar außerhalb Österreichs liegt — auch ohne
  Landesangabe im Text, wenn es sich um eine bekannte Stadt in einem anderen Land handelt
  (z. B. "Hamburg", "München", "Berlin", "Zürich", "Visp", "Frankfurt am Main").
  Die Stadt trotzdem in city zurückgeben (für die Karte), z. B. city="Hamburg".
- Bei leerem, generischem ("Österreich", "diverse Standorte") oder sonst nicht auflösbarem
  Text: city=null, in_austria=true (im Zweifel nicht ausschließen).
- Erfinde nichts."""


def _cache_get(conn: sqlite3.Connection, key: str) -> tuple[bool, str | None, bool]:
    """(hit, city, in_austria). hit=False: noch nicht (in aktueller Version) aufgelöst."""
    row = conn.execute(
        "SELECT city, in_austria FROM location_cache WHERE location_key=? AND schema_version=?",
        (key, LOCATION_SCHEMA_VERSION),
    ).fetchone()
    return (True, row["city"], bool(row["in_austria"])) if row else (False, None, True)


def _cache_put(
    conn: sqlite3.Connection,
    key: str,
    raw: str,
    city: str | None,
    in_austria: bool,
    model: str | None = None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO location_cache
           (location_key, location_text_raw, city, in_austria, schema_version, model, resolved_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            key,
            raw,
            city,
            int(in_austria),
            LOCATION_SCHEMA_VERSION,
            model or llm.EXTRACT_MODEL,
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


def _resolve(conn: sqlite3.Connection, location_text: str) -> tuple[str | None, bool]:
    """(city, in_austria), gecacht über den normalisierten Text.

    in_austria=False heißt: erkennbar Ausland (z. B. XING-Stadtsuche zieht auch
    deutsche/schweizer Städte mit) — match.py nutzt das für einen harten Ausschluss,
    statt nur zu flaggen. city=None + in_austria=True heißt: unklar/unaufgelöst."""
    key = norm_text(location_text)
    if not key:
        return None, True
    hit, cached_city, cached_austria = _cache_get(conn, key)
    if hit:
        return cached_city, cached_austria
    city = _static_city(location_text)
    if city is not None:
        _cache_put(conn, key, location_text, city, True, model="static")
        return city, True
    result = llm.parse_structured(SYSTEM_PROMPT, location_text, LocationResolution, max_tokens=100)
    city = _clean_city(result.city)
    _cache_put(conn, key, location_text, city, result.in_austria)
    return city, result.in_austria


def resolve_city(conn: sqlite3.Connection, location_text: str) -> str | None:
    """Kanonische Stadt für einen freien Standort-Text, gecacht über den normalisierten Text."""
    return _resolve(conn, location_text)[0]


def is_in_austria(conn: sqlite3.Connection, location_text: str) -> bool:
    """False nur bei erkanntem Auslandsstandort — match.py's harter Ausschlussgrund."""
    return _resolve(conn, location_text)[1]


def _match_company_site(conn: sqlite3.Connection, company_id: int, city: str) -> int | None:
    """Kuratierte Firmen-Site bevorzugen (SPEC: Werk ≠ Firmensitz), sonst None."""
    target = norm_text(city)
    best_id, best_score = None, 0.0
    for row in conn.execute("SELECT id, label FROM sites WHERE company_id=?", (company_id,)):
        score = fuzz.partial_ratio(target, norm_text(row["label"]))
        if score > best_score:
            best_id, best_score = row["id"], score
    return best_id if best_score >= COMPANY_SITE_MATCH_THRESHOLD else None


def _get_or_create_generic_site(conn: sqlite3.Connection, city: str, in_austria: bool = True) -> int:
    # Auslands-Sites ohne ", Österreich"-Suffix, sonst geokodiert Nominatim ins Leere
    conn.execute(
        """INSERT INTO sites (company_id, label, address_text, is_hq, in_austria)
           VALUES (NULL, ?, ?, 0, ?)
           ON CONFLICT(label) WHERE company_id IS NULL DO NOTHING""",
        (city, f"{city}, Österreich" if in_austria else city, int(in_austria)),
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
        city, in_austria = _resolve(conn, ex.location_text)
        if not city:
            continue
        site_id = (
            _match_company_site(conn, row["company_id"], city) if row["company_id"] else None
        )
        if site_id is None:
            site_id = _get_or_create_generic_site(conn, city, in_austria)
        conn.execute("UPDATE postings SET site_id=? WHERE id=?", (site_id, row["id"]))
        conn.commit()
        done += 1
    return done
