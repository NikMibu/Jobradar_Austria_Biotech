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


def _clean(v: Any) -> str | None:
    """pandas liefert fehlende Werte als float('nan') — alles Nicht-String wird None."""
    if isinstance(v, str):
        return v.strip() or None
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return str(v)


def _rows_to_postings(rows: list[dict[str, Any]]) -> list[RawPosting]:
    postings = []
    for r in rows:
        job_id = _clean(r.get("id")) or _clean(r.get("job_url"))
        title = _clean(r.get("title"))
        if not job_id or not title:
            continue
        postings.append(
            RawPosting(
                source=f"jobspy_{_clean(r.get('site')) or 'unknown'}",
                source_id=job_id,
                url=_clean(r.get("job_url")),
                title=title,
                company=_clean(r.get("company")),
                location=_clean(r.get("location")),
                text=_clean(r.get("description")),
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
        print(f"  [{i + 1}/{len(terms)}] {term} ({'+'.join(sites)}) …", flush=True)
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
                verbose=0,
            )
        except Exception as e:  # noqa: BLE001 — eine geblockte Query darf den Lauf nicht beenden
            print(f"      fehlgeschlagen: {e}", flush=True)
            continue
        batch = _rows_to_postings(df.to_dict("records"))
        postings.extend(batch)
        print(f"      {len(batch)} Treffer", flush=True)
        if "linkedin" in sites and i + 1 < len(terms):
            time.sleep(LINKEDIN_PAUSE_S)
    return postings
