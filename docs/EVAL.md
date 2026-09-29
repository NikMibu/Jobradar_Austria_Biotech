# Evaluation

Heimspiel trennt drei Messungen, weil Rollenklassifikation, vollständige
Extraktion und persönliche Rangfolge unterschiedliche Wahrheiten haben.

## 1. Schneller Rollen-Smoke-Test

```bash
HEIMSPIEL_LLM=ollama uv run heimspiel eval-roles \
  --models qwen3.5:9b,qwen3.8:27b --n-random 20
```

Die Keyword-Referenz ist bewusst nur ein Smoke-Test. Allgemeine Begriffe wie
„Validation“ können fachlich außerhalb CSV/GMP liegen und deshalb irreführende
Sollwerte erzeugen.

## 2. Extraktionsfelder

`data/eval/extraction-labels.jsonl` enthält 50 handgeprüfte Inserate aus allen
Quellen und Rollenfamilien. Eine Zeile kann alle oder nur ausgewählte Felder
prüfen:

```json
{"raw_id":123,"expected":{"role_family":"bioinformatics","phd_required":false,"years_experience_min":2,"salary_min_eur_month":4200,"must_skills":["Python","Nextflow"]}}
```

```bash
HEIMSPIEL_LLM=ollama uv run heimspiel eval-extraction \
  --labels data/eval/extraction-labels.jsonl \
  --models qwen3.5:9b,qwen3.8:27b
```

Enums, Booleans, Zahlen und Daten werden exakt verglichen. Skilllisten melden
Precision und Recall. Zielwerte für das Instruct-Modell sind mindestens 98 %
valide Erstantworten, 90 % Rollen-Accuracy, 95 % für kritische Bool-/Enum-Felder
und 85/75 % Skill-Precision/Recall. Eine neue Prompt-/Schema-Version wird erst
nach diesem Vergleich als Standard gesetzt.

## 3. Persönliches Ranking

Im Job-Drawer werden Stellen mit `Passt`, `Vielleicht` oder `Nein` markiert.
`Labels exportieren` erzeugt JSONL inklusive Posting-ID und Profilversion.

```bash
HEIMSPIEL_LLM=ollama uv run heimspiel eval-ranking \
  --labels ~/Downloads/heimspiel-ranking-labels-2026-08-26.jsonl \
  --models qwen3.8:27b,qwen3.5:9b
```

Das Eval verändert keine produktiven Scores. Es meldet Modellfehler,
Durchschnittsscores je Label, Spearman-Rangkorrelation und Precision@10/20.
Labels einer anderen Profilversion werden abgelehnt, statt unbemerkt mit einem
geänderten Profil verglichen zu werden.
