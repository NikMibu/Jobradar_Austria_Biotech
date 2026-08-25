"""eRecruiter-Adapter — die in Österreich verbreitete ATS-Plattform (Plan Teil 2.1).

GET https://{host}/Jobs liefert die komplette Stellenliste als JSON-Array im HTML
([{"Id":…,"Title":…,"Location":…,"Date":…}]), Detailseiten /Job/{Id} sind
serverseitig gerendert. robots.txt der geprüften Hosts sperrt nur /Login/ und
/Register/. Hosts kuratiert in config/ats.yaml (jobs.{domain} raten schlägt fehl)."""

import json
import re
import time

import requests
from bs4 import BeautifulSoup

from .base import USER_AGENT, RawPosting

REQUEST_DELAY_S = 1.5
_JSON_RE = re.compile(r'\[\{"Id":\d+.*?\}\]')


def parse_jobs_json(html: str) -> list[dict]:
    m = _JSON_RE.search(html)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []


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


def fetch(hosts: dict[str, str]) -> list[RawPosting]:
    """hosts: {host: Arbeitgebername} aus config/ats.yaml."""
    import typer

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    postings: list[RawPosting] = []
    for host, company in hosts.items():
        try:
            resp = session.get(f"https://{host}/Jobs", timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  {host}: {e}", flush=True)
            continue
        jobs = parse_jobs_json(resp.text)
        with typer.progressbar(jobs, label=f"  {host}", show_pos=True) as bar:
            for j in bar:
                time.sleep(REQUEST_DELAY_S)
                url = f"https://{host}/Job/{j['Id']}"
                text = None
                try:
                    d = session.get(url, timeout=30)
                    d.raise_for_status()
                    text = parse_detail_text(d.text)
                except requests.RequestException:
                    pass
                subtitle = j.get("SubTitle") or ""
                postings.append(
                    RawPosting(
                        source="erecruiter",
                        source_id=f"{host}:{j['Id']}",
                        url=url,
                        title=j.get("Title", ""),
                        company=company,
                        location=j.get("Location"),
                        text="\n\n".join(x for x in (subtitle, text) if x) or None,
                    )
                )
    return postings
