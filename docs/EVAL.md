# Eval — Extraktions-Qualität

> Status: Gerüst (M6). Das Eval-Set schützt vor den zwei teuersten LLM-Fehlern
> aus SPEC §13: erfundenes Gehalt und erfundene PhD-Pflicht. Das Standortfeld
> wird gesondert geprüft (Werk ≠ Firmensitz).

## Vorgehen

1. **50 Inserate handlabeln:** `data/eval/labels.jsonl`, eine Zeile pro Inserat:
   `{"raw_id": 123, "expected": {<Extraction-Felder>}}`. Quelle: echte Inserate
   aus `postings_raw`, quer über alle Quellen und Rollenfamilien.
2. **Extraktion laufen lassen** (normaler Cache-Pfad) und pro Feld vergleichen.
3. **Metriken pro Feld:** Accuracy für Enums/Bools, Precision/Recall für Listen
   (must_skills etc.), exakte Übereinstimmung für Zahlen/Daten mit null-Toleranz.

## Ergebnis-Tabelle (auszufüllen)

| Feld | Accuracy / P/R | Haiku 4.5 | Lokal (Qwen 2.5 7B) |
|---|---|---|---|
| role_family | Acc | – | – |
| seniority | Acc | – | – |
| phd_required | Acc | – | – |
| salary_min_eur_month | Acc (±0) | – | – |
| german_required | Acc | – | – |
| location_text | Acc (manuell) | – | – |
| must_skills | P / R | – | – |
| contract_type | Acc | – | – |

## Modell-Switch

`HEIMSPIEL_MODEL` und `ANTHROPIC_BASE_URL` schalten das Backend um
(z. B. Ollama hinter einem Anthropic-kompatiblen Proxy / LiteLLM) — damit
läuft dasselbe Eval-Set gegen Haiku und das lokale Modell.
