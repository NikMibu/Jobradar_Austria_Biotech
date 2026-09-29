"""Profil-Matching (SPEC §6): harte Filter (kein LLM), Jev-Score nur für hard_pass,
Bewerbungsempfehlung (bewerben/stretch/nicht_bewerben), Initiativ-Score pro Firma (kein LLM)."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from . import jev, llm, locations
from .config import Profile
from .extract import SCHEMA_VERSION as EXTRACTION_SCHEMA_VERSION
from .extract import Extraction
from .jev import JevAssessment

SHORT_CONTRACT_MONTHS = 12
# v3: Extraktions-Schema 5 (role_family computational_chemistry) — der Score-Cache
# keyt nicht auf die Extraktions-Version, daher hier bumpen, um alle Postings gegen
# die neue Taxonomie neu zu bewerten.
# v4: Jev (TypeSafe System One) statt Score-LLM; Empfehlung + Stub-Deckel.
# v5: eine Jev-Hard-No-Frage je Regel statt gebündelt; Life-Science-Tätigkeit als Info-Frage.
SCORE_VERSION = 5
SCORE_MODEL = jev.MODEL


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
        # Kein Ausschluss mehr: Jev bewertet jedes Inserat (kostet praktisch nichts).
        # Gegencheck 2026-09-24: "Production Supervisor IVD" (role_family other) wäre
        # sonst ungesehen aussortiert worden — trotz Gesprächseinladung. Vertrieb &
        # Co. fängt die Hard-No-Frage an Jev ab.
        res.flags.append(f"Rollenfamilie {ex.role_family} außerhalb des Zielprofils")
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


TrafficStatus = Literal["green", "yellow", "red"]
Recommendation = Literal["bewerben", "stretch", "nicht_bewerben"]

# Inserate unter dieser Textlänge sind Stubs (nur Titel/Teaser, z. B. PDF-Links
# oder Kurz-Snippets). Daily 2026-09-15: solche Stubs landeten mit 70/100 ganz
# oben, weil nur der Titel bewertet wurde → Confidence-Deckel + max. "stretch".
STUB_TEXT_CHARS = 800
STUB_CONFIDENCE_CAP = 30
NOT_CLAIM_THRESHOLD = 0.7
HARD_NO_THRESHOLD = 0.8
# Erster Volllauf 2026-09-24: Jevs Choice sagte "bewerben" auch bei Fachfit 44 und
# Fachnähe 0,27 (Testperson Marktforschung) — die Formel muss mitreden.
OFF_DOMAIN = 0.35
APPLY_MIN_FIT = 50


def text_quality(raw_text: str | None) -> Literal["full", "stub"]:
    return "stub" if len((raw_text or "").strip()) < STUB_TEXT_CHARS else "full"


@dataclass
class ComputedScore:
    fit_score: int
    breakdown: dict[str, int]
    confidence: int
    reasons: list[str]
    gaps: list[str]
    evidence: dict


def compute_score(
    ex: Extraction, assessment: JevAssessment, quality: str = "full"
) -> ComputedScore:
    """60/25/15 aus Jev-Erwartungswerten (je 0–1). Für PhD-Stellen ersetzt der
    Themenfit zur Promotion den allgemeinen Interessenfit."""
    reqs = assessment.requirements

    def requirement_points(importance: str, maximum: int) -> int:
        values = [r["level"] for r in reqs if r["importance"] == importance and r["level"] is not None]
        return round(maximum * sum(values) / len(values)) if values else maximum // 2

    must_points = requirement_points("must", 50)
    nice_points = requirement_points("nice", 10)
    skills_points = must_points + nice_points
    domain_points = round(25 * assessment.domain)
    use_phd = ex.position_type in jev.PHD_TYPES and assessment.phd_topic is not None
    interest_value = assessment.phd_topic if use_phd else assessment.interest
    interest_points = round(15 * interest_value)
    fit_score = skills_points + domain_points + interest_points

    confidence = round(100 * assessment.confidence)
    if not any(r["importance"] == "must" for r in reqs):
        confidence -= 30
    if quality == "stub":
        confidence = min(confidence, STUB_CONFIDENCE_CAP)
    confidence = max(0, min(100, confidence))

    def count(key: str) -> int:
        return sum(
            max(("p_missing", "p_transferable", "p_direct"), key=lambda k: r[k]) == key
            for r in reqs
        )

    interest_label = "PhD-Themenfit" if use_phd else "Interessenfit"
    reasons = [
        f"Skills {skills_points}/60: {count('p_direct')} direkt, "
        f"{count('p_transferable')} übertragbar, {count('p_missing')} fehlen",
        f"Domänenfit {domain_points}/25",
        f"{interest_label} {interest_points}/15",
    ]
    if quality == "stub":
        reasons.append("Datenbasis dünn: Inserat ohne verwertbaren Volltext")
    gaps = [
        r["requirement"]
        for r in sorted(reqs, key=lambda r: (r["importance"] != "must", -r["p_missing"]))
        if r["p_missing"] >= 0.5
    ][:3]
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
        evidence={"jev": assessment.to_json(), "text_quality": quality},
    )


ENTRY_SENIORITY = {"entry", "junior"}


def decide_recommendation(
    assessment: JevAssessment | None, formal: TrafficStatus, quality: str,
    seniority: str = "junior", abroad: bool = False,
    fit_score: int | None = None, initiative: bool = False,
) -> tuple[Recommendation, list[str]]:
    """Jev-Empfehlung plus harte Python-Regeln, die Jev nicht überstimmen darf.

    Ein verlangter Nicht-Claim (z. B. CSV/GMP-Praxis) heißt bei Einstiegsstellen
    höchstens "stretch" — dort wird es oft angelernt —, sonst "nicht_bewerben"."""
    if assessment is None or not assessment.recommendation_probs:
        return "nicht_bewerben", ["Rollenfamilie ausgeschlossen"]
    probs = assessment.recommendation_probs
    rec: Recommendation = max(probs, key=probs.get)  # type: ignore[assignment]
    notes: list[str] = []
    if abroad:
        # Vor-Ort-/Hybridstelle außerhalb Österreichs: Umzug ins Ausland ist ein
        # Hard-No im Profil (Zwischenstand 2026-09-24: DKFZ/MPI landeten auf "bewerben").
        if rec != "nicht_bewerben":
            notes.append("Standort außerhalb Österreichs (kein Umzug ins Ausland)")
        rec = "nicht_bewerben"
    if formal == "red":
        if rec != "nicht_bewerben":
            notes.append("Formale Hürde (Ampel rot)")
        rec = "nicht_bewerben"
    if assessment.not_claim > NOT_CLAIM_THRESHOLD and rec != "nicht_bewerben":
        if seniority in ENTRY_SENIORITY:
            if rec == "bewerben":
                rec = "stretch"
                notes.append(
                    f"Verlangt Erfahrung, die fehlt (p={assessment.not_claim:.2f}) — "
                    "Einstiegsstelle, daher Stretch"
                )
        else:
            rec = "nicht_bewerben"
            notes.append(f"Verlangt Nicht-Claim (p={assessment.not_claim:.2f})")
    if assessment.domain < OFF_DOMAIN:
        if rec != "nicht_bewerben":
            notes.append(f"Fachfremd (Fachnähe {assessment.domain:.2f})")
        rec = "nicht_bewerben"
    if rec == "bewerben":
        # life_science ist nur Information: IT-/KI-Stellen ohne Life-Science-Bezug
        # können passen (Nutzerentscheidung 2026-09-24), daher kein Deckel.
        if quality == "stub":
            rec = "stretch"
            notes.append("Stub-Inserat: höchstens Stretch bis zum Volltext")
        elif initiative:
            rec = "stretch"
            notes.append("Initiativbewerbung/Talentpool: kein konkretes Stellenprofil")
        elif fit_score is not None and fit_score < APPLY_MIN_FIT:
            rec = "stretch"
            notes.append(f"Fachfit {fit_score} < {APPLY_MIN_FIT}")
    return rec, notes


def formal_status(
    ex: Extraction, profile: Profile, assessment: JevAssessment | None = None
) -> tuple[TrafficStatus, list[str]]:
    red: list[str] = []
    yellow: list[str] = []
    levels = {"none": 0, "bsc": 1, "msc": 2, "phd": 3}
    education = (profile.education or "").lower().split("_")[0]
    if ex.education_min != "none" and education not in levels:
        yellow.append("Eigene Ausbildung nicht eindeutig einordenbar")
    elif ex.education_min != "none" and levels.get(education, -1) < levels[ex.education_min]:
        # Eine Doktoratsstelle "verlangt" im Extrakt oft phd, gemeint ist aber das Ziel.
        if not (ex.position_type in jev.PHD_TYPES and ex.education_min == "phd"):
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
    if assessment and assessment.hard_no > HARD_NO_THRESHOLD:
        # Standort-hard_no entscheidet in_austria (practical_status); jev fragt nur
        # die übrigen Regeln ab.
        red.append(f"Hard-no (p={assessment.hard_no:.2f})")
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
        """SELECT p.id AS posting_id, p.extracted_json, p.site_id,
                  r.raw_text, r.raw_company
           FROM postings p
           JOIN postings_raw r ON r.id = p.raw_id
           LEFT JOIN scores s ON s.posting_id = p.id AND s.profile_version = ?
             AND s.score_version = ? AND s.model = ?
           WHERE s.posting_id IS NULL AND p.schema_version = ? AND p.model = ?
           ORDER BY p.id""",
        (
            profile.profile_version,
            SCORE_VERSION,
            SCORE_MODEL,
            EXTRACTION_SCHEMA_VERSION,
            llm.EXTRACT_MODEL,
        ),
    ).fetchall()
    if limit:
        rows = rows[:limit]
    if rows:
        jev.ensure_available()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    done = 0
    tokens = 0
    import typer

    # DB-Lesearbeit (site/location-Cache) im Haupt-Thread; danach laufen die
    # Jev-Calls parallel, die Writes wieder im Haupt-Thread.
    prepared = []
    for row in rows:
        ex = Extraction.model_validate_json(row["extracted_json"])
        travel_ok = site_travel_ok(conn, row["site_id"], profile)
        in_austria = locations.is_in_austria(conn, ex.location_text)
        prepared.append((row, ex, travel_ok, in_austria))

    def _assess(item):
        row, ex, travel_ok, in_austria = item
        quality = text_quality(row["raw_text"])
        hard = hard_filter(ex, profile, travel_ok, in_austria)
        assessment: JevAssessment | None = None
        fit: ComputedScore | None = None
        if hard.passed:
            assessment = jev.assess(ex, profile, row["raw_company"], row["raw_text"])
            fit = compute_score(ex, assessment, quality)
        formal, formal_reasons = formal_status(ex, profile, assessment)
        practical, practical_reasons = practical_status(ex, travel_ok, in_austria)
        rec, rec_notes = decide_recommendation(
            assessment, formal, quality, ex.seniority,
            abroad=not in_austria and ex.workplace_mode != "remote",
            fit_score=fit.fit_score if fit else None,
            initiative=ex.position_type == "initiative",
        )
        return (hard, fit, assessment, quality, formal, formal_reasons,
                practical, practical_reasons, rec, rec_notes)

    with typer.progressbar(length=len(prepared), label="  Score (Jev)", show_pos=True) as bar:
        for item, result in llm.parallel_map(_assess, prepared, workers=jev.CONCURRENCY):
            bar.update(1)
            row = item[0]
            if isinstance(result, Exception):
                print(f"\n  Score fehlgeschlagen für posting {row['posting_id']}: {result}")
                continue
            (hard, fit, assessment, quality, formal, formal_reasons,
             practical, practical_reasons, rec, rec_notes) = result
            if assessment and assessment.input_tokens:
                tokens += assessment.input_tokens
            _store_score(
                conn, row["posting_id"], profile, now, hard, fit, assessment, quality,
                formal, formal_reasons, practical, practical_reasons, rec, rec_notes,
            )
            done += 1
    if tokens:
        print(f"  Jev: {tokens:,} Input-Tokens")
    return done


def _store_score(
    conn, posting_id, profile, now, hard, fit, assessment, quality,
    formal, formal_reasons, practical, practical_reasons, rec, rec_notes,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO scores
           (posting_id, profile_version, hard_pass, hard_reasons,
            fit_score, fit_reasons, gaps, angle, model, scored_at,
            score_version, score_breakdown, score_confidence, score_evidence,
            formal_status, formal_reasons, practical_status, practical_reasons,
            fallback_model, recommendation, recommendation_probs,
            recommendation_notes, text_quality)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            posting_id,
            profile.profile_version,
            int(hard.passed),
            json.dumps({"reasons": hard.reasons, "flags": hard.flags}, ensure_ascii=False),
            fit.fit_score if fit else None,
            json.dumps(fit.reasons, ensure_ascii=False) if fit else None,
            json.dumps(fit.gaps, ensure_ascii=False) if fit else None,
            None,
            SCORE_MODEL,
            now,
            SCORE_VERSION,
            json.dumps(fit.breakdown, ensure_ascii=False) if fit else None,
            fit.confidence if fit else None,
            json.dumps(fit.evidence, ensure_ascii=False) if fit else None,
            formal,
            json.dumps(formal_reasons, ensure_ascii=False),
            practical,
            json.dumps(practical_reasons, ensure_ascii=False),
            None,
            rec,
            json.dumps(assessment.recommendation_probs) if assessment else None,
            json.dumps(rec_notes, ensure_ascii=False),
            quality,
        ),
    )
    conn.commit()


def recompute_statuses(conn: sqlite3.Connection, profile: Profile) -> int:
    """Ampeln und Empfehlung aus dem gespeicherten Jev-Assessment neu berechnen —
    ohne API-Call. fit_score bleibt unverändert."""
    rows = conn.execute(
        """SELECT s.posting_id, s.score_evidence, s.text_quality, s.fit_score,
                  p.extracted_json, p.site_id
           FROM scores s JOIN postings p ON p.id = s.posting_id
           WHERE s.profile_version = ? AND s.score_version = ? AND s.model = ?""",
        (profile.profile_version, SCORE_VERSION, SCORE_MODEL),
    ).fetchall()
    done = 0
    for row in rows:
        ex = Extraction.model_validate_json(row["extracted_json"])
        evidence = json.loads(row["score_evidence"]) if row["score_evidence"] else {}
        assessment = JevAssessment.from_json(evidence["jev"]) if "jev" in evidence else None
        quality = row["text_quality"] or "full"
        travel_ok = site_travel_ok(conn, row["site_id"], profile)
        in_austria = locations.is_in_austria(conn, ex.location_text)
        formal, formal_reasons = formal_status(ex, profile, assessment)
        practical, practical_reasons = practical_status(ex, travel_ok, in_austria)
        rec, rec_notes = decide_recommendation(
            assessment, formal, quality, ex.seniority,
            abroad=not in_austria and ex.workplace_mode != "remote",
            fit_score=row["fit_score"],
            initiative=ex.position_type == "initiative",
        )
        conn.execute(
            """UPDATE scores SET formal_status = ?, formal_reasons = ?,
                   practical_status = ?, practical_reasons = ?,
                   recommendation = ?, recommendation_notes = ?
               WHERE posting_id = ? AND profile_version = ? AND score_version = ?
                 AND model = ?""",
            (
                formal,
                json.dumps(formal_reasons, ensure_ascii=False),
                practical,
                json.dumps(practical_reasons, ensure_ascii=False),
                rec,
                json.dumps(rec_notes, ensure_ascii=False),
                row["posting_id"],
                profile.profile_version,
                SCORE_VERSION,
                SCORE_MODEL,
            ),
        )
        done += 1
    conn.commit()
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
