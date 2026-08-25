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
    """Diagnose-Befunde 2026-08-25 (gegen Live-Indeed/-Google gemessen):
    - Indeed wendet hours_old als harten Serverfilter an → bei Nischen-Termen 0 Treffer.
      Indeed läuft darum OHNE Zeitfilter; die (source, source_id)-Idempotenz macht
      wiedergesehene Inserate ohnehin zu reinen last_seen-Updates.
    - Google Jobs liefert von EU-IPs 0 Ergebnisse ("initial cursor not found", bekannter
      jobspy-Defekt) — auch mit dem offiziellen Query-Format. Deshalb deaktiviert."""
    try:
        from jobspy import scrape_jobs
    except ImportError as e:
        raise RuntimeError(
            "python-jobspy fehlt. Installieren mit: uv sync --extra scrape"
        ) from e

    postings: list[RawPosting] = []
    for i, term in enumerate(terms):
        with_linkedin = i < LINKEDIN_MAX_QUERIES
        label = "indeed" + ("+linkedin" if with_linkedin else "")
        print(f"  [{i + 1}/{len(terms)}] {term} ({label}) …", flush=True)
        batch: list[RawPosting] = []
        try:
            df = scrape_jobs(
                site_name=["indeed"],
                search_term=term,
                location="Austria",
                country_indeed="Austria",
                hours_old=None,
                results_wanted=results_wanted,
                description_format="markdown",
                verbose=0,
            )
            batch += _rows_to_postings(df.to_dict("records"))
        except Exception as e:  # noqa: BLE001 — eine geblockte Query darf den Lauf nicht beenden
            print(f"      indeed fehlgeschlagen: {e}", flush=True)
        if with_linkedin:
            try:
                df = scrape_jobs(
                    site_name=["linkedin"],
                    search_term=term,
                    location="Austria",
                    hours_old=hours_old,
                    results_wanted=results_wanted,
                    description_format="markdown",
                    linkedin_fetch_description=True,
                    verbose=0,
                )
                batch += _rows_to_postings(df.to_dict("records"))
            except Exception as e:  # noqa: BLE001
                print(f"      linkedin fehlgeschlagen: {e}", flush=True)
        postings.extend(batch)
        print(f"      {len(batch)} Treffer", flush=True)
        if with_linkedin and i + 1 < len(terms):
            time.sleep(LINKEDIN_PAUSE_S)
    return postings
