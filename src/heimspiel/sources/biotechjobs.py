"""biotechjobs.at-Adapter: server-gerendertes HTML, Jobkarten in .row.row-arrow,
Detailseiten tragen ein schema.org-JobPosting (Ort, Firma, Datum) plus wenig Text."""

import json
import re
import time

import requests
from bs4 import BeautifulSoup

from .base import USER_AGENT, RawPosting

SEARCH_URL = "https://www.biotechjobs.at/search.php"
REQUEST_DELAY_S = 1.5
_ID_RE = re.compile(r"biotechjobs\.at/(\d+)/")


def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for row in soup.select("div.row.row-arrow"):
        link = next(
            (a["href"] for a in row.select("a[href]") if _ID_RE.search(a.get("href", ""))), None
        )
        if not link:
            continue
        job_id = _ID_RE.search(link).group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        middle = row.select_one(".column-middle")
        title = middle.select_one("h1") if middle else None
        company = middle.select_one("h3") if middle else None
        info = middle.select_one(".job-info") if middle else None
        location = None
        if info:
            location = info.get_text(" ", strip=True).split("|")[0].strip() or None
        jobs.append(
            {
                "id": job_id,
                "url": link,
                "title": title.get_text(strip=True) if title else "",
                "company": company.get_text(strip=True) if company else None,
                "location": location,
            }
        )
    return jobs


def parse_detail(html: str) -> dict:
    """JSON-LD JobPosting + sichtbarer Text der Detailseite."""
    out: dict = {}
    m = re.search(r'<script type="application/ld\+json">\s*(\{.*?\})\s*</script>', html, re.S)
    if m:
        try:
            d = json.loads(m.group(1))
            if d.get("@type") == "JobPosting":
                addr = (d.get("jobLocation") or {}).get("address") or {}
                out["location"] = addr.get("addressLocality") or addr.get("addressRegion")
                out["company"] = (d.get("hiringOrganization") or {}).get("name")
                desc = re.sub(r"<[^>]+>", " ", d.get("description") or "")
                out["description"] = " ".join(desc.split()) or None
        except json.JSONDecodeError:
            pass
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one(".column-middle") or soup.body
    if body:
        text = body.get_text("\n", strip=True)
        if len(text) > len(out.get("description") or ""):
            out["description"] = text
    return out


def parse_employer_directory(html: str) -> list[dict]:
    """Arbeitgeber-Verzeichnis als Seed für companies.yaml (SPEC §3)."""
    soup = BeautifulSoup(html, "html.parser")
    companies = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "company" in href or "arbeitgeber" in href:
            name = a.get_text(strip=True)
            if name and len(name) > 2:
                companies.append({"name": name, "url": href})
    return companies


def fetch() -> list[RawPosting]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    resp = session.get(SEARCH_URL, timeout=30)
    resp.raise_for_status()
    jobs = parse_search(resp.text)
    postings = []
    for j in jobs:
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
                source="biotechjobs",
                source_id=j["id"],
                url=j["url"],
                title=j["title"],
                company=j["company"] or detail.get("company"),
                location=j["location"] or detail.get("location"),
                text=detail.get("description"),
            )
        )
    return postings
