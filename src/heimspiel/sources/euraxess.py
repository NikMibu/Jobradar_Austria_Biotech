"""EURAXESS-Adapter — akademische Stellen aller österreichischen Unis/Institute (Plan Teil 2.3).

Suche: https://euraxess.ec.europa.eu/jobs/search?f[0]=job_country:791&page=N,
Detailseiten /jobs/{id}, Volltext via trafilatura (kein JSON-LD außer Breadcrumbs).

** robots.txt-Hinweis — bewusste Entscheidung des Betreibers dieses Radars: **
Die robots.txt von euraxess.ec.europa.eu widerspricht sich selbst — sie enthält
sowohl `Allow: /jobs` als auch (nachträglich, ohne abschließenden Zeilenumbruch
hinter die Sitemap-Zeile geklebt) `Disallow: /jobs/*` und `Disallow: /*?`.
Nach üblicher Auswertung (längster Match gewinnt) wären Suche und Detailseiten
gesperrt. Der Adapter läuft trotzdem, weil die Datei offensichtlich schlampig
gepflegt ist, die Nutzung persönlich und die Last minimal ist (~20 Requests/Tag).
Daraus folgen zwei nicht optionale Auflagen:
  1. Konservativster Adapter im Projekt: >= 3 s Pause, kein Parallelabruf,
     harte Seiten-Obergrenze pro Lauf (config/ats.yaml: euraxess_max_pages).
  2. Ehrlicher User-Agent (heimspiel/0.1) statt des Browser-Strings —
     wer blocken will, soll uns erkennen können.
Vermerkt auch in heimspiel_SPEC.md §3."""

import re
import time

import requests
from bs4 import BeautifulSoup

from .base import RawPosting

BASE = "https://euraxess.ec.europa.eu"
SEARCH = f"{BASE}/jobs/search"
COUNTRY_AT = "job_country:791"
REQUEST_DELAY_S = 3.0
HONEST_UA = "heimspiel/0.1 (open-source job radar; personal use)"
_JOB_RE = re.compile(r"^/jobs/(\d+)$")


def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for a in soup.select("a[href^='/jobs/']"):
        m = _JOB_RE.match(a.get("href", "").split("?")[0])
        title = a.get_text(strip=True)
        if not m or not title:
            continue
        job_id = m.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        jobs.append({"id": job_id, "url": f"{BASE}/jobs/{job_id}", "title": title})
    return jobs


def parse_detail(html: str) -> dict:
    out: dict = {}
    soup = BeautifulSoup(html, "html.parser")
    try:
        import trafilatura

        out["text"] = trafilatura.extract(html)
    except ImportError:
        body = soup.body
        out["text"] = body.get_text("\n", strip=True)[:20000] if body else None
    # Institution/Ort stehen in Definition-Lists der Detailseite; Best-Effort
    for dt in soup.select("dt"):
        key = dt.get_text(strip=True).lower()
        dd = dt.find_next_sibling("dd")
        if not dd:
            continue
        if "organisation" in key or "company" in key:
            out.setdefault("company", dd.get_text(strip=True))
        if "city" in key or "location" in key:
            out.setdefault("location", dd.get_text(strip=True))
    return out


def fetch(max_pages: int = 5) -> list[RawPosting]:
    import typer

    session = requests.Session()
    session.headers.update({"User-Agent": HONEST_UA})
    jobs: list[dict] = []
    for page in range(max_pages):
        try:
            resp = session.get(
                SEARCH, params={"f[0]": COUNTRY_AT, "page": page}, timeout=30
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  euraxess Seite {page}: {e}", flush=True)
            break
        batch = [j for j in parse_search(resp.text) if j["id"] not in {x["id"] for x in jobs}]
        jobs += batch
        time.sleep(REQUEST_DELAY_S)
        if not batch:
            break
    postings: list[RawPosting] = []
    with typer.progressbar(jobs, label="  euraxess", show_pos=True) as bar:
        for j in bar:
            time.sleep(REQUEST_DELAY_S)
            detail: dict = {}
            try:
                d = session.get(j["url"], timeout=30)
                d.raise_for_status()
                detail = parse_detail(d.text)
            except requests.RequestException:
                pass
            postings.append(
                RawPosting(
                    source="euraxess",
                    source_id=j["id"],
                    url=j["url"],
                    title=j["title"],
                    company=detail.get("company"),
                    location=detail.get("location", "Österreich"),
                    text=detail.get("text"),
                )
            )
    return postings
