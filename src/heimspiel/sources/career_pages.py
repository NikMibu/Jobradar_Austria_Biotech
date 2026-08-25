"""Firmen-Karriereseiten-Watcher (SPEC §3, wöchentlich).

companies.yaml → fetch → Text → LLM listet offene Positionen → Diff zum letzten
Snapshot. `new`/`closed` füttern die Einstellungs-Historie für den Initiativ-Score;
neue Positionen landen zusätzlich als postings_raw."""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import requests
from pydantic import BaseModel

from .. import llm
from .base import USER_AGENT, RawPosting, store_postings


class Position(BaseModel):
    title: str
    url: str | None = None
    location: str | None = None


class PositionList(BaseModel):
    positions: list[Position]


_SYSTEM = (
    "Du bekommst den Text einer Firmen-Karriereseite. Liste alle offenen Positionen "
    "als (title, url, location). Nur echte Stellenanzeigen, keine Navigation, keine "
    "Benefits-Texte. url und location nur, wenn sie im Text stehen, sonst null."
)


def fetch_page_text(url: str) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    try:
        import trafilatura

        text = trafilatura.extract(resp.text)
        if text:
            return text
    except ImportError:
        pass
    from bs4 import BeautifulSoup

    body = BeautifulSoup(resp.text, "html.parser").body
    return body.get_text("\n", strip=True) if body else None


def extract_positions(page_text: str) -> list[Position]:
    return llm.parse_structured(_SYSTEM, page_text[:40000], PositionList, max_tokens=4000).positions


def diff_positions(old: list[dict], new: list[Position]) -> tuple[list[dict], list[dict]]:
    old_titles = {p["title"] for p in old}
    new_titles = {p.title for p in new}
    added = [p.model_dump() for p in new if p.title not in old_titles]
    closed = [p for p in old if p["title"] not in new_titles]
    return added, closed


def watch_company(conn: sqlite3.Connection, company_id: int, career_url: str) -> int:
    """Ein Firmen-Check: fetch → LLM → Diff → Snapshot + neue postings_raw. Rückgabe: # neu."""
    text = fetch_page_text(career_url)
    if not text:
        return 0
    positions = extract_positions(text)
    prev = conn.execute(
        "SELECT positions_json FROM career_snapshots WHERE company_id=? ORDER BY fetched_at DESC LIMIT 1",
        (company_id,),
    ).fetchone()
    old = json.loads(prev["positions_json"]) if prev else []
    added, closed = diff_positions(old, positions)
    conn.execute(
        "INSERT INTO career_snapshots (company_id, fetched_at, positions_json, diff_new, diff_closed) VALUES (?,?,?,?,?)",
        (
            company_id,
            datetime.now(UTC).isoformat(timespec="seconds"),
            json.dumps([p.model_dump() for p in positions], ensure_ascii=False),
            json.dumps(added, ensure_ascii=False),
            json.dumps(closed, ensure_ascii=False),
        ),
    )
    conn.commit()

    company = conn.execute("SELECT name FROM companies WHERE id=?", (company_id,)).fetchone()
    postings = [
        RawPosting(
            source="career_page",
            source_id=hashlib.sha256(f"{company_id}|{p['title']}".encode()).hexdigest()[:16],
            url=p.get("url") or career_url,
            title=p["title"],
            company=company["name"] if company else None,
            location=p.get("location"),
            text=None,
        )
        for p in added
    ]
    return store_postings(conn, postings)


def watch_all(conn: sqlite3.Connection) -> int:
    total = 0
    for row in conn.execute(
        "SELECT id, name, career_url FROM companies WHERE career_url IS NOT NULL"
    ).fetchall():
        print(f"  Karriereseite: {row['name']}")
        total += watch_company(conn, row["id"], row["career_url"])
    return total
