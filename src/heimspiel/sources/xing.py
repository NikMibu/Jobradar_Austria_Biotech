"""XING-Jobs-Adapter (SPEC §1 nannte XING für v0.2, „nur wenn Google Jobs Lücken
zeigt" — Google Jobs liefert von EU-IPs 0 Ergebnisse, die Bedingung ist erfüllt).

Technik (live geprüft 2026-08-25):
- Suche `GET /jobs/search?keywords=…&location=<Stadt>` ist serverseitig gerendert
  (20 Karten/Seite, `article[data-testid="job-search-result"]`), kein Login nötig.
  Landesweite Suche (`location=Österreich`) mischt DACH-Ergebnisse → Stadt-Schleife.
- Detailseiten `/jobs/{slug}-{id}` tragen vollständiges schema.org-JobPosting-JSON-LD
  (`<script data-ch type="application/ld+json">`): Titel, Beschreibung, Firma, Ort.

** robots.txt-Hinweis (analog zur EURAXESS-Entscheidung, siehe euraxess.py): **
xing.com sperrt `/jobs/search` für `User-agent: *`, erlaubt es aber ausdrücklich
für AI-Agents (ClaudeBot, Claude-User, GPTBot, PerplexityBot, …); die Detailseiten
`/jobs/{slug}` sind für niemanden gesperrt. XING will erkennbar persönliche
AI-Werkzeuge lesen lassen und Massen-Crawler fernhalten. Dieser Radar ist ein
persönliches AI-Tool mit ~100 Listen-Requests/Tag — er läuft trotzdem bewusst
konservativ: >= 3 s Pause, ehrlicher User-Agent `heimspiel/0.1`, Seite 2 nur bei
voller Trefferseite, Detailabruf nur für neue IDs. Vermerkt in SPEC §3."""

import json
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .base import RawPosting

BASE = "https://www.xing.com"
REQUEST_DELAY_S = 3.0
HONEST_UA = "heimspiel/0.1 (open-source job radar; personal use)"
MAX_PAGES = 2
FULL_PAGE = 20
_ID_RE = re.compile(r"-(\d+)$")
_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S
)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(s: str | None) -> str | None:
    # xing.com fügt &shy;-Soft-Hyphens (U+00AD) für Zeilenumbrüche in Titeln ein
    # ("Da­ta Scien­tists") — unsichtbar/störend außerhalb des eigenen Renderers.
    return s.replace("\xad", "") if s else s


def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for card in soup.select("article[data-testid='job-search-result']"):
        a = card.select_one("a[href^='/jobs/']")
        if not a:
            continue
        href = a["href"].split("?")[0]
        m = _ID_RE.search(href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        title = _clean((a.get("aria-label") or a.get_text(" ", strip=True) or "").strip())
        jobs.append({"id": m.group(1), "url": BASE + href, "title": title})
    return jobs


def parse_detail(html: str) -> dict:
    """schema.org-JobPosting aus der Detailseite: title, text, company, location."""
    for m in _JSONLD_RE.finditer(html):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("@type") != "JobPosting":
            continue
        desc = _TAG_RE.sub("\n", d.get("description") or "")
        desc = re.sub(r"\n{3,}", "\n\n", desc).strip()
        loc = d.get("jobLocation") or {}
        if isinstance(loc, list):
            loc = loc[0] if loc else {}
        addr = loc.get("address") or {}
        out = {
            "title": _clean(d.get("title")),
            "text": _clean(desc) or None,
            "company": _clean((d.get("hiringOrganization") or {}).get("name")),
            "location": _clean(addr.get("addressLocality") or addr.get("addressRegion")),
        }
        if d.get("employmentType"):
            out["text"] = f"Anstellung: {d['employmentType']}\n\n{out['text'] or ''}".strip()
        return out
    return {}


def fetch(
    terms: list[str], locations: list[str], known_ids: set[str] | None = None
) -> list[RawPosting]:
    import typer

    known_ids = known_ids or set()
    session = requests.Session()
    session.headers.update({"User-Agent": HONEST_UA})
    found: dict[str, dict] = {}
    for i, term in enumerate(terms):
        print(f"  [{i + 1}/{len(terms)}] {term} … ({len(found)} bisher)", flush=True)
        for loc in locations:
            for page in range(1, MAX_PAGES + 1):
                url = (
                    f"{BASE}/jobs/search?keywords={quote(term)}&location={quote(loc)}"
                    + (f"&page={page}" if page > 1 else "")
                )
                try:
                    resp = session.get(url, timeout=30)
                    resp.raise_for_status()
                except requests.RequestException:
                    break
                time.sleep(REQUEST_DELAY_S)
                # xing.com liefert UTF-8 ohne Charset im Content-Type-Header — requests
                # rät sonst Latin-1 und erzeugt Mojibake ("für" -> "fÃ¼r").
                resp.encoding = "utf-8"
                batch = [j for j in parse_search(resp.text) if j["id"] not in found]
                for j in batch:
                    found[j["id"]] = j
                if len(batch) < FULL_PAGE:
                    break

    postings: list[RawPosting] = []
    new = [j for j in found.values() if j["id"] not in known_ids]
    # Bekannte IDs ohne Detailabruf durchreichen → store_postings frischt last_seen auf
    for j in found.values():
        if j["id"] in known_ids:
            postings.append(
                RawPosting("xing", j["id"], j["url"], j["title"], None, None, None)
            )
    with typer.progressbar(new, label="  Detailseiten", show_pos=True) as bar:
        for j in bar:
            time.sleep(REQUEST_DELAY_S)
            detail: dict = {}
            try:
                d = session.get(j["url"], timeout=30)
                d.raise_for_status()
                d.encoding = "utf-8"
                detail = parse_detail(d.text)
            except requests.RequestException:
                pass
            postings.append(
                RawPosting(
                    source="xing",
                    source_id=j["id"],
                    url=j["url"],
                    title=detail.get("title") or j["title"],
                    company=detail.get("company"),
                    location=detail.get("location"),
                    text=detail.get("text"),
                )
            )
    return postings
