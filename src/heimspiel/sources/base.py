"""Gemeinsamer Posting-Typ und Speicherung mit (source, source_id)-Idempotenz."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ..normalize import content_hash

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass
class RawPosting:
    source: str
    source_id: str
    url: str | None
    title: str
    company: str | None
    location: str | None
    text: str | None


def store_postings(conn: sqlite3.Connection, postings: list[RawPosting]) -> int:
    """Schreibt Postings in postings_raw. Bekannte (source, source_id) bekommen nur
    ein frisches last_seen (und längeren Text, falls vorhanden). Rückgabe: Anzahl neuer Zeilen."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    new = 0
    for p in postings:
        row = conn.execute(
            "SELECT id, raw_text FROM postings_raw WHERE source=? AND source_id=?",
            (p.source, p.source_id),
        ).fetchone()
        if row:
            longer = p.text if p.text and len(p.text) > len(row["raw_text"] or "") else None
            if longer:
                conn.execute(
                    "UPDATE postings_raw SET last_seen=?, raw_text=? WHERE id=?",
                    (now, longer, row["id"]),
                )
            else:
                conn.execute("UPDATE postings_raw SET last_seen=? WHERE id=?", (now, row["id"]))
        else:
            conn.execute(
                """INSERT INTO postings_raw
                   (source, source_id, url, first_seen, last_seen,
                    raw_title, raw_company, raw_location, raw_text, content_hash)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    p.source,
                    p.source_id,
                    p.url,
                    now,
                    now,
                    p.title,
                    p.company,
                    p.location,
                    p.text,
                    content_hash(p.title, p.company, p.location),
                ),
            )
            new += 1
    conn.commit()
    return new
