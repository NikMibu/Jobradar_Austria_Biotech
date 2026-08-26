"""LLM-Extraktion ins feste Schema (SPEC §5).

Cache-Key = content_hash + schema_version: ein Posting wird nur neu extrahiert,
wenn sich Inhalt oder Schema ändern. Backfills > 500 Inserate laufen über die
Batch-API (halber Preis)."""

import json
import math
import re
import sqlite3
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from rapidfuzz import fuzz

from . import llm
from .normalize import match_company, norm_text

# v2: role_family-Definitionen im Prompt (236× fälschlich "other" mit v1)
# v3: + wet_lab_rnd — Nasslabor-F&E (CRISPR Protein Engineer u. Ä. landeten in "other")
# v4: evidenzbasierte Anforderungen und Belege für alle kritischen Felder
SCHEMA_VERSION = 4
BATCH_THRESHOLD = 500

RoleFamily = Literal[
    "bioinformatics",
    "data_science",
    "csv_qa_validation",
    "lab_analytics",
    "downstream_process",
    "mass_spec",
    "data_steward",
    "scientific_software",
    "wet_lab_rnd",
    "other",
]


class Requirement(BaseModel):
    name: str
    importance: Literal["must", "nice"]
    evidence: str


class FieldEvidence(BaseModel):
    phd_required: str | None = None
    years_experience_min: str | None = None
    german_required: str | None = None
    salary_min_eur_month: str | None = None
    workplace_mode: str | None = None
    contract_type: str | None = None
    contract_end: str | None = None
    application_deadline: str | None = None


class Extraction(BaseModel):
    title_norm: str
    role_family: RoleFamily
    seniority: Literal["entry", "junior", "mid", "senior"]
    education_min: Literal["none", "bsc", "msc", "phd"]
    phd_required: bool
    years_experience_min: int | None = None
    must_skills: list[str] = Field(default_factory=list)
    nice_skills: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    domain_keywords: list[str] = Field(default_factory=list)
    german_required: bool
    salary_min_eur_month: float | None = None
    salary_basis: Literal["monthly_14x", "yearly"] | None = None
    location_text: str
    workplace_mode: Literal["onsite", "hybrid", "remote", "unknown"]
    travel_share_pct: int | None = None
    start_text: str | None = None
    contract_type: Literal["permanent", "fixed_term", "internship", "unknown"]
    contract_end: str | None = None
    application_deadline: str | None = None
    summary_2_lines: str
    field_evidence: FieldEvidence = Field(default_factory=FieldEvidence)
    validation_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_legacy_skill_lists(self):
        """`requirements` ist die Quelle; die alten Listen bleiben exportkompatibel."""
        if self.requirements:
            self.must_skills = [r.name for r in self.requirements if r.importance == "must"]
            self.nice_skills = [r.name for r in self.requirements if r.importance == "nice"]
        return self


SYSTEM_PROMPT = """Du extrahierst Stellenanzeigen (deutsch oder englisch, meist Österreich) in ein festes Schema.

Regeln:
- requirements: Erfasse JEDE fachliche oder methodische Anforderung einzeln. `must` nur
  für erforderlich/verlangt/vorausgesetzt; Wünsche und Vorteile sind `nice`. `evidence`
  ist ein kurzes WÖRTLICHES Zitat aus dem Inserat. Keine Anforderung ohne Textbeleg.
- must_skills und nice_skills aus denselben Anforderungen befüllen. domain_keywords
  enthält konkrete fachliche Themen des Jobs, nicht allgemeine Wörter wie "Teamarbeit".
- Gehalt nur übernehmen, wenn im Text eine konkrete Zahl steht (österreichische Inserate müssen das kollektivvertragliche Mindestgehalt nennen). salary_min_eur_month ist immer der vergleichbare Monatswert: Monatsbrutto unverändert, Jahresbrutto durch 14. salary_basis beschreibt die Schreibweise der Quelle: monthly_14x für Monatsbrutto, yearly für Jahresbrutto.
- phd_required = true NUR bei explizitem "PhD/Doktorat erforderlich", nicht bei "von Vorteil" oder "wünschenswert".
- german_required = true nur, wenn Deutsch explizit verlangt wird (nicht bloß Inserat auf Deutsch).
- seniority: entry = Absolvent/keine Erfahrung, junior = 0-2 Jahre, mid = 2-5 Jahre, senior = 5+ Jahre oder Lead-Rolle.
- role_family — wähle die passendste Kategorie nach diesen Definitionen:
  - bioinformatics: Analyse biologischer Daten (NGS, Genomik, Omics, Pipelines). Beispiele: "Bioinformatician", "Bioinformatiker", "Computational Biologist", "NGS Data Analyst".
  - data_science: Datenanalyse/ML/Statistik, auch ohne Biologie-Bezug. Beispiele: "Data Scientist", "Machine Learning Engineer", "Biostatistiker".
  - csv_qa_validation: Computer System Validation, Qualifizierung, QA im GMP-Umfeld. Beispiele: "CSV Engineer", "Validierungsingenieur", "Qualification Expert", "QA Specialist GMP".
  - lab_analytics: Labor-Analytik nasschemisch/instrumentell (HPLC, Assays, QC-Labor). Beispiele: "Laborant Analytik", "QC Analyst", "Labortechniker HPLC", "Laboranalytiker".
  - downstream_process: Aufreinigung/Prozessentwicklung Biopharma. Beispiele: "Downstream Processing Scientist", "Purification Chemist", "DSP Engineer", "Protein Purification".
  - mass_spec: Massenspektrometrie als Kern der Rolle. Beispiele: "Mass Spec Analyst", "LC-MS/MS Operator", "Proteomics Scientist".
  - data_steward: Datenmanagement, Datenqualität, FAIR, Research Data Management. Beispiele: "Data Steward", "Research Data Manager", "Clinical Data Manager".
  - scientific_software: Softwareentwicklung für Wissenschaft/Labor/Forschung. Beispiele: "Scientific Programmer", "Research Software Engineer", "Scientific Researcher / Research Engineer", "LIMS Developer".
  - wet_lab_rnd: molekularbiologische/biochemische Forschung & Entwicklung im Nasslabor (Klonierung, Proteinexpression und -aufreinigung, Assay-Entwicklung, Zellkultur, CRISPR, akademische Forschungsstellen in Biologie/Biomedizin). Beispiele: "Protein Engineer", "Research Associate Molecular Biology", "Scientist Cell Line Development", "PhD/PostDoc Position Cell Biology", "Master Thesis Student Biotech". NICHT für reine Produktions-/Routinetätigkeit ohne F&E-Anteil (das ist "other").
  - other: NUR wenn keine der neun Kategorien passt (z. B. HR, Vertrieb, Einkauf, Personalverrechnung, reine IT-Administration, Produktions-/Logistikrollen, Pharmareferenten). Eine wissenschaftlich-technische Life-Science-Rolle mit F&E- oder Analytikanteil ist praktisch nie "other" — im Zweifel die nächstliegende Fachkategorie wählen.
- Daten (contract_end, application_deadline) als ISO YYYY-MM-DD.
- field_evidence: Für jeden gesetzten kritischen Wert das kürzeste WÖRTLICHE Zitat
  eintragen. Für false/null/unknown ist kein Beleg nötig. Ohne Beleg bleibt der Wert
  false/null/unknown. Das gilt besonders für PhD, Jahre, Deutsch, Gehalt und Vertrag.
- Nichts erfinden. null ist erlaubt und richtig, wenn die Information fehlt.
- summary_2_lines: maximal zwei Sätze, was die Stelle ist und was verlangt wird."""


def _posting_content(row: sqlite3.Row) -> str:
    return (
        f"Titel: {row['raw_title']}\n"
        f"Firma: {row['raw_company'] or 'unbekannt'}\n"
        f"Ort: {row['raw_location'] or 'unbekannt'}\n\n"
        f"{(row['raw_text'] or '')[:30000]}"
    )


def pending_raws(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Kanonische Roh-Postings ohne Extraktion für aktuelle Version und Modell."""
    return conn.execute(
        """SELECT r.* FROM postings_raw r
           LEFT JOIN postings p ON p.raw_id = r.id AND p.schema_version = ? AND p.model = ?
           WHERE r.duplicate_of IS NULL AND p.id IS NULL
           ORDER BY r.id""",
        (SCHEMA_VERSION, llm.EXTRACT_MODEL),
    ).fetchall()


def _evidence_present(evidence: str | None, source: str) -> bool:
    needle = norm_text(evidence)
    return bool(needle and needle in norm_text(source))


def _numbers_in_evidence(evidence: str | None) -> list[float]:
    if not evidence:
        return []
    values: list[float] = []
    groups = re.findall(r"\d(?:[\d.,\s]*\d)?", evidence)
    for group in groups:
        token = re.sub(r"\s", "", group)
        if "." in token and "," in token:
            decimal = "." if token.rfind(".") > token.rfind(",") else ","
            thousands = "," if decimal == "." else "."
            token = token.replace(thousands, "").replace(decimal, ".")
        elif "." in token or "," in token:
            separator = "." if "." in token else ","
            parts = token.split(separator)
            if len(parts) == 2 and len(parts[1]) in {1, 2}:
                token = ".".join(parts)
            else:
                token = "".join(parts)
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values


def _number_in_evidence(value: float | int, evidence: str | None) -> bool:
    return any(
        math.isclose(number, float(value), rel_tol=0, abs_tol=0.5)
        for number in _numbers_in_evidence(evidence)
    )


def _salary_in_evidence(ex: Extraction, evidence: str | None) -> bool:
    if ex.salary_min_eur_month is None:
        return True
    if _number_in_evidence(ex.salary_min_eur_month, evidence):
        return True
    return ex.salary_basis == "yearly" and _number_in_evidence(
        ex.salary_min_eur_month * 14, evidence
    )


def _annual_salary_in_evidence(evidence: str | None) -> float | None:
    plausible = [number for number in _numbers_in_evidence(evidence) if number >= 5000]
    return max(plausible, default=None)


def _sanitize_extraction(raw: sqlite3.Row, ex: Extraction) -> Extraction:
    """Beleglose kritische Werte verwerfen, statt sie später als Fakten zu behandeln."""
    source = _posting_content(raw)
    data = ex.model_dump()
    # Dieses Feld ist Teil des gespeicherten Schemas, aber ausschließlich
    # Python darf es befüllen. Modell-generierte Selbstkritik ist weder stabil
    # noch eine tatsächlich ausgeführte Validierung.
    warnings: list[str] = []

    requirements: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for requirement in ex.requirements:
        key = (norm_text(requirement.name), requirement.importance)
        if not key[0] or key in seen:
            continue
        if not _evidence_present(requirement.evidence, source):
            warnings.append(f"Anforderung ohne Originalbeleg verworfen: {requirement.name}")
            continue
        seen.add(key)
        requirements.append(requirement.model_dump())
    data["requirements"] = requirements
    data["must_skills"] = [r["name"] for r in requirements if r["importance"] == "must"]
    data["nice_skills"] = [r["name"] for r in requirements if r["importance"] == "nice"]

    evidence = ex.field_evidence
    checks = {
        "phd_required": ex.phd_required,
        "years_experience_min": ex.years_experience_min is not None,
        "german_required": ex.german_required,
        "salary_min_eur_month": ex.salary_min_eur_month is not None,
        "workplace_mode": ex.workplace_mode != "unknown",
        "contract_type": ex.contract_type != "unknown",
        "contract_end": ex.contract_end is not None,
        "application_deadline": ex.application_deadline is not None,
    }
    fallbacks = {
        "phd_required": False,
        "years_experience_min": None,
        "german_required": False,
        "salary_min_eur_month": None,
        "workplace_mode": "unknown",
        "contract_type": "unknown",
        "contract_end": None,
        "application_deadline": None,
    }
    for field, active in checks.items():
        quote = getattr(evidence, field)
        valid = not active or _evidence_present(quote, source)
        if field == "salary_min_eur_month" and ex.salary_min_eur_month is not None:
            if ex.salary_basis == "yearly":
                annual_salary = _annual_salary_in_evidence(quote)
                valid = valid and annual_salary is not None
                if valid and annual_salary is not None:
                    data[field] = round(annual_salary / 14, 2)
            else:
                valid = valid and _salary_in_evidence(ex, quote)
        if field == "years_experience_min" and ex.years_experience_min is not None:
            valid = valid and _number_in_evidence(ex.years_experience_min, quote)
        if active and not valid:
            data[field] = fallbacks[field]
            warnings.append(f"{field} mangels Originalbeleg verworfen")

    if data["salary_min_eur_month"] is None:
        data["salary_basis"] = None

    raw_title = (raw["raw_title"] or "").replace("\u00ad", "").strip()
    if raw_title and fuzz.token_set_ratio(norm_text(raw_title), norm_text(ex.title_norm)) < 35:
        data["title_norm"] = raw_title
        warnings.append("title_norm wegen geringer Übereinstimmung auf Originaltitel zurückgesetzt")
    data["validation_warnings"] = warnings
    return Extraction.model_validate(data)


def _store(conn: sqlite3.Connection, raw: sqlite3.Row, ex: Extraction) -> None:
    conn.execute(
        """INSERT INTO postings (raw_id, company_id, site_id, extracted_json, schema_version, model, extracted_at)
           VALUES (?,?,NULL,?,?,?,?)
           ON CONFLICT(raw_id) DO UPDATE SET
             company_id=excluded.company_id, site_id=excluded.site_id,
             extracted_json=excluded.extracted_json, schema_version=excluded.schema_version,
             model=excluded.model, extracted_at=excluded.extracted_at""",
        (
            raw["id"],
            match_company(conn, raw["raw_company"]),
            ex.model_dump_json(),
            SCHEMA_VERSION,
            llm.EXTRACT_MODEL,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def extract_one(raw: sqlite3.Row) -> Extraction:
    result = llm.parse_structured(
        SYSTEM_PROMPT,
        _posting_content(raw),
        Extraction,
        max_tokens=3500,
        model=llm.EXTRACT_MODEL,
        think=False,
        temperature=0,
    )
    return _sanitize_extraction(raw, result)


def extract_pending(conn: sqlite3.Connection, limit: int | None = None) -> int:
    import typer

    rows = pending_raws(conn)
    if limit:
        rows = rows[:limit]
    if len(rows) > BATCH_THRESHOLD and llm.BACKEND == "anthropic":
        return _extract_via_batch(conn, rows)
    done = 0
    with typer.progressbar(rows, label="  Extraktion", show_pos=True) as bar:
        for raw in bar:
            try:
                ex = extract_one(raw)
            except Exception as e:  # noqa: BLE001 — ein kaputtes Inserat stoppt nicht den Lauf
                print(f"\n  Extraktion fehlgeschlagen für raw_id={raw['id']}: {e}")
                continue
            _store(conn, raw, ex)
            done += 1
    return done


def _extract_via_batch(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> int:
    """Backfill über die Batch-API (50 % Preis). Blockiert bis zum Ende des Batches."""
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = llm.client()
    by_id = {str(r["id"]): r for r in rows}
    schema = Extraction.model_json_schema()
    batch = client.messages.batches.create(
        requests=[
            Request(
                custom_id=rid,
                params=MessageCreateParamsNonStreaming(
                    model=llm.EXTRACT_MODEL,
                    max_tokens=3500,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": _posting_content(raw)}],
                    output_config={"format": {"type": "json_schema", "schema": schema}},
                ),
            )
            for rid, raw in by_id.items()
        ]
    )
    print(f"  Batch {batch.id} mit {len(rows)} Inseraten gestartet …")
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        time.sleep(60)
    done = 0
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            print(f"  Batch-Request {result.custom_id}: {result.result.type}")
            continue
        msg = result.result.message
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            ex = _sanitize_extraction(
                by_id[result.custom_id], Extraction.model_validate(json.loads(text))
            )
        except Exception as e:  # noqa: BLE001
            print(f"  Batch-Parse fehlgeschlagen für {result.custom_id}: {e}")
            continue
        _store(conn, by_id[result.custom_id], ex)
        done += 1
    return done
