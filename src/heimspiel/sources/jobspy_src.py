"""JobSpy-Adapter: Indeed, LinkedIn, Google Jobs (SPEC §3).

LinkedIn limitiert pro IP → max. 6 LinkedIn-Queries/Tag mit 15 s Pause;
Indeed und Google tragen die Breite. Braucht das Extra `scrape` (python-jobspy).
"""

import math
import time
from typing import Any

from .base import RawPosting

LINKEDIN_MAX_QUERIES = 6
LINKEDIN_PAUSE_S = 15


def _rows_to_postings(rows: list[dict[str, Any]]) -> list[RawPosting]:
    postings = []
    for r in rows:
        job_id = r.get("id") or r.get("job_url") or ""
        title = r.get("title") or ""
        if not job_id or not title:
            continue
        text = r.get("description") or None
        if isinstance(text, float) and math.isnan(text):
            text = None
        postings.append(
            RawPosting(
                source=f"jobspy_{r.get('site', 'unknown')}",
                source_id=str(job_id),
                url=r.get("job_url"),
                title=title,
                company=r.get("company"),
                location=r.get("location"),
                text=text,
            )
        )
    return postings


def fetch(terms: list[str], hours_old: int = 24, results_wanted: int = 100) -> list[RawPosting]:
    try:
        from jobspy import scrape_jobs
    except ImportError as e:
        raise RuntimeError(
            "python-jobspy fehlt. Installieren mit: uv sync --extra scrape"
        ) from e

    postings: list[RawPosting] = []
    for i, term in enumerate(terms):
        sites = ["indeed", "google"]
        if i < LINKEDIN_MAX_QUERIES:
            sites.append("linkedin")
        try:
            df = scrape_jobs(
                site_name=sites,
                search_term=term,
                google_search_term=f"{term} jobs Österreich",
                location="Austria",
                country_indeed="Austria",
                hours_old=hours_old,
                results_wanted=results_wanted,
                description_format="markdown",
                linkedin_fetch_description=True,
            )
        except Exception as e:  # noqa: BLE001 — eine geblockte Query darf den Lauf nicht beenden
            print(f"  jobspy '{term}' fehlgeschlagen: {e}")
            continue
        postings.extend(_rows_to_postings(df.to_dict("records")))
        if "linkedin" in sites and i + 1 < len(terms):
            time.sleep(LINKEDIN_PAUSE_S)
    return postings
