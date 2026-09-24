"""Jev (TypeSafe System One) als Score-Modell.

Jev beantwortet geschlossene Fragen (Noul = ja/nein, Score = geordnete Stufen,
Choice = Kategorie) mit kalibrierten Wahrscheinlichkeiten statt Freitext. Pro
Posting geht EIN `system_one`-Call raus: State = Stelle + Kandidat, Fragen =
je Anforderung eine Score-Frage plus Domäne, Interesse, PhD-Themenfit,
Nicht-Claims, Hard-No und die Bewerbungsempfehlung. Python rechnet daraus den
Fachfit (match.compute_score) — Jev vergibt selbst keine Punktzahl.

Konfiguration:
  TYPESAFE_API_KEY            liest das typesafe-sdk selbst
  HEIMSPIEL_JEV_MODEL         voll versionierte Modell-ID (Kurz-IDs → HTTP 400);
                              gepinnt, weil das Modell Teil des Score-Cache-Keys ist
  HEIMSPIEL_JEV_CONCURRENCY   parallele Requests (default 8)
"""

import os
import threading
from dataclasses import dataclass, field
from typing import Any

from .config import Profile
from .extract import Extraction, Requirement

MODEL = os.environ.get("HEIMSPIEL_JEV_MODEL", "jev-1.13.0")
CONCURRENCY = int(os.environ.get("HEIMSPIEL_JEV_CONCURRENCY", "8"))
MAX_REQUIREMENTS = 12
POSTING_EXCERPT_CHARS = 6000
PHD_TYPES = {"phd", "predoc"}

REQUIREMENT_LEVELS = [
    "fehlt: im Kandidatenprofil keine Erfahrung mit dieser Anforderung, auch nichts Verwandtes",
    "übertragbar: verwandte Methode oder Erfahrung vorhanden, aber nicht genau diese",
    "direkt belegt: genau diese Methode/Technologie/Erfahrung ist im Profil belegt",
]
FIT_LEVELS = [
    "keine fachliche Nähe",
    "schwache Nähe (nur allgemeiner Life-Science-/Datenbezug)",
    "mittlere Nähe (benachbartes Fachgebiet, Kernmethoden teilweise gleich)",
    "starke Nähe (gleiches Fachgebiet und gleiche Kernmethoden)",
]
RECOMMENDATIONS = {
    "bewerben": (
        "Muss-Anforderungen sind überwiegend direkt belegt oder gut übertragbar, fachlich nah, "
        "Erfahrungsniveau passt zu einem Berufseinsteiger mit gut zwei Jahren Praxis — "
        "realistische Chance auf eine Einladung."
    ),
    "stretch": (
        "Teilweise passend: Kern und Domäne stimmen, aber einzelne Muss-Anforderungen fehlen "
        "oder sind nur übertragbar, oder es wird etwas mehr Erfahrung verlangt — Bewerbung "
        "lohnt nur mit gezielter Argumentation."
    ),
    "nicht_bewerben": (
        "Wesentliche Muss-Anforderungen fehlen, die Stelle setzt Erfahrung voraus, die der "
        "Kandidat ausdrücklich nicht hat, deutlich zu senior, fachlich fern oder ein Hard-No."
    ),
}


@dataclass
class JevAssessment:
    requirements: list[dict[str, Any]] = field(default_factory=list)
    domain: float = 0.5
    interest: float = 0.5
    phd_topic: float | None = None
    not_claim: float = 0.0
    hard_no: float = 0.0
    recommendation_probs: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    input_tokens: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "requirements": self.requirements,
            "domain": self.domain,
            "interest": self.interest,
            "phd_topic": self.phd_topic,
            "not_claim": self.not_claim,
            "hard_no": self.hard_no,
            "recommendation_probs": self.recommendation_probs,
            "confidence": self.confidence,
            "input_tokens": self.input_tokens,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "JevAssessment":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def requirements_for(ex: Extraction) -> list[Requirement]:
    """Muss-Anforderungen zuerst, gedeckelt — jede wird eine eigene Jev-Frage."""
    if ex.requirements:
        reqs = list(ex.requirements)
    else:
        reqs = [
            Requirement(name=name, importance=importance, evidence="")
            for importance, names in (("must", ex.must_skills), ("nice", ex.nice_skills))
            for name in names
        ]
    # Soft Skills lassen sich aus einem CV nie direkt belegen und zogen im Retro-Check
    # (2026-09-24) fast jede Stelle Richtung "stretch" — nicht Teil des Fachfits.
    reqs = [r for r in reqs if r.kind != "soft"]
    reqs.sort(key=lambda r: r.importance != "must")
    return reqs[:MAX_REQUIREMENTS]


def _location_free_hard_no(profile: Profile) -> list[str]:
    # Standort entscheidet in_austria (practical_status), nicht Jev — s. formal_status.
    return [
        rule for rule in profile.hard_no
        if "ausland" not in rule.lower() and "umzug" not in rule.lower()
    ]


def build_state(
    ex: Extraction, profile: Profile, company: str | None, posting_text: str | None
) -> dict[str, Any]:
    return {
        "stelle": {
            "titel": ex.title_norm,
            "firma": company or "unbekannt",
            "stellenart": ex.position_type,
            "senioritaet": ex.seniority,
            "mindestausbildung": ex.education_min,
            "jahre_erfahrung_min": ex.years_experience_min,
            "zusammenfassung": ex.summary_2_lines,
            "anforderungen": [
                {"name": r.name, "wichtigkeit": r.importance, "beleg": r.evidence}
                for r in requirements_for(ex)
            ],
            "themen": ex.domain_keywords,
            "inserat_auszug": (posting_text or "")[:POSTING_EXCERPT_CHARS],
        },
        "kandidat": {
            "profil": profile.cv_summary,
            "belegte_skills": profile.skills,
            "interessen": profile.interests,
            "phd_interessen": profile.phd_interests,
            "nicht_vorhanden": profile.not_claims,
            "ausbildung": profile.education,
        },
    }


def build_questions(ex: Extraction, profile: Profile) -> dict[str, Any]:
    from typesafe_sdk import Choice, Noul, Score

    questions: dict[str, Any] = {}
    for i, req in enumerate(requirements_for(ex)):
        questions[f"req_{i}"] = Score(
            instructions=(
                f"Wie gut ist die Anforderung „{req.name}“ der Stelle im Kandidatenprofil "
                "belegt? Nur kandidat.profil und kandidat.belegte_skills zählen als Beleg; "
                "Interessen sind keine Erfahrung, kandidat.nicht_vorhanden ist ausdrücklich fehlend."
            ),
            criteria=REQUIREMENT_LEVELS,
        )
    questions["domain"] = Score(
        instructions="Wie nah ist die Tätigkeit der Stelle am belegten Fachprofil des Kandidaten?",
        criteria=FIT_LEVELS,
    )
    questions["interest"] = Score(
        instructions="Wie stark trifft die Stelle die ausdrücklich genannten kandidat.interessen?",
        criteria=FIT_LEVELS,
    )
    if ex.position_type in PHD_TYPES:
        questions["phd_topic"] = Score(
            instructions=(
                "Wie gut passt das Forschungsthema dieser Doktoratsstelle zu "
                "kandidat.phd_interessen und zur Masterarbeit des Kandidaten?"
            ),
            criteria=FIT_LEVELS,
        )
    questions["not_claim"] = Noul(
        instructions=(
            "Setzt die Stelle zwingend (als Muss-Anforderung) eine Erfahrung aus "
            "kandidat.nicht_vorhanden voraus? Wünschenswerte Punkte zählen nicht."
        ),
    )
    hard_no = _location_free_hard_no(profile)
    if hard_no:
        questions["hard_no"] = Noul(
            instructions=(
                "Fällt die Stelle eindeutig unter eine dieser Ausschlussregeln: "
                + "; ".join(hard_no) + "?"
            ),
        )
    questions["recommendation"] = Choice(
        instructions=(
            "Soll sich der Kandidat auf diese Stelle bewerben? Bewertet wird die fachliche und "
            "formale Passung aus Sicht einer Personalabteilung."
        ),
        criteria=RECOMMENDATIONS,
    )
    return questions


def parse_response(ex: Extraction, answers: dict[str, Any], input_tokens: int | None) -> JevAssessment:
    """Jev-Antworten in normierte Werte (0–1) übersetzen."""

    def level(name: str, levels: int) -> float | None:
        answer = answers.get(name)
        if answer is None:
            return None
        return max(0.0, min(1.0, float(answer.score) / (levels - 1)))

    confidences: list[float] = []
    requirements: list[dict[str, Any]] = []
    for i, req in enumerate(requirements_for(ex)):
        answer = answers.get(f"req_{i}")
        if answer is None:
            continue
        probs = {int(k): float(v) for k, v in answer.probabilities.items()}
        confidences.append(float(answer.confidence))
        requirements.append(
            {
                "requirement": req.name,
                "importance": req.importance,
                "job_evidence": req.evidence,
                "level": level(f"req_{i}", len(REQUIREMENT_LEVELS)),
                "p_missing": round(probs.get(0, 0.0), 3),
                "p_transferable": round(probs.get(1, 0.0), 3),
                "p_direct": round(probs.get(2, 0.0), 3),
            }
        )
    for name in ("domain", "interest", "phd_topic"):
        if name in answers:
            confidences.append(float(answers[name].confidence))
    rec = answers.get("recommendation")
    if rec is not None:
        confidences.append(float(rec.confidence))
    return JevAssessment(
        requirements=requirements,
        domain=level("domain", len(FIT_LEVELS)) if "domain" in answers else 0.5,
        interest=level("interest", len(FIT_LEVELS)) if "interest" in answers else 0.5,
        phd_topic=level("phd_topic", len(FIT_LEVELS)),
        not_claim=float(answers["not_claim"].noul) if "not_claim" in answers else 0.0,
        hard_no=float(answers["hard_no"].noul) if "hard_no" in answers else 0.0,
        recommendation_probs=(
            {k: round(float(v), 3) for k, v in rec.probabilities.items()} if rec else {}
        ),
        confidence=sum(confidences) / len(confidences) if confidences else 0.0,
        input_tokens=input_tokens,
    )


_client = None
_client_lock = threading.Lock()


def client():
    global _client
    with _client_lock:
        if _client is None:
            from typesafe_sdk import RetryPolicy, TypeSafeClient

            _client = TypeSafeClient(
                model=MODEL, retry=RetryPolicy(max_retries=4, backoff_max=10.0)
            )
        return _client


def assess(
    ex: Extraction,
    profile: Profile,
    company: str | None = None,
    posting_text: str | None = None,
    *,
    model: str | None = None,
) -> JevAssessment:
    response = client().system_one(
        state=build_state(ex, profile, company, posting_text),
        questions=build_questions(ex, profile),
        model=model or MODEL,
    )
    return parse_response(ex, response.answers, response.usage.input_tokens)


def ensure_available() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise RuntimeError(
            "TYPESAFE_API_KEY fehlt — Key in der TypeSafe-Console anlegen und in .env eintragen."
        )
