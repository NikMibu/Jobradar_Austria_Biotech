"""SAP-SuccessFactors-Adapter (Plan Teil 2.2).

GET https://{tenant}/search/?q=&locationsearch=Austria → serverseitig gerenderte
Liste mit /job/{slug}/{id}/-Links (gleiche URL-Form über alle Tenants), Pagination
über &startrow=N. Tenants kuratiert in config/ats.yaml."""

import re
import time

import requests
from bs4 import BeautifulSoup

from .base import USER_AGENT, RawPosting

REQUEST_DELAY_S = 1.5
PAGE_SIZE = 25
MAX_PAGES = 4
_ID_RE = re.compile(r"/job/[^/]*/(\d+)/?$")


def parse_list(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for a in soup.select("a[href*='/job/']"):
        href = a.get("href", "")
        m = _ID_RE.search(href.split("?")[0])
        title = a.get_text(strip=True)
        if not m or not title:
            continue
        job_id = m.group(1)
        if job_id in seen:
            continue
        seen.add(job_id)
        row = a.find_parent("tr") or a.find_parent("li")
        location = None
        if row:
            loc = row.select_one(".jobLocation")
            location = loc.get_text(strip=True) if loc else None
        jobs.append(
            {"id": job_id, "url": base + href if href.startswith("/") else href,
             "title": title, "location": location}
        )
    return jobs


def parse_detail_text(html: str) -> str | None:
    try:
        import trafilatura

        text = trafilatura.extract(html)
        if text:
            return text
    except ImportError:
        pass
    body = BeautifulSoup(html, "html.parser").body
    return body.get_text("\n", strip=True)[:20000] if body else None


def fetch(tenants: dict[str, str]) -> list[RawPosting]:
    """tenants: {host: Arbeitgebername} aus config/ats.yaml."""
    import typer

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    postings: list[RawPosting] = []
    for host, company in tenants.items():
        base = f"https://{host}"
        jobs: list[dict] = []
        for page in range(MAX_PAGES):
            try:
                resp = session.get(
                    f"{base}/search/",
                    params={"q": "", "locationsearch": "Austria", "startrow": page * PAGE_SIZE},
                    timeout=30,
                )
                resp.raise_for_status()
            except requests.RequestException as e:
                print(f"  {host}: {e}", flush=True)
                break
            batch = parse_list(resp.text, base)
            new = [j for j in batch if j["id"] not in {x["id"] for x in jobs}]
            jobs += new
            time.sleep(REQUEST_DELAY_S)
            if not new:
                break
        with typer.progressbar(jobs, label=f"  {host}", show_pos=True) as bar:
            for j in bar:
                time.sleep(REQUEST_DELAY_S)
                text = None
                try:
                    d = session.get(j["url"], timeout=30)
                    d.raise_for_status()
                    text = parse_detail_text(d.text)
                except requests.RequestException:
                    pass
                postings.append(
                    RawPosting(
                        source="successfactors",
                        source_id=f"{host}:{j['id']}",
                        url=j["url"],
                        title=j["title"],
                        company=company,
                        location=j["location"],
                        text=text,
                    )
                )
    return postings
