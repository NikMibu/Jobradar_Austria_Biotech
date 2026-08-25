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
EXTRACT_MODEL = os.environ.get(
    "HEIMSPIEL_MODEL", "claude-haiku-4-5" if BACKEND == "anthropic" else "qwen2.5:7b"
)
OLLAMA_URL = os.environ.get("HEIMSPIEL_OLLAMA_URL", "http://localhost:11434")


@lru_cache(maxsize=1)
def client():
    import anthropic

    return anthropic.Anthropic()


def parse_structured[T: BaseModel](
    system: str, user: str, output: type[T], max_tokens: int = 2500
) -> T:
    """Ein Structured-Output-Call, Backend-unabhängig."""
    if BACKEND == "ollama":
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": EXTRACT_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": output.model_json_schema(),
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=600,
        )
        resp.raise_for_status()
        return output.model_validate_json(resp.json()["message"]["content"])

    response = client().messages.parse(
        model=EXTRACT_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=output,
    )
    return response.parsed_output
