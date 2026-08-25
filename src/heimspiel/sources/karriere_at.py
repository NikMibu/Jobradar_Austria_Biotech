"""karriere.at-Adapter.

Die Seite ist eine SPA, liefert aber mit `Accept: application/json` +
`X-Requested-With: XMLHttpRequest` dieselbe URL als JSON aus:
Liste unter data.jobsSearchList.{active,inactive}Items.items[].jobsItem,
Detailseite unter data.jobDetailContent.schemaOrgJobPosting (JSON-LD JobPosting).
"""

import json
import re
import time
from typing import Any

import requests

from .base import USER_AGENT, RawPosting

BASE = "https://www.karriere.at"
REQUEST_DELAY_S = 2.0

_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9äöüß]+", "-", s.lower()).strip("-")


def parse_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrahiert Job-Items aus der Listen-JSON-Antwort (nur aktive Inserate)."""
    jsl = data.get("data", {}).get("jobsSearchList", {})
    items = jsl.get("activeItems", {}).get("items", [])
    jobs = []
    for item in items:
        ji = item.get("jobsItem")
        if not ji or not ji.get("id"):
            continue
        locations = ji.get("locations") or []
        loc = ", ".join(
            x.get("name", x) if isinstance(x, dict) else str(x) for x in locations
        )
        company = ji.get("company") or {}
        jobs.append(
            {
                "id": str(ji["id"]),
                "url": ji.get("link") or f"{BASE}/jobs/{ji['id']}",
                "title": ji.get("title", ""),
                "company": company.get("name") if isinstance(company, dict) else str(company),
                "location": loc,
                "snippet": ji.get("snippet") or "",
                "salary": ji.get("salary") or "",
            }
        )
    return jobs


def parse_detail(data: dict[str, Any]) -> str | None:
    """Volltext aus dem eingebetteten schema.org-JobPosting der Detail-JSON."""
    raw = data.get("data", {}).get("jobDetailContent", {}).get("schemaOrgJobPosting")
    if not raw:
        return None
    m = re.search(r"<script[^>]*>(.*?)</script>", raw, re.S)
    payload = m.group(1) if m else raw
    try:
        d = json.loads(payload)
    except json.JSONDecodeError:
        return None
    desc = d.get("description") or ""
    text = re.sub(r"<[^>]+>", "\n", desc)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def fetch(terms: list[str], locations: list[str], max_pages: int = 2) -> list[RawPosting]:
    session = requests.Session()
    session.headers.update(_HEADERS)
    postings: list[RawPosting] = []
    seen: set[str] = set()
    for term in terms:
        for loc in locations:
            for page in range(1, max_pages + 1):
                url = f"{BASE}/jobs/{_slug(term)}/{_slug(loc)}"
                try:
                    resp = session.get(url, params={"page": page}, timeout=30)
                    resp.raise_for_status()
                    jobs = parse_list(resp.json())
                except (requests.RequestException, ValueError):
                    break  # geblockt oder kein JSON → Google-Jobs-Abdeckung übernimmt
                time.sleep(REQUEST_DELAY_S)
                if not jobs:
                    break
                for j in jobs:
                    if j["id"] in seen:
                        continue
                    seen.add(j["id"])
                    text = None
                    try:
                        d = session.get(j["url"], timeout=30)
                        d.raise_for_status()
                        text = parse_detail(d.json())
                    except (requests.RequestException, ValueError):
                        pass
                    time.sleep(REQUEST_DELAY_S)
                    parts = [j["snippet"], j["salary"], text or ""]
                    postings.append(
                        RawPosting(
                            source="karriere_at",
                            source_id=j["id"],
                            url=j["url"],
                            title=j["title"],
                            company=j["company"],
                            location=j["location"],
                            text="\n\n".join(p for p in parts if p).strip() or None,
                        )
                    )
    return postings
