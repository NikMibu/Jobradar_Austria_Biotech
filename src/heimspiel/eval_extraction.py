"""Read-only Feld-Evaluation der Extraktion gegen handgelabelte echte Inserate."""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from . import llm
from .extract import SYSTEM_PROMPT, Extraction, _posting_content, _sanitize_extraction
from .normalize import norm_text


def _normalized_set(values: list[str]) -> set[str]:
    return {norm_text(value) for value in values if norm_text(value)}


def _list_counts(actual: list[str], expected: list[str]) -> tuple[int, int, int]:
    got, wanted = _normalized_set(actual), _normalized_set(expected)
    return len(got & wanted), len(got - wanted), len(wanted - got)


def run(conn: sqlite3.Connection, labels_path: Path, models: list[str]) -> None:
    labels = [
        json.loads(line)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rows = {
        row["id"]: row
        for row in conn.execute(
            f"SELECT * FROM postings_raw WHERE id IN ({','.join('?' for _ in labels)})",
            tuple(item["raw_id"] for item in labels),
        ).fetchall()
    } if labels else {}
    missing = [item["raw_id"] for item in labels if item["raw_id"] not in rows]
    if missing:
        raise ValueError(f"Raw-IDs fehlen in der Datenbank: {missing}")

    for model in models:
        exact: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        lists: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        errors = 0
        for item in labels:
            raw = rows[item["raw_id"]]
            try:
                result = llm.parse_structured(
                    SYSTEM_PROMPT,
                    _posting_content(raw),
                    Extraction,
                    max_tokens=3500,
                    model=model,
                    think=False,
                    temperature=0,
                )
                result = _sanitize_extraction(raw, result)
            except Exception as error:  # noqa: BLE001
                errors += 1
                print(f"  {model}: raw_id={item['raw_id']} fehlgeschlagen: {error}")
                continue
            actual = result.model_dump()
            for field, expected in item["expected"].items():
                if field in {"must_skills", "nice_skills", "domain_keywords"}:
                    counts = _list_counts(actual.get(field, []), expected)
                    lists[field] = [a + b for a, b in zip(lists[field], counts, strict=True)]
                else:
                    exact[field][1] += 1
                    exact[field][0] += int(actual.get(field) == expected)

        print(f"\n## {model}\n")
        print(f"Structured-Output-Fehler: {errors}/{len(labels)}")
        for field, (correct, total) in sorted(exact.items()):
            print(f"  {field}: {correct}/{total} ({correct / total:.1%})")
        for field, (true_positive, false_positive, false_negative) in sorted(lists.items()):
            precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0
            recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0
            print(f"  {field}: Precision {precision:.1%}, Recall {recall:.1%}")
