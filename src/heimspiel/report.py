"""Markdown-Tagesreport: Top 10 neu, Initiativ-Top 5 (SPEC §9)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from .config import Profile
from .match import initiative_scores


def daily_report(conn: sqlite3.Connection, profile: Profile, days: int = 1) -> str:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT p.id, p.extracted_json, r.url, r.raw_company, r.first_seen,
                  s.fit_score, s.fit_reasons, s.gaps, s.angle, s.hard_reasons
           FROM postings p
           JOIN postings_raw r ON r.id = p.raw_id
           JOIN scores s ON s.posting_id = p.id AND s.profile_version = ?
           WHERE s.hard_pass = 1 AND r.first_seen >= ?
           ORDER BY s.fit_score DESC NULLS LAST
           LIMIT 10""",
        (profile.profile_version, cutoff),
    ).fetchall()

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# Heimspiel — Tagesreport {today}", ""]
    if not rows:
        lines.append(f"Keine neuen passenden Inserate in den letzten {days} Tag(en).")
    else:
        lines.append(f"## Top {len(rows)} neu")
        lines.append("")
        for row in rows:
            ex = json.loads(row["extracted_json"])
            reasons = json.loads(row["fit_reasons"]) if row["fit_reasons"] else []
            gaps = json.loads(row["gaps"]) if row["gaps"] else []
            score = row["fit_score"] if row["fit_score"] is not None else "–"
            lines.append(f"### {score}/100 — {ex['title_norm']} @ {row['raw_company']}")
            lines.append(f"{ex.get('location_text', '')} · [{row['url']}]({row['url']})")
            lines.append("")
            lines.append(ex.get("summary_2_lines", ""))
            for r in reasons:
                lines.append(f"- {r}")
            if gaps:
                lines.append(f"- **Lücken:** {'; '.join(gaps)}")
            if row["angle"]:
                lines.append(f"- **Angle:** {row['angle']}")
            lines.append("")

    top_initiative = initiative_scores(conn, profile)[:5]
    if top_initiative:
        lines.append("## Initiativ-Top 5")
        lines.append("")
        for c in top_initiative:
            lines.append(f"- **{c['name']}** ({c['initiative_score']}): {c['summary']}")
        lines.append("")
    return "\n".join(lines)
