"""LLM-Backend-Abstraktion: Anthropic (default) oder Ollama (Null-Kosten-Variante, SPEC §12).

Konfiguration über Umgebungsvariablen:
  HEIMSPIEL_LLM=anthropic|ollama   Backend (default: anthropic)
  HEIMSPIEL_MODEL=<name>           Modell (default: claude-haiku-4-5 bzw. qwen2.5:7b)
  HEIMSPIEL_OLLAMA_URL=<url>       Ollama-Server (default: http://localhost:11434)

Beide Backends liefern Pydantic-validierte Structured Outputs: Anthropic über
messages.parse (mit Prompt-Caching), Ollama über /api/chat mit format=<JSON-Schema>.
"""

import os
from functools import lru_cache

import requests
from pydantic import BaseModel

BACKEND = os.environ.get("HEIMSPIEL_LLM", "anthropic")
# eval-roles auf 29 Verdachtsfällen: qwen3.5:9b 22/29, qwen3:8b 18/29 (qwen2.5:7b
# 7/11 auf der alten 11er-Stichprobe, gpt-oss:20b liefert mit format=JSON-Schema
# nur leere Antworten). qwen3:8b fehlklassifizierte scientific_software
# systematisch als data_science — bei qwen3.5:9b kaum noch der Fall.
EXTRACT_MODEL = os.environ.get(
    "HEIMSPIEL_MODEL", "claude-haiku-4-5" if BACKEND == "anthropic" else "qwen3.5:9b"
)
OLLAMA_URL = os.environ.get("HEIMSPIEL_OLLAMA_URL", "http://localhost:11434")


@lru_cache(maxsize=1)
def client():
    import anthropic

    return anthropic.Anthropic()


def parse_structured[T: BaseModel](
    system: str, user: str, output: type[T], max_tokens: int = 2500, model: str | None = None
) -> T:
    """Ein Structured-Output-Call, Backend-unabhängig. model überschreibt EXTRACT_MODEL
    (für Modellvergleiche wie `heimspiel eval-roles`)."""
    if BACKEND == "ollama":
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model or EXTRACT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": output.model_json_schema(),
                "stream": False,
                # Qwen3 & Co. schreiben sonst <think>-Blöcke vor das JSON und
                # brechen Structured Output (Ollama >= 0.9 kennt den Parameter)
                "think": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=600,
        )
        resp.raise_for_status()
        return output.model_validate_json(resp.json()["message"]["content"])

    response = client().messages.parse(
        model=model or EXTRACT_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=output,
    )
    return response.parsed_output
