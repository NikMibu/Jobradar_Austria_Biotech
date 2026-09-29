"""Schneller Modellvergleich für die role_family-Klassifikation (Plan Teil 1).

Kein volles Hand-Labeling: die ~10 nachweislich falsch klassifizierten Postings
(Titel enthält bioinformat/downstream/purification/…) plus N zufällige werden
gegen mehrere Modelle nur auf role_family/seniority re-klassifiziert. Für die
Verdachtsfälle liefert der Titel selbst die Referenz (Keyword → erwartete Familie)."""

import random
import re
import sqlite3
from typing import Literal

from pydantic import BaseModel

from . import llm
from .extract import SYSTEM_PROMPT

# Titel-Keyword → erwartete role_family (nur für die eindeutigen Verdachtsfälle)
EXPECTED_BY_KEYWORD: list[tuple[str, str]] = [
    (r"bioinformat", "bioinformatics"),
    (r"computational biolog", "bioinformatics"),
    (r"downstream|purification|\bdsp\b", "downstream_process"),
    (r"mass spec|lc-ms|massenspektro|proteomic", "mass_spec"),
    (r"research engineer|scientific (software|programmer|researcher)", "scientific_software"),
    (r"data steward|research data manager", "data_steward"),
    (r"\bcsv\b|validierung|qualifizierung|validation", "csv_qa_validation"),
]


class RoleOnly(BaseModel):
    role_family: Literal[
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
    seniority: Literal["entry", "junior", "mid", "senior"]


def expected_family(title: str) -> str | None:
    t = title.lower()
    for pattern, family in EXPECTED_BY_KEYWORD:
        if re.search(pattern, t):
            return family
    return None


def pick_sample(conn: sqlite3.Connection, n_random: int = 20) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT r.id, r.raw_title, r.raw_company, r.raw_location, r.raw_text
           FROM postings_raw r WHERE r.duplicate_of IS NULL AND r.raw_text IS NOT NULL"""
    ).fetchall()
    suspects = [r for r in rows if expected_family(r["raw_title"] or "")]
    rest = [r for r in rows if r not in suspects]
    random.seed(42)
    return suspects + random.sample(rest, min(n_random, len(rest)))


def run(conn: sqlite3.Connection, models: list[str], n_random: int = 20) -> None:
    sample = pick_sample(conn, n_random)
    suspects = [r for r in sample if expected_family(r["raw_title"] or "")]
    print(f"{len(sample)} Postings ({len(suspects)} Verdachtsfälle mit Titel-Referenz)\n")

    results: dict[str, dict[int, str]] = {m: {} for m in models}
    correct: dict[str, int] = dict.fromkeys(models, 0)
    for m in models:
        print(f"— {m}")
        for r in sample:
            user = (
                f"Titel: {r['raw_title']}\nFirma: {r['raw_company'] or '?'}\n"
                f"Ort: {r['raw_location'] or '?'}\n\n{(r['raw_text'] or '')[:6000]}"
            )
            try:
                res = llm.parse_structured(SYSTEM_PROMPT, user, RoleOnly, max_tokens=200, model=m)
                results[m][r["id"]] = res.role_family
            except Exception as e:  # noqa: BLE001
                results[m][r["id"]] = f"FEHLER: {e}"
        for r in suspects:
            if results[m].get(r["id"]) == expected_family(r["raw_title"]):
                correct[m] += 1

    print("\n## Verdachtsfälle (Referenz aus Titel)\n")
    header = "| Titel | erwartet | " + " | ".join(models) + " |"
    print(header)
    print("|" + "---|" * (2 + len(models)))
    for r in suspects:
        exp = expected_family(r["raw_title"])
        cells = " | ".join(
            ("✓ " if results[m].get(r["id"]) == exp else "✗ ") + str(results[m].get(r["id"]))
            for m in models
        )
        print(f"| {r['raw_title'][:55]} | {exp} | {cells} |")

    print("\n## Score auf Verdachtsfällen")
    for m in models:
        print(f"  {m}: {correct[m]}/{len(suspects)}")

    print("\n## Abweichungen bei den Zufalls-Postings")
    for r in sample:
        if r in suspects:
            continue
        families = {results[m].get(r["id"]) for m in models}
        if len(families) > 1:
            detail = ", ".join(f"{m}={results[m].get(r['id'])}" for m in models)
            print(f"  {r['raw_title'][:60]}: {detail}")
