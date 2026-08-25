"""Export für die Static Site: jobs.json, companies.json, meta.json (SPEC §8)."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from . import paths
from .config import Profile
from .match import initiative_scores

DATA_SCHEMA_VERSION = 2


def _split_job(job: dict) -> tuple[dict, dict]:
    """Split a full export row into startup data and drawer-only details."""
    ex = job["extraction"]
    summary = {
        key: job[key]
        for key in (
            "id",
            "title",
            "company",
            "source",
            "first_seen",
            "location_text",
            "lat",
            "lon",
            "site_label",
            "hard_pass",
            "hard_reasons",
            "fit_score",
            "travel",
        )
    }
    summary.update(
        {
            "role_family": ex.get("role_family"),
            "workplace_mode": ex.get("workplace_mode"),
            "contract_type": ex.get("contract_type"),
            "salary_min_eur_month": ex.get("salary_min_eur_month"),
            "application_deadline": ex.get("application_deadline"),
        }
    )
    details = {
        key: job[key]
        for key in (
            "url",
            "alt_urls",
            "last_seen",
            "extraction",
            "fit_reasons",
            "gaps",
            "angle",
        )
    }
    return summary, details


def _job_rows(conn: sqlite3.Connection, profile: Profile) -> list[dict]:
    rows = conn.execute(
        """SELECT p.id AS posting_id, p.extracted_json, p.company_id, p.site_id,
                  r.source, r.url, r.first_seen, r.last_seen,
                  r.raw_title, r.raw_company, r.raw_location,
                  s.hard_pass, s.hard_reasons, s.fit_score, s.fit_reasons, s.gaps, s.angle,
                  c.name AS company_name,
                  st.lat, st.lon, st.label AS site_label
           FROM postings p
           JOIN postings_raw r ON r.id = p.raw_id
           LEFT JOIN scores s ON s.posting_id = p.id AND s.profile_version = ?
           LEFT JOIN companies c ON c.id = p.company_id
           LEFT JOIN sites st ON st.id = p.site_id
           ORDER BY r.first_seen DESC""",
        (profile.profile_version,),
    ).fetchall()

    jobs = []
    for row in rows:
        ex = json.loads(row["extracted_json"])
        travel = {
            t["anchor_id"]: {"minutes": t["minutes"], "transfers": t["transfers"]}
            for t in conn.execute(
                "SELECT anchor_id, minutes, transfers FROM travel_times WHERE site_id=?",
                (row["site_id"],),
            ).fetchall()
        } if row["site_id"] else {}
        # Duplikat-URLs derselben Stelle aus anderen Quellen einsammeln
        alt = [
            d["url"]
            for d in conn.execute(
                "SELECT url FROM postings_raw WHERE duplicate_of = (SELECT raw_id FROM postings WHERE id=?)",
                (row["posting_id"],),
            ).fetchall()
            if d["url"]
        ]
        jobs.append(
            {
                "id": row["posting_id"],
                "title": ex.get("title_norm") or row["raw_title"],
                "company": row["company_name"] or row["raw_company"],
                "source": row["source"],
                "url": row["url"],
                "alt_urls": alt,
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "location_text": ex.get("location_text") or row["raw_location"],
                "lat": row["lat"],
                "lon": row["lon"],
                "site_label": row["site_label"],
                "extraction": ex,
                "hard_pass": bool(row["hard_pass"]) if row["hard_pass"] is not None else None,
                "hard_reasons": json.loads(row["hard_reasons"]) if row["hard_reasons"] else None,
                "fit_score": row["fit_score"],
                "fit_reasons": json.loads(row["fit_reasons"]) if row["fit_reasons"] else None,
                "gaps": json.loads(row["gaps"]) if row["gaps"] else None,
                "angle": row["angle"],
                "travel": travel,
            }
        )
    return jobs


def export_all(conn: sqlite3.Connection, profile: Profile, out_dir: Path | None = None) -> dict:
    out = out_dir or paths.site_data_dir()
    out.mkdir(parents=True, exist_ok=True)

    full_jobs = _job_rows(conn, profile)
    jobs: list[dict] = []
    job_details: dict[str, dict] = {}
    for full_job in full_jobs:
        summary, details = _split_job(full_job)
        jobs.append(summary)
        job_details[str(full_job["id"])] = details
    companies = initiative_scores(conn, profile)
    for c in companies:
        c["sites"] = [
            dict(s)
            for s in conn.execute(
                "SELECT label, lat, lon, is_hq FROM sites WHERE company_id=?",
                (c["company_id"],),
            ).fetchall()
        ]
    meta = {
        "data_schema_version": DATA_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile_version": profile.profile_version,
        "anchors": [
            {"id": a.id, "label": a.label, "max_minutes": a.max_minutes} for a in profile.anchors
        ],
        "counts": {
            "jobs": len(jobs),
            "hard_pass": sum(1 for j in jobs if j["hard_pass"]),
            "companies_initiative": len(companies),
        },
    }
    outputs = [
        ("jobs.json", jobs),
        ("job-details.json", job_details),
        ("companies.json", companies),
        ("meta.json", meta),
    ]
    for name, data in outputs:
        (out / name).write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return meta
