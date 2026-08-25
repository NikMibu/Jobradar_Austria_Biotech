"""Vienna BioCenter: eine Seite mit allen Campus-Instituten und -Firmen.
Grid-Items unter .open-vacancies__grid-item mit data-organisation, Link, Datum, Titel.
Die Links zeigen auf externe ATS-Seiten oder PDFs — Text-Nachladen ist Best-Effort."""

import hashlib
import time

import requests
from bs4 import BeautifulSoup

from .base import USER_AGENT, RawPosting

# SPEC nennt /career/open-positions/, das ist inzwischen eine 404 — die Liste lebt hier:
URL = "https://www.viennabiocenter.org/career/open-vacancies/"
REQUEST_DELAY_S = 1.5


def parse_positions(html: str) -> list[dict]:
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for item in soup.select(".open-vacancies__grid-item .item"):
        a = item.select_one("a[href]")
        title = item.select_one(".title")
        if not a or not title:
            continue
        t = item.select_one("time")
        jobs.append(
            {
                # PDF-Links der Seite sind relativ (/fileadmin/…) → absolut machen,
                # sonst wirft new URL() im Frontend und der Drawer bleibt leer
                "url": urljoin(URL, a["href"]),
                "title": title.get_text(strip=True),
                "organisation": item.get("data-organisation"),
                "date": t.get("datetime") if t else None,
            }
        )
    return jobs


def _fetch_text(session: requests.Session, url: str) -> str | None:
    if url.lower().endswith(".pdf"):
        return None
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    if "html" not in resp.headers.get("content-type", "html"):
        return None
    try:
        import trafilatura

        text = trafilatura.extract(resp.text)
        if text:
            return text
    except ImportError:
        pass
    body = BeautifulSoup(resp.text, "html.parser").body
    return body.get_text("\n", strip=True)[:20000] if body else None


def fetch() -> list[RawPosting]:
    import typer

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = session.get(URL, timeout=30)
    resp.raise_for_status()
    postings = []
    with typer.progressbar(
        parse_positions(resp.text), label="  Positionen", show_pos=True
    ) as bar:
        for j in bar:
            time.sleep(REQUEST_DELAY_S)
            text = _fetch_text(session, j["url"])
            source_id = hashlib.sha256(f"{j['url']}|{j['title']}".encode()).hexdigest()[:16]
            postings.append(
                RawPosting(
                    source="vbc",
                    source_id=source_id,
                    url=j["url"],
                    title=j["title"],
                    company=j["organisation"],
                    location="Wien, Vienna BioCenter",
                    text=text,
                )
            )
    return postings
