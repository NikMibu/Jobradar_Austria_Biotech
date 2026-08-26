"""Profil-Matching (SPEC §6): harte Filter (kein LLM), LLM-Score nur für hard_pass,
Initiativ-Score pro Firma (kein LLM)."""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from . import llm, locations
from .config import Profile
from .extract import SCHEMA_VERSION as EXTRACTION_SCHEMA_VERSION
from .extract import Extraction, Requirement
from .normalize import norm_text

SHORT_CONTRACT_MONTHS = 12
SCORE_VERSION = 2


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
    ex: Extraction, profile: Profile, travel_ok: bool | None = None, in_austria: bool = True
) -> HardResult:
    """Die Regeln aus SPEC §6, in Reihenfolge."""
    res = HardResult(passed=True)
    # Formale Hürden bleiben sichtbar, verhindern aber keinen Fachscore mehr.
    if ex.phd_required and not profile.phd_wanted:
        res.flags.append("PhD erforderlich")
    if ex.seniority not in profile.seniority_allowed or (
        ex.years_experience_min is not None
        and ex.years_experience_min > profile.max_years_experience
    ):
        res.flags.append(f"Seniorität: {ex.seniority}, {ex.years_experience_min or '?'} Jahre")
    if ex.role_family not in profile.role_families_allowed:
        res.passed = False
        res.reasons.append(f"Rollenfamilie {ex.role_family} nicht erlaubt")
    if ex.workplace_mode == "remote":
        pass  # vollständig Remote: Standort/Fahrzeit irrelevant
    elif not in_austria:
        # Ausland (onsite/hybrid): kein Ausschluss, nur Flag — Frontend filtert bei Bedarf
        # (z. B. XING-Stadtsuche zieht Hamburg/München/Zürich mit, kann aber relevant sein).
        res.flags.append("Standort außerhalb Österreichs")
    elif travel_ok is False:
        # Kein Ausschluss mehr (Nutzer-Feedback: Anker sollen nicht hart ausschließen,
        # sonst verschwinden echte Österreich-Stellen einfach aus dem Radar) — nur Flag,
        # Frontend/Filter (Score ≥, Anker ≤ min) blenden bei Bedarf aus.
        res.flags.append("Kein Anker im Fahrzeit-Limit")
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


MatchLevel = Literal["direct", "transferable", "missing", "unknown"]
FitLevel = Literal["strong", "moderate", "weak", "none", "unknown"]
TrafficStatus = Literal["green", "yellow", "red"]


class SkillAssessment(BaseModel):
    requirement: str
    match: MatchLevel
    profile_evidence: str | None = None


class HardNoHit(BaseModel):
    rule: str
    evidence: str


class ScoreAssessment(BaseModel):
    skills: list[SkillAssessment] = Field(default_factory=list)
    domain_fit: FitLevel = "unknown"
    domain_evidence: str = ""
    interest_fit: FitLevel = "unknown"
    interest_evidence: str = ""
    hard_no_hits: list[HardNoHit] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, max_length=3)
    angle: str


_SCORE_SYSTEM_TEMPLATE = """Du erstellst ein evidenzbasiertes fachliches Assessment.

## Profil
{profile_yaml}

## Regeln
- Vergib KEINE Punktzahl. Python berechnet den Score deterministisch.
- Bewerte jede übergebene Anforderung genau einmal als direct, transferable,
  missing oder unknown. direct/transferable nur mit einem kurzen WÖRTLICHEN
  Beleg aus dem Profil; ohne Beleg ist der Wert unknown.
- direct bedeutet dieselbe nachgewiesene Methode, Technologie oder Erfahrung;
  bloß verwandte Themen und Interessen sind höchstens transferable. Ein Interesse
  ist niemals Beleg für praktische Erfahrung. Bei Zweifel unknown.
- domain_fit bewertet die fachliche Nähe der Tätigkeit zum belegten Profil;
  domain_evidence ist ein kurzes WÖRTLICHES Zitat aus der Stelle.
- interest_fit bewertet den Bezug zu den ausdrücklich genannten Interessen;
  interest_evidence ist ein kurzes WÖRTLICHES Zitat aus dem Profil.
- hard_no_hits nur für eine Regel aus hard_no und mit einem konkreten WÖRTLICHEN
  Beleg aus der Stelle; nichts hineininterpretieren.
- gaps: maximal drei konkrete Lücken. angle: ein Satz aus Ich-Perspektive.
- Fehlende Angaben bleiben unknown. Nichts erfinden."""


def score_system_prompt(profile: Profile) -> str:
    safe = {k: v for k, v in profile.raw.items() if k != "anchors"}
    return _SCORE_SYSTEM_TEMPLATE.format(
        profile_yaml=yaml.safe_dump(safe, allow_unicode=True, sort_keys=True)
    )


def _requirements(ex: Extraction) -> list[Requirement]:
    if ex.requirements:
        return ex.requirements
    return [
        Requirement(name=name, importance=importance, evidence="")
        for importance, names in (("must", ex.must_skills), ("nice", ex.nice_skills))
        for name in names
    ]


def _assessment_call(
    ex: Extraction,
    profile: Profile,
    model: str,
    *,
    think: bool,
    seed: int | None = None,
) -> ScoreAssessment:
    return llm.parse_structured(
        score_system_prompt(profile),
        ex.model_dump_json(indent=2),
        ScoreAssessment,
        max_tokens=3000,
        model=model,
        think=think,
        temperature=0.15 if think else 0,
        seed=seed,
    )


def score_one(ex: Extraction, profile: Profile) -> tuple[ScoreAssessment, str | None]:
    """Reasoning ist primär; Instruct ist der protokollierte Sicherheitsfallback."""
    for attempt in range(2):
        try:
            return (
                _assessment_call(
                    ex,
                    profile,
                    llm.SCORE_MODEL,
                    think=True,
                    seed=llm.OLLAMA_SEED + attempt,
                ),
                None,
            )
        except Exception:  # noqa: BLE001 — ein zweiter strukturierter Versuch ist Absicht
            pass
    return _assessment_call(ex, profile, llm.EXTRACT_MODEL, think=False), llm.EXTRACT_MODEL


_SKILL_FACTORS: dict[MatchLevel, float] = {
    "direct": 1.0,
    "transferable": 0.6,
    "unknown": 0.25,
    "missing": 0.0,
}
_DOMAIN_POINTS: dict[FitLevel, int] = {
    "strong": 25,
    "moderate": 18,
    "weak": 10,
    "none": 0,
    "unknown": 12,
}
_INTEREST_POINTS: dict[FitLevel, int] = {
    "strong": 15,
    "moderate": 10,
    "weak": 5,
    "none": 0,
    "unknown": 7,
}

_SHORT_SKILL_TOKENS = {"r", "c", "go", "ai", "ml", "qa"}


def _skill_tokens(value: str) -> set[str]:
    normalized = value.lower().replace("c++", "cpp").replace("c#", "csharp")
    return {
        token
        for token in re.findall(r"[a-z0-9äöüß]+", normalized)
        if len(token) >= 3 or token in _SHORT_SKILL_TOKENS
    }


@dataclass
class ComputedScore:
    fit_score: int
    breakdown: dict[str, int]
    confidence: int
    reasons: list[str]
    gaps: list[str]
    angle: str
    evidence: dict


def _find_skill_assessment(
    requirement: Requirement, assessments: list[SkillAssessment]
) -> SkillAssessment | None:
    target = norm_text(requirement.name)
    exact = next((a for a in assessments if norm_text(a.requirement) == target), None)
    if exact:
        return exact
    candidates = [
        (fuzz.token_set_ratio(target, norm_text(a.requirement)), a) for a in assessments
    ]
    if not candidates:
        return None
    score, candidate = max(candidates, key=lambda item: item[0])
    return candidate if score >= 85 else None


def compute_score(ex: Extraction, profile: Profile, assessment: ScoreAssessment) -> ComputedScore:
    requirements = _requirements(ex)
    safe_profile = {key: value for key, value in profile.raw.items() if key != "anchors"}
    profile_text = yaml.safe_dump(safe_profile, allow_unicode=True, sort_keys=True)
    cleaned: list[dict] = []
    evidence_ok = 0
    for requirement in requirements:
        found = _find_skill_assessment(requirement, assessment.skills)
        level: MatchLevel = found.match if found else "unknown"
        profile_evidence = found.profile_evidence if found else None
        if level in {"direct", "transferable"} and not (
            profile_evidence and norm_text(profile_evidence) in norm_text(profile_text)
        ):
            level = "unknown"
        if level == "direct" and profile_evidence:
            requirement_tokens = _skill_tokens(requirement.name)
            evidence_tokens = _skill_tokens(profile_evidence)
            if requirement_tokens.isdisjoint(evidence_tokens):
                level = "unknown"
        job_evidence_ok = bool(requirement.evidence)
        profile_evidence_ok = level not in {"direct", "transferable"} or bool(profile_evidence)
        evidence_ok += int(job_evidence_ok and profile_evidence_ok)
        cleaned.append(
            {
                "requirement": requirement.name,
                "importance": requirement.importance,
                "match": level,
                "job_evidence": requirement.evidence,
                "profile_evidence": profile_evidence,
            }
        )

    def requirement_points(importance: str, maximum: int) -> int:
        values = [
            _SKILL_FACTORS[item["match"]]
            for item in cleaned
            if item["importance"] == importance
        ]
        return round(maximum * sum(values) / len(values)) if values else maximum // 2

    must_points = requirement_points("must", 50)
    nice_points = requirement_points("nice", 10)
    skills_points = must_points + nice_points
    extraction_text = norm_text(ex.model_dump_json())
    profile_text_normalized = norm_text(profile_text)
    domain_fit = assessment.domain_fit
    if domain_fit in {"strong", "moderate", "weak"} and not (
        norm_text(assessment.domain_evidence)
        and norm_text(assessment.domain_evidence) in extraction_text
    ):
        domain_fit = "unknown"
    interest_fit = assessment.interest_fit
    if interest_fit in {"strong", "moderate", "weak"} and not (
        norm_text(assessment.interest_evidence)
        and norm_text(assessment.interest_evidence) in profile_text_normalized
    ):
        interest_fit = "unknown"

    domain_points = _DOMAIN_POINTS[domain_fit]
    interest_points = _INTEREST_POINTS[interest_fit]
    fit_score = skills_points + domain_points + interest_points

    confidence = 100
    if not any(item["importance"] == "must" for item in cleaned):
        confidence -= 40
    if cleaned:
        confidence -= round(40 * (1 - evidence_ok / len(cleaned)))
    else:
        confidence -= 40
    if domain_fit == "unknown":
        confidence -= 10
    if interest_fit == "unknown":
        confidence -= 10
    confidence = max(0, min(100, confidence))

    counts = {level: sum(item["match"] == level for item in cleaned) for level in _SKILL_FACTORS}
    reasons = [
        f"Skills {skills_points}/60: {counts['direct']} direkt, "
        f"{counts['transferable']} übertragbar, {counts['missing']} fehlen",
        f"Domänenfit {domain_points}/25 ({domain_fit}): {assessment.domain_evidence}",
        f"Interessenfit {interest_points}/15 ({interest_fit}): "
        f"{assessment.interest_evidence}",
    ]
    missing = [item["requirement"] for item in cleaned if item["match"] == "missing"]
    gaps = list(dict.fromkeys([*assessment.gaps, *missing]))[:3]
    return ComputedScore(
        fit_score=fit_score,
        breakdown={
            "skills": skills_points,
            "must_skills": must_points,
            "nice_skills": nice_points,
            "domain": domain_points,
            "interests": interest_points,
        },
        confidence=confidence,
        reasons=reasons,
        gaps=gaps,
        angle=assessment.angle,
        evidence={
            "skills": cleaned,
            "domain": {"fit": domain_fit, "quote": assessment.domain_evidence},
            "interests": {"fit": interest_fit, "quote": assessment.interest_evidence},
            "hard_no_hits": [hit.model_dump() for hit in assessment.hard_no_hits],
        },
    )


def formal_status(
    ex: Extraction, profile: Profile, assessment: ScoreAssessment | None = None
) -> tuple[TrafficStatus, list[str]]:
    red: list[str] = []
    yellow: list[str] = []
    levels = {"none": 0, "bsc": 1, "msc": 2, "phd": 3}
    education = (profile.education or "").lower().split("_")[0]
    if ex.education_min != "none" and education not in levels:
        yellow.append("Eigene Ausbildung nicht eindeutig einordenbar")
    elif ex.education_min != "none" and levels.get(education, -1) < levels[ex.education_min]:
        red.append(f"Ausbildung: {ex.education_min} verlangt")
    if ex.phd_required and not profile.phd_wanted:
        red.append("PhD ausdrücklich erforderlich")
    if ex.seniority not in profile.seniority_allowed:
        red.append(f"Seniorität {ex.seniority} nicht im Zielprofil")
    if (
        ex.years_experience_min is not None
        and ex.years_experience_min > profile.max_years_experience
    ):
        red.append(
            f"{ex.years_experience_min} Jahre verlangt, Profilgrenze {profile.max_years_experience}"
        )
    if assessment:
        source = norm_text(ex.model_dump_json())
        hard_no_rules = [norm_text(rule) for rule in profile.hard_no]
        red.extend(
            f"Hard-no: {hit.rule}"
            for hit in assessment.hard_no_hits
            if norm_text(hit.evidence)
            and norm_text(hit.evidence) in source
            and any(
                norm_text(hit.rule) in rule
                or rule in norm_text(hit.rule)
                or fuzz.token_set_ratio(norm_text(hit.rule), rule) >= 85
                for rule in hard_no_rules
            )
        )
    return ("red", red) if red else (("yellow", yellow) if yellow else ("green", []))


def practical_status(
    ex: Extraction, travel_ok: bool | None, in_austria: bool
) -> tuple[TrafficStatus, list[str]]:
    red: list[str] = []
    yellow: list[str] = []
    if ex.workplace_mode != "remote":
        if not in_austria:
            red.append("Vor-Ort-/Hybridstandort außerhalb Österreichs")
        elif travel_ok is False:
            red.append("Alle bekannten Anker über dem Fahrzeitlimit")
        elif travel_ok is None:
            yellow.append("Standort oder Fahrzeit unbekannt")
    if ex.contract_end:
        try:
            if date.fromisoformat(ex.contract_end) < date.today() + timedelta(
                days=SHORT_CONTRACT_MONTHS * 30
            ):
                yellow.append(f"Befristung endet {ex.contract_end}")
        except ValueError:
            yellow.append("Befristungsdatum unklar")
    return ("red", red) if red else (("yellow", yellow) if yellow else ("green", []))


def score_pending(conn: sqlite3.Connection, profile: Profile, limit: int | None = None) -> int:
    """Scort alle Postings ohne aktuelles Profil-/Formel-/Modell-Ergebnis."""
    rows = conn.execute(
        """SELECT p.id AS posting_id, p.extracted_json, p.site_id
           FROM postings p
           LEFT JOIN scores s ON s.posting_id = p.id AND s.profile_version = ?
             AND s.score_version = ? AND s.model = ?
           WHERE s.posting_id IS NULL AND p.schema_version = ? AND p.model = ?
           ORDER BY p.id""",
        (
            profile.profile_version,
            SCORE_VERSION,
            llm.SCORE_MODEL,
            EXTRACTION_SCHEMA_VERSION,
            llm.EXTRACT_MODEL,
        ),
    ).fetchall()
    if limit:
        rows = rows[:limit]
    if rows:
        llm.ensure_available([llm.SCORE_MODEL, llm.EXTRACT_MODEL])
    now = datetime.now(UTC).isoformat(timespec="seconds")
    done = 0
    import typer

    with typer.progressbar(rows, label="  Score", show_pos=True) as bar:
        for row in bar:
            ex = Extraction.model_validate_json(row["extracted_json"])
            travel_ok = site_travel_ok(conn, row["site_id"], profile)
            in_austria = locations.is_in_austria(conn, ex.location_text)
            hard = hard_filter(
                ex,
                profile,
                travel_ok,
                in_austria,
            )
            assessment: ScoreAssessment | None = None
            fit: ComputedScore | None = None
            fallback_model: str | None = None
            if hard.passed:
                try:
                    assessment, fallback_model = score_one(ex, profile)
                    fit = compute_score(ex, profile, assessment)
                except Exception as e:  # noqa: BLE001
                    print(f"\n  Score fehlgeschlagen für posting {row['posting_id']}: {e}")
                    continue
            formal, formal_reasons = formal_status(ex, profile, assessment)
            practical, practical_reasons = practical_status(ex, travel_ok, in_austria)
            conn.execute(
                """INSERT OR REPLACE INTO scores
                   (posting_id, profile_version, hard_pass, hard_reasons,
                    fit_score, fit_reasons, gaps, angle, model, scored_at,
                    score_version, score_breakdown, score_confidence, score_evidence,
                    formal_status, formal_reasons, practical_status, practical_reasons,
                    fallback_model)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["posting_id"],
                    profile.profile_version,
                    int(hard.passed),
                    json.dumps({"reasons": hard.reasons, "flags": hard.flags}, ensure_ascii=False),
                    fit.fit_score if fit else None,
                    json.dumps(fit.reasons, ensure_ascii=False) if fit else None,
                    json.dumps(fit.gaps, ensure_ascii=False) if fit else None,
                    fit.angle if fit else None,
                    llm.SCORE_MODEL,
                    now,
                    SCORE_VERSION,
                    json.dumps(fit.breakdown, ensure_ascii=False) if fit else None,
                    fit.confidence if fit else None,
                    json.dumps(fit.evidence, ensure_ascii=False) if fit else None,
                    formal,
                    json.dumps(formal_reasons, ensure_ascii=False),
                    practical,
                    json.dumps(practical_reasons, ensure_ascii=False),
                    fallback_model,
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
