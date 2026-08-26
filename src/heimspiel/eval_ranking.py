"""Read-only Ranking-Evaluation gegen im Browser exportierte persönliche Labels."""

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from .config import Profile
from .extract import Extraction
from .match import _assessment_call, compute_score

LABEL_CANONICAL = {
    "yes": "yes",
    "maybe": "maybe",
    "no": "no",
    "passt": "yes",
    "vielleicht": "maybe",
    "nein": "no",
}
LABEL_VALUE = {"yes": 2, "maybe": 1, "no": 0}


def load_labels(path: Path, profile: Profile) -> list[dict]:
    labels: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        item = json.loads(line)
        label = str(item.get("label", "")).lower()
        if label not in LABEL_CANONICAL:
            raise ValueError(f"Zeile {number}: unbekanntes Label {label!r}")
        if item.get("profile_version") not in {None, profile.profile_version}:
            raise ValueError(
                f"Zeile {number}: Profilversion {item['profile_version']} != {profile.profile_version}"
            )
        item["label"] = LABEL_CANONICAL[label]
        labels.append(item)
    return labels


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + end - 1) / 2
        for index in order[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    numerator = sum((x - mx) * (y - my) for x, y in zip(rx, ry, strict=True))
    denominator = math.sqrt(
        sum((x - mx) ** 2 for x in rx) * sum((y - my) ** 2 for y in ry)
    )
    return numerator / denominator if denominator else None


def _mean(by_label: dict[str, list[int]], label: str) -> str:
    values = by_label[label]
    return f"{sum(values) / len(values):.1f}" if values else "–"


def _precision(scored: list[tuple[int, str]], k: int) -> str:
    top = scored[:k]
    return (
        f"{sum(label in {'yes', 'passt'} for _, label in top) / len(top):.2f}"
        if top
        else "–"
    )


def run(conn: sqlite3.Connection, profile: Profile, labels_path: Path, models: list[str]) -> None:
    labels = load_labels(labels_path, profile)
    rows = {
        row["posting_id"]: row
        for row in conn.execute(
            f"SELECT id AS posting_id, extracted_json FROM postings "
            f"WHERE id IN ({','.join('?' for _ in labels)})",
            tuple(item["posting_id"] for item in labels),
        ).fetchall()
    } if labels else {}
    missing = [item["posting_id"] for item in labels if item["posting_id"] not in rows]
    if missing:
        raise ValueError(f"Posting-IDs fehlen in der Datenbank: {missing}")

    print(f"{len(labels)} Labels, Profilversion {profile.profile_version}\n")
    print("| Modell | Fehler | Mittel Passt | Vielleicht | Nein | Spearman | P@10 | P@20 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for model in models:
        scored: list[tuple[int, str]] = []
        errors = 0
        by_label: dict[str, list[int]] = defaultdict(list)
        for item in labels:
            ex = Extraction.model_validate_json(rows[item["posting_id"]]["extracted_json"])
            try:
                assessment = _assessment_call(
                    ex, profile, model, think="reasoning" in model.lower()
                )
                score = compute_score(ex, profile, assessment).fit_score
            except Exception as error:  # noqa: BLE001
                errors += 1
                print(f"  {model}: posting {item['posting_id']} fehlgeschlagen: {error}")
                continue
            scored.append((score, item["label"]))
            by_label[item["label"]].append(score)

        scored.sort(reverse=True)
        xs = [float(score) for score, _ in scored]
        ys = [float(LABEL_VALUE[label]) for _, label in scored]
        rho = _spearman(xs, ys)

        print(
            f"| {model} | {errors} | {_mean(by_label, 'yes')} | "
            f"{_mean(by_label, 'maybe')} | {_mean(by_label, 'no')} | "
            f"{rho:.2f} | {_precision(scored, 10)} | {_precision(scored, 20)} |"
            if rho is not None
            else f"| {model} | {errors} | {_mean(by_label, 'yes')} | "
            f"{_mean(by_label, 'maybe')} | {_mean(by_label, 'no')} | – | "
            f"{_precision(scored, 10)} | {_precision(scored, 20)} |"
        )
