"""LLM-Extraktion ins feste Schema (SPEC §5).

Cache-Key = content_hash + schema_version: ein Posting wird nur neu extrahiert,
wenn sich Inhalt oder Schema ändern. Backfills > 500 Inserate laufen über die
Batch-API (halber Preis)."""

import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from . import llm
from .normalize import match_company

# v2: role_family-Definitionen im Prompt (236× fälschlich "other" mit v1)
# v3: + wet_lab_rnd — Nasslabor-F&E (CRISPR Protein Engineer u. Ä. landeten in "other")
SCHEMA_VERSION = 3
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


class Extraction(BaseModel):
    title_norm: str
    role_family: RoleFamily
    seniority: Literal["entry", "junior", "mid", "senior"]
    education_min: Literal["none", "bsc", "msc", "phd"]
    phd_required: bool
    years_experience_min: int | None = None
    must_skills: list[str] = Field(default_factory=list)
    nice_skills: list[str] = Field(default_factory=list)
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


SYSTEM_PROMPT = """Du extrahierst Stellenanzeigen (deutsch oder englisch, meist Österreich) in ein festes Schema.

Regeln:
- Gehalt nur übernehmen, wenn im Text eine konkrete Zahl steht (österreichische Inserate müssen das kollektivvertragliche Mindestgehalt nennen). salary_basis: monthly_14x für Monatsbrutto (14x), yearly für Jahresbrutto.
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
    """Kanonische Roh-Postings ohne aktuelle Extraktion (Cache über schema_version)."""
    return conn.execute(
        """SELECT r.* FROM postings_raw r
           LEFT JOIN postings p ON p.raw_id = r.id AND p.schema_version = ?
           WHERE r.duplicate_of IS NULL AND p.id IS NULL
           ORDER BY r.id""",
        (SCHEMA_VERSION,),
    ).fetchall()


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
    return llm.parse_structured(SYSTEM_PROMPT, _posting_content(raw), Extraction, max_tokens=2500)


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
                    max_tokens=2500,
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
            ex = Extraction.model_validate(json.loads(text))
        except Exception as e:  # noqa: BLE001
            print(f"  Batch-Parse fehlgeschlagen für {result.custom_id}: {e}")
            continue
        _store(conn, by_id[result.custom_id], ex)
        done += 1
    return done
