"""AITHYRA (ÖAW-Institut für KI in der Biomedizin, Wien): offene Stellen inkl. PhD-Call.

Die Liste /about/open-positions verlinkt TYPO3-News-Detailseiten (tx_news, stabile
`news`-ID in der Query). Der jährliche International PhD Call erscheint dort als
eigene News (2026: news 78), die Programmseite /about/phd-program wäre ein Duplikat."""

import re
import time
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .base import USER_AGENT, RawPosting

BASE = "https://www.aithyra.at"
LIST_URL = f"{BASE}/about/open-positions"
COMPANY = "AITHYRA"
LOCATION = "Wien"
REQUEST_DELAY_S = 1.5


def parse_list(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for a in soup.select("a[href*='open-positions/detail']"):
        url = urljoin(BASE, a["href"])
        news_id = parse_qs(urlparse(url).query).get("tx_news_pi1[news]", [None])[0]
        if not news_id or news_id in seen:
            continue
        seen.add(news_id)
        jobs.append({"id": f"news-{news_id}", "url": url})
    return jobs


def parse_detail(html: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else None
    text = None
    try:
        import trafilatura

        text = trafilatura.extract(html)
    except ImportError:
        pass
    if not text:
        main = soup.find("main") or soup.body
        text = main.get_text("\n", strip=True)[:20000] if main else None
    return title, text


def fetch() -> list[RawPosting]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = session.get(LIST_URL, timeout=30)
    resp.raise_for_status()
    postings: list[RawPosting] = []
    for job in parse_list(resp.text):
        time.sleep(REQUEST_DELAY_S)
        try:
            detail = session.get(job["url"], timeout=30)
            detail.raise_for_status()
        except requests.RequestException as e:
            print(f"  aithyra {job['id']}: {e}", flush=True)
            continue
        title, text = parse_detail(detail.text)
        postings.append(
            RawPosting(
                source="aithyra",
                source_id=job["id"],
                url=job["url"],
                title=title or "AITHYRA Position",
                company=COMPANY,
                location=LOCATION,
                text=text,
            )
        )
    return postings
