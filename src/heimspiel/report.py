"""Markdown-Tagesreport: Kopfzeile mit Trichter, Top 10 neu, Grenzfälle, Initiativ-Top 5 (SPEC §9)."""

import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta

from .config import Profile
from .match import initiative_scores

BORDERLINE_MIN_SCORE = 50


def _funnel_stats(conn: sqlite3.Connection, profile: Profile, cutoff: str) -> dict:
    new = conn.execute(
        "SELECT COUNT(*) FROM postings_raw WHERE duplicate_of IS NULL AND first_seen >= ?",
        (cutoff,),
    ).fetchone()[0]
    per_source = conn.execute(
        """SELECT source, COUNT(*) AS n FROM postings_raw
           WHERE duplicate_of IS NULL AND first_seen >= ? GROUP BY source ORDER BY n DESC""",
        (cutoff,),
    ).fetchall()
    rejected = conn.execute(
        """SELECT s.hard_reasons FROM scores s
           JOIN postings p ON p.id = s.posting_id
           JOIN postings_raw r ON r.id = p.raw_id
           WHERE s.profile_version = ? AND s.hard_pass = 0 AND r.first_seen >= ?""",
        (profile.profile_version, cutoff),
    ).fetchall()
    reasons: Counter[str] = Counter()
    for row in rejected:
        try:
            for reason in json.loads(row["hard_reasons"] or "{}").get("reasons", []):
                reasons[reason.split(":")[0]] += 1
        except json.JSONDecodeError:
            pass
    passed = conn.execute(
        """SELECT COUNT(*) FROM scores s
           JOIN postings p ON p.id = s.posting_id
           JOIN postings_raw r ON r.id = p.raw_id
           WHERE s.profile_version = ? AND s.hard_pass = 1 AND r.first_seen >= ?""",
        (profile.profile_version, cutoff),
    ).fetchone()[0]
    return {
        "new": new,
        "per_source": per_source,
        "rejected": len(rejected),
        "reasons": reasons,
        "passed": passed,
    }


def _job_block(row: sqlite3.Row) -> list[str]:
    ex = json.loads(row["extracted_json"])
    reasons = json.loads(row["fit_reasons"]) if row["fit_reasons"] else []
    gaps = json.loads(row["gaps"]) if row["gaps"] else []
    score = row["fit_score"] if row["fit_score"] is not None else "–"
    title = ex.get("title_norm") or row["raw_title"] or "?"
    lines = [
        f"### {score}/100 — {title} @ {row['raw_company'] or '?'}",
        f"{ex.get('location_text') or ''} · [{row['url']}]({row['url']})",
        "",
        ex.get("summary_2_lines") or "",
    ]
    lines += [f"- {r}" for r in reasons]
    if gaps:
        lines.append(f"- **Lücken:** {'; '.join(gaps)}")
    if row["angle"]:
        lines.append(f"- **Angle:** {row['angle']}")
    lines.append("")
    return lines


def daily_report(conn: sqlite3.Connection, profile: Profile, days: int = 1) -> str:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    stats = _funnel_stats(conn, profile, cutoff)

    rows = conn.execute(
        """SELECT p.id, p.extracted_json, r.url, r.raw_title, r.raw_company, r.first_seen,
                  s.fit_score, s.fit_reasons, s.gaps, s.angle, s.hard_reasons
           FROM postings p
           JOIN postings_raw r ON r.id = p.raw_id
           JOIN scores s ON s.posting_id = p.id AND s.profile_version = ?
           WHERE s.hard_pass = 1 AND r.first_seen >= ?
           ORDER BY s.fit_score DESC NULLS LAST""",
        (profile.profile_version, cutoff),
    ).fetchall()
    top = rows[:10]
    borderline = [
        r
        for r in rows[10:]
        if r["fit_score"] is not None and r["fit_score"] >= BORDERLINE_MIN_SCORE
    ][:5]

    today = datetime.now().strftime("%Y-%m-%d")
    src = ", ".join(f"{r['source']} {r['n']}" for r in stats["per_source"]) or "keine"
    lines = [
        f"# Heimspiel — Tagesreport {today}",
        "",
        f"**{stats['new']} neu** ({src}) · {stats['rejected']} gefiltert · **{stats['passed']} Treffer**",
    ]
    # Ein Lauf mit 0 neuen Inseraten aus einer sonst ergiebigen Quelle soll auffallen
    if stats["reasons"]:
        top_reasons = ", ".join(f"{k} ({v})" for k, v in stats["reasons"].most_common(4))
        lines.append(f"Ablehnungsgründe: {top_reasons}")
    lines.append("")

    if not top:
        lines.append(f"Keine neuen passenden Inserate in den letzten {days} Tag(en).")
        lines.append("")
    else:
        lines.append(f"## Top {len(top)} neu")
        lines.append("")
        for row in top:
            lines += _job_block(row)

    if borderline:
        lines.append("## Grenzfälle")
        lines.append("")
        for row in borderline:
            lines += _job_block(row)

    top_initiative = initiative_scores(conn, profile)[:5]
    if top_initiative:
        lines.append("## Initiativ-Top 5")
        lines.append("")
        for c in top_initiative:
            lines.append(f"- **{c['name']}** ({c['initiative_score']}): {c['summary']}")
        lines.append("")
    return "\n".join(lines)
