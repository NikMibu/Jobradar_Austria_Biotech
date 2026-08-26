"""Extraktions-Cache und Schema-Validierung — ohne echte API-Calls."""

import json

import pytest

from heimspiel import extract as ex
from heimspiel.extract import Extraction, FieldEvidence, Requirement
from heimspiel.sources.base import RawPosting, store_postings

SAMPLE = {
    "title_norm": "Bioinformatiker NGS",
    "role_family": "bioinformatics",
    "seniority": "junior",
    "education_min": "msc",
    "phd_required": False,
    "years_experience_min": 1,
    "must_skills": ["Python"],
    "nice_skills": [],
    "domain_keywords": ["NGS"],
    "german_required": True,
    "salary_min_eur_month": 3500,
    "salary_basis": "monthly_14x",
    "location_text": "Wien",
    "workplace_mode": "hybrid",
    "travel_share_pct": None,
    "start_text": None,
    "contract_type": "permanent",
    "contract_end": None,
    "application_deadline": None,
    "summary_2_lines": "NGS-Pipelines bauen. Python und Nextflow verlangt.",
}


def test_schema_roundtrip():
    e = Extraction.model_validate(SAMPLE)
    assert e.role_family == "bioinformatics"
    assert Extraction.model_validate_json(e.model_dump_json()) == e


def test_schema_rejects_invented_enum():
    with pytest.raises(ValueError):
        Extraction.model_validate({**SAMPLE, "role_family": "sales"})


def test_pending_skips_extracted_and_duplicates(conn, monkeypatch):
    store_postings(
        conn,
        [
            RawPosting("indeed", "1", None, "A", "F", "Wien", "t"),
            RawPosting("indeed", "2", None, "B", "F", "Wien", "t"),
            RawPosting("indeed", "3", None, "C", "F", "Wien", "t"),
        ],
    )
    conn.execute("UPDATE postings_raw SET duplicate_of=1 WHERE source_id='3'")
    conn.commit()
    assert len(ex.pending_raws(conn)) == 2  # Duplikat raus

    calls = []

    def fake_extract_one(raw):
        calls.append(raw["id"])
        return Extraction.model_validate(SAMPLE)

    monkeypatch.setattr(ex, "extract_one", fake_extract_one)
    assert ex.extract_pending(conn) == 2
    # Cache: zweiter Lauf extrahiert nichts mehr
    assert ex.extract_pending(conn) == 0
    assert len(calls) == 2

    row = conn.execute("SELECT extracted_json, schema_version FROM postings LIMIT 1").fetchone()
    assert row["schema_version"] == ex.SCHEMA_VERSION
    assert json.loads(row["extracted_json"])["title_norm"] == "Bioinformatiker NGS"


def test_restore_resets_stale_site_id(conn, monkeypatch):
    # Regression: eine Re-Extraktion (Schema-/Modell-Wechsel) muss eine zuvor
    # zugeordnete site_id verwerfen, sonst zeigt die Karte den alten Standort
    # weiter an, obwohl die neue Extraktion einen anderen location_text liefert
    # (beobachtet: "SA, AT" von einer alten Extraktion blieb an einer Stelle
    # hängen, deren Neu-Extraktion "Salzburg" ergab — Karte zeigte Wien).
    store_postings(conn, [RawPosting("indeed", "1", None, "A", "F", "Wien", "t")])
    raw = conn.execute("SELECT * FROM postings_raw WHERE source_id='1'").fetchone()
    ex._store(conn, raw, Extraction.model_validate(SAMPLE))
    conn.execute(
        "UPDATE postings SET site_id=999 WHERE raw_id=?", (raw["id"],)
    )  # simuliert bereits aufgelösten Standort
    conn.commit()

    ex._store(conn, raw, Extraction.model_validate({**SAMPLE, "location_text": "Salzburg"}))
    row = conn.execute("SELECT site_id FROM postings WHERE raw_id=?", (raw["id"],)).fetchone()
    assert row["site_id"] is None


def test_cache_is_bound_to_extraction_model(conn, monkeypatch):
    store_postings(conn, [RawPosting("indeed", "1", None, "A", "F", "Wien", "Text")])
    raw = conn.execute("SELECT * FROM postings_raw").fetchone()
    ex._store(conn, raw, Extraction.model_validate(SAMPLE))
    assert ex.pending_raws(conn) == []
    monkeypatch.setattr(ex.llm, "EXTRACT_MODEL", "anderes-modell")
    assert [row["id"] for row in ex.pending_raws(conn)] == [raw["id"]]


def test_sanitizer_rejects_unsubstantiated_fields_and_requirements(conn):
    store_postings(
        conn,
        [
            RawPosting(
                "indeed",
                "1",
                None,
                "Bioinformatiker NGS",
                "F",
                "Wien",
                "Python ist erforderlich. Das Mindestgehalt beträgt EUR 3.500 monatlich.",
            )
        ],
    )
    raw = conn.execute("SELECT * FROM postings_raw").fetchone()
    candidate = Extraction.model_validate(
        {
            **SAMPLE,
            "title_norm": "Drilling",
            "years_experience_min": 5,
            "salary_min_eur_month": 3500,
            "requirements": [
                Requirement(name="Python", importance="must", evidence="Python ist erforderlich."),
                Requirement(name="Nextflow", importance="nice", evidence="Nextflow von Vorteil"),
            ],
            "field_evidence": FieldEvidence(
                years_experience_min="mindestens fünf Jahre",
                salary_min_eur_month="EUR 3.500 monatlich",
            ),
            "validation_warnings": ["vom Modell erfunden"],
        }
    )
    cleaned = ex._sanitize_extraction(raw, candidate)
    assert cleaned.title_norm == "Bioinformatiker NGS"
    assert cleaned.years_experience_min is None
    assert cleaned.salary_min_eur_month == 3500
    assert cleaned.must_skills == ["Python"]
    assert cleaned.nice_skills == []
    assert cleaned.validation_warnings
    assert "vom Modell erfunden" not in cleaned.validation_warnings


def test_sanitizer_accepts_annual_salary_converted_to_fourteen_payments(conn):
    store_postings(
        conn,
        [
            RawPosting(
                "indeed",
                "salary",
                None,
                "Data Scientist",
                "F",
                "Wien",
                "Das Jahresbruttogehalt beträgt mindestens EUR 49.000,0.",
            )
        ],
    )
    raw = conn.execute("SELECT * FROM postings_raw").fetchone()
    candidate = Extraction.model_validate(
        {
            **SAMPLE,
            # Lokale Modelle teilen Jahresgehälter häufig durch 12. Python
            # normalisiert anhand des belegten Jahreswerts deterministisch auf 14.
            "salary_min_eur_month": 4083.33,
            "salary_basis": "yearly",
            "field_evidence": FieldEvidence(
                salary_min_eur_month="Jahresbruttogehalt beträgt mindestens EUR 49.000,0"
            ),
        }
    )
    cleaned = ex._sanitize_extraction(raw, candidate)
    assert cleaned.salary_min_eur_month == 3500
    assert cleaned.salary_basis == "yearly"


def test_sanitizer_clears_basis_when_salary_is_unsubstantiated(conn):
    store_postings(
        conn,
        [RawPosting("indeed", "salary", None, "Data Scientist", "F", "Wien", "Kein Gehalt")],
    )
    raw = conn.execute("SELECT * FROM postings_raw").fetchone()
    candidate = Extraction.model_validate(
        {**SAMPLE, "salary_basis": "yearly", "field_evidence": FieldEvidence()}
    )
    cleaned = ex._sanitize_extraction(raw, candidate)
    assert cleaned.salary_min_eur_month is None
    assert cleaned.salary_basis is None
