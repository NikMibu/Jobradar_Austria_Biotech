"""Normalisierung, Content-Hash, Dedup über Quellen, Company-Matching (SPEC §4)."""

import hashlib
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta

from rapidfuzz import fuzz

FUZZ_THRESHOLD = 92
DEDUP_WINDOW_DAYS = 60
COMPANY_MATCH_THRESHOLD = 90

# (m/w/d)-Varianten, Gender-Sternchen etc. tragen keine Information für den Vergleich
_GENDER_RE = re.compile(r"\((?:[mwfdx]\s*[/|,]\s*)+[mwfdx]\)", re.I)
_NOISE_RE = re.compile(r"[^a-z0-9äöüß ]+")
_LEGAL_RE = re.compile(
    r"\b(gmbh|ag|kg|og|se|co|inc|ltd|holding|austria|österreich|deutschland)\b\.?", re.I
)


def norm_text(s: str | None) -> str:
    # Adapter liefern gelegentlich Nicht-Strings (z. B. NaN aus pandas) — nie crashen
    if not s or not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = _GENDER_RE.sub(" ", s)
    s = _NOISE_RE.sub(" ", s)
    return " ".join(s.split())


def norm_company(s: str | None) -> str:
    return " ".join(_LEGAL_RE.sub(" ", norm_text(s)).split())


def content_hash(title: str | None, company: str | None, location: str | None) -> str:
    key = "|".join([norm_text(title), norm_company(company), norm_text(location)])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def dedup(conn: sqlite3.Connection) -> int:
    """Markiert Duplikate über Quellen: gleiche (normalisierte) Firma, Titel-Ähnlichkeit
    token_set_ratio >= 92, innerhalb von 60 Tagen. Gewinner = längster Text; ein bereits
    extrahierter Kanon bleibt Kanon, damit Postings nicht neu extrahiert werden müssen."""
    cutoff = (datetime.now(UTC) - timedelta(days=DEDUP_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        """SELECT r.id, r.raw_title, r.raw_company, r.raw_text, r.first_seen,
                  EXISTS(SELECT 1 FROM postings p WHERE p.raw_id = r.id) AS extracted
           FROM postings_raw r
           WHERE r.duplicate_of IS NULL AND r.first_seen >= ?
           ORDER BY r.first_seen, r.id""",
        (cutoff,),
    ).fetchall()

    by_company: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_company.setdefault(norm_company(r["raw_company"]), []).append(r)

    marked = 0
    for company, group in by_company.items():
        if not company or len(group) < 2:
            continue
        canonical: list[dict] = []
        for r in group:
            r = dict(r)
            title = norm_text(r["raw_title"])
            match = next(
                (c for c in canonical if fuzz.token_set_ratio(title, c["_title"]) >= FUZZ_THRESHOLD),
                None,
            )
            if match is None:
                r["_title"] = title
                canonical.append(r)
                continue
            new_longer = len(r["raw_text"] or "") > len(match["raw_text"] or "")
            if new_longer and not match["extracted"]:
                conn.execute(
                    "UPDATE postings_raw SET duplicate_of=? WHERE id=?", (r["id"], match["id"])
                )
                canonical[canonical.index(match)] = {**r, "_title": title}
            else:
                conn.execute(
                    "UPDATE postings_raw SET duplicate_of=? WHERE id=?", (match["id"], r["id"])
                )
            marked += 1
    conn.commit()
    return marked


def match_company(conn: sqlite3.Connection, raw_company: str | None) -> int | None:
    """Fuzzy-Match des Inserats-Firmennamens gegen die companies-Tabelle."""
    if not raw_company:
        return None
    target = norm_company(raw_company)
    if not target:
        return None
    best_id, best_score = None, 0.0
    for row in conn.execute("SELECT id, name FROM companies"):
        score = fuzz.token_set_ratio(target, norm_company(row["name"]))
        if score > best_score:
            best_id, best_score = row["id"], score
    return best_id if best_score >= COMPANY_MATCH_THRESHOLD else None
