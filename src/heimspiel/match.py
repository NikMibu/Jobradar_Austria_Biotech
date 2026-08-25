"""Profil-Matching (SPEC §6): harte Filter (kein LLM), LLM-Score nur für hard_pass,
Initiativ-Score pro Firma (kein LLM)."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import yaml
from pydantic import BaseModel, Field

from . import llm
from .config import Profile
from .extract import Extraction

MAX_YEARS_EXPERIENCE = 3
SHORT_CONTRACT_MONTHS = 12


@dataclass
class HardResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)  # Ausschlussgründe
    flags: list[str] = field(default_factory=list)  # Markierungen, kein Ausschluss


def site_travel_ok(
    conn: sqlite3.Connection, site_id: int | None, profile: Profile
) -> bool | None:
    """True/False = Fahrzeit bekannt und (nicht) im Limit; None = unbekannt."""
    if site_id is None:
        return None
    rows = conn.execute(
        "SELECT anchor_id, minutes FROM travel_times WHERE site_id=? AND minutes IS NOT NULL",
        (site_id,),
    ).fetchall()
    if not rows:
        return None
    limits = {a.id: a.max_minutes for a in profile.anchors}
    return any(r["minutes"] <= limits.get(r["anchor_id"], 0) for r in rows)


def hard_filter(
    ex: Extraction, profile: Profile, travel_ok: bool | None = None
) -> HardResult:
    """Die fünf Regeln aus SPEC §6, in Reihenfolge."""
    res = HardResult(passed=True)
    if ex.phd_required and not profile.phd_wanted:
        res.passed = False
        res.reasons.append("PhD erforderlich")
    if ex.seniority == "senior" or (
        ex.years_experience_min is not None and ex.years_experience_min > MAX_YEARS_EXPERIENCE
    ):
        res.passed = False
        res.reasons.append(f"Seniorität: {ex.seniority}, {ex.years_experience_min or '?'} Jahre")
    if ex.role_family not in profile.role_families_allowed:
        res.passed = False
        res.reasons.append(f"Rollenfamilie {ex.role_family} nicht erlaubt")
    if travel_ok is False:
        res.passed = False
        res.reasons.append("Kein Anker im Fahrzeit-Limit")
    elif travel_ok is None:
        res.flags.append("Standort/Fahrzeit unbekannt")
    if ex.contract_end:
        try:
            end = date.fromisoformat(ex.contract_end)
            if end < date.today() + timedelta(days=SHORT_CONTRACT_MONTHS * 30):
                res.flags.append(f"Befristung endet {ex.contract_end}")
        except ValueError:
            pass
    return res


class ScoreResult(BaseModel):
    fit_score: int = Field(ge=0, le=100)
    fit_reasons: list[str] = Field(min_length=1, max_length=3)
    gaps: list[str] = Field(max_length=3)
    angle: str


_SCORE_SYSTEM_TEMPLATE = """Du bewertest, wie gut eine Stelle zu diesem Profil passt.

## Profil
{profile_yaml}

## Rubrik (fit_score 0-100)
- Skill-Fit: Welcher Anteil der must_skills ist durch das Profil belegt?
- Interessen-Fit: Bezug der Stelle zu den interests des Profils.
- Realismus: Passen Ausbildung und geforderte Erfahrungsjahre? Eine Stelle, die formal erreichbar UND inhaltlich spannend ist, scort hoch.

## Output
- fit_reasons: genau 3 kurze Bullets, warum der Score so ausfällt.
- gaps: maximal 3 konkrete Lücken gegenüber den Anforderungen.
- angle: EIN Satz aus der Ich-Perspektive des Bewerbers: "so würde ich mich hier positionieren"."""


def score_system_prompt(profile: Profile) -> str:
    safe = {k: v for k, v in profile.raw.items() if k != "anchors"}
    return _SCORE_SYSTEM_TEMPLATE.format(
        profile_yaml=yaml.safe_dump(safe, allow_unicode=True, sort_keys=True)
    )


def score_one(ex: Extraction, profile: Profile) -> ScoreResult:
    response = llm.client().messages.parse(
        model=llm.EXTRACT_MODEL,
        max_tokens=1500,
        system=[
            {
                "type": "text",
                "text": score_system_prompt(profile),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": ex.model_dump_json(indent=2)}],
        output_format=ScoreResult,
    )
    return response.parsed_output


def score_pending(conn: sqlite3.Connection, profile: Profile, limit: int | None = None) -> int:
    """Scort alle Postings ohne Score für die aktuelle profile_version."""
    rows = conn.execute(
        """SELECT p.id AS posting_id, p.extracted_json, p.site_id
           FROM postings p
           LEFT JOIN scores s ON s.posting_id = p.id AND s.profile_version = ?
           WHERE s.posting_id IS NULL ORDER BY p.id""",
        (profile.profile_version,),
    ).fetchall()
    if limit:
        rows = rows[:limit]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    done = 0
    for row in rows:
        ex = Extraction.model_validate_json(row["extracted_json"])
        hard = hard_filter(ex, profile, site_travel_ok(conn, row["site_id"], profile))
        fit: ScoreResult | None = None
        if hard.passed:
            try:
                fit = score_one(ex, profile)
            except Exception as e:  # noqa: BLE001
                print(f"  Score fehlgeschlagen für posting {row['posting_id']}: {e}")
                continue
        conn.execute(
            """INSERT OR REPLACE INTO scores
               (posting_id, profile_version, hard_pass, hard_reasons,
                fit_score, fit_reasons, gaps, angle, model, scored_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                row["posting_id"],
                profile.profile_version,
                int(hard.passed),
                json.dumps({"reasons": hard.reasons, "flags": hard.flags}, ensure_ascii=False),
                fit.fit_score if fit else None,
                json.dumps(fit.fit_reasons, ensure_ascii=False) if fit else None,
                json.dumps(fit.gaps, ensure_ascii=False) if fit else None,
                fit.angle if fit else None,
                llm.EXTRACT_MODEL if fit else None,
                now,
            ),
        )
        conn.commit()
        done += 1
    return done


def initiative_scores(conn: sqlite3.Connection, profile: Profile) -> list[dict]:
    """Initiativ-Score pro Firma (SPEC §6):
    relevant_12m × 1.0 + relevant_24m × 0.5 − aktuell offene passende Inserate.
    Nur Firmen mit mindestens einem Standort im Fahrzeit-Limit (oder ohne Fahrzeit-Daten)."""
    now = datetime.now(UTC)
    t12 = (now - timedelta(days=365)).isoformat()
    t24 = (now - timedelta(days=730)).isoformat()
    t_open = (now - timedelta(days=45)).isoformat()
    families = set(profile.role_families_allowed)
    results = []
    for c in conn.execute("SELECT id, name, website, career_url FROM companies").fetchall():
        rows = conn.execute(
            """SELECT p.extracted_json, r.first_seen, r.last_seen
               FROM postings p JOIN postings_raw r ON r.id = p.raw_id
               WHERE p.company_id = ?""",
            (c["id"],),
        ).fetchall()
        rel_12m = rel_24m = open_now = 0
        for row in rows:
            ex = json.loads(row["extracted_json"])
            if ex.get("role_family") not in families:
                continue
            if row["first_seen"] >= t12:
                rel_12m += 1
            elif row["first_seen"] >= t24:
                rel_24m += 1
            if row["last_seen"] >= t_open:
                open_now += 1
        score = rel_12m * 1.0 + rel_24m * 0.5 - open_now
        if score <= 0:
            continue
        sites = conn.execute("SELECT id FROM sites WHERE company_id=?", (c["id"],)).fetchall()
        oks = [site_travel_ok(conn, s["id"], profile) for s in sites]
        if oks and all(ok is False for ok in oks):
            continue
        results.append(
            {
                "company_id": c["id"],
                "name": c["name"],
                "website": c["website"],
                "career_url": c["career_url"],
                "initiative_score": round(score, 1),
                "relevant_12m": rel_12m,
                "relevant_24m": rel_24m,
                "open_now": open_now,
                "summary": (
                    f"hat in 12 Monaten {rel_12m}× relevant gesucht"
                    + (f", davor {rel_24m}× " if rel_24m else "")
                    + (", aktuell nichts Passendes offen" if open_now == 0 else f", {open_now} offen")
                    + " → Initiativbewerbung"
                ),
            }
        )
    return sorted(results, key=lambda r: -r["initiative_score"])
