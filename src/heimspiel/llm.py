"""LLM-Backend-Abstraktion: Anthropic oder Ollama (lokaler Standard).

Konfiguration über Umgebungsvariablen:
  HEIMSPIEL_LLM=anthropic|ollama   Backend (default: ollama)
  HEIMSPIEL_EXTRACT_MODEL=<name>   Modell für strukturierte Extraktion (Ollama-Default: qwen3.8:27b)
  HEIMSPIEL_SCORE_MODEL=<name>     Modell für das fachliche Assessment (Ollama-Default: qwen3.8:27b)
  HEIMSPIEL_MODEL=<name>           kompatibler Fallback für beide Aufgaben
  HEIMSPIEL_OLLAMA_URL=<url>       Ollama-Server (default: http://localhost:11434)

Der Ollama-Default ist ein einziges Modell für beide Rollen: qwen3.8:27b deckt die
Instruct-Extraktion (think=false) und das Reasoning-Assessment (natives think, sofern
/api/show die Capability meldet) ab.

Beide Backends liefern Pydantic-validierte Structured Outputs: Anthropic über
messages.parse (mit Prompt-Caching), Ollama über /api/chat mit format=<JSON-Schema>.
"""

import json
import os
from collections.abc import Iterable
from functools import lru_cache

import requests
from pydantic import BaseModel

BACKEND = os.environ.get("HEIMSPIEL_LLM", "ollama")
_LEGACY_MODEL = os.environ.get("HEIMSPIEL_MODEL")
_ANTHROPIC_DEFAULT = "claude-haiku-4-5"
_OLLAMA_EXTRACT_DEFAULT = "qwen3.8:27b"
_OLLAMA_SCORE_DEFAULT = "qwen3.8:27b"
EXTRACT_MODEL = os.environ.get(
    "HEIMSPIEL_EXTRACT_MODEL",
    _LEGACY_MODEL or (_ANTHROPIC_DEFAULT if BACKEND == "anthropic" else _OLLAMA_EXTRACT_DEFAULT),
)
SCORE_MODEL = os.environ.get(
    "HEIMSPIEL_SCORE_MODEL",
    _LEGACY_MODEL or (_ANTHROPIC_DEFAULT if BACKEND == "anthropic" else _OLLAMA_SCORE_DEFAULT),
)
OLLAMA_URL = os.environ.get("HEIMSPIEL_OLLAMA_URL", "http://localhost:11434")
OLLAMA_CONTEXT = int(os.environ.get("HEIMSPIEL_OLLAMA_CONTEXT", "16384"))
OLLAMA_SEED = int(os.environ.get("HEIMSPIEL_OLLAMA_SEED", "42"))


def _canonical_model_name(name: str) -> str:
    normalized = name.strip().lower()
    return normalized.removesuffix(":latest")


def ensure_available(models: Iterable[str]) -> None:
    """Ollama-Verbindung und lokale Modelle einmal vor einem Batch prüfen."""
    if BACKEND == "anthropic":
        return
    if BACKEND != "ollama":
        raise RuntimeError(f"Unbekanntes HEIMSPIEL_LLM-Backend: {BACKEND!r}")
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as error:
        raise RuntimeError(
            f"Ollama ist unter {OLLAMA_URL} nicht erreichbar. Starte zuerst `ollama serve`."
        ) from error

    installed = {
        _canonical_model_name(name)
        for item in payload.get("models", [])
        for name in (item.get("name"), item.get("model"))
        if isinstance(name, str)
    }
    missing = [model for model in models if _canonical_model_name(model) not in installed]
    if missing:
        commands = ", ".join(
            f"`ollama run {model}`" if model.startswith("hf.co/") else f"`ollama pull {model}`"
            for model in missing
        )
        raise RuntimeError(f"Ollama-Modell(e) fehlen: {', '.join(missing)}. Installieren mit {commands}.")


@lru_cache(maxsize=32)
def _ollama_supports_thinking(model: str) -> bool:
    """Native `think`-Capability abfragen; Reasoning im Modellnamen reicht nicht.

    Insbesondere das Ministral-Reasoning-GGUF reasoniert modellintern, wird von
    Ollama aber nicht als Modell mit separatem `message.thinking` registriert.
    """
    response = requests.post(
        f"{OLLAMA_URL}/api/show",
        json={"model": model},
        timeout=30,
    )
    response.raise_for_status()
    return "thinking" in response.json().get("capabilities", [])


@lru_cache(maxsize=1)
def client():
    import anthropic

    return anthropic.Anthropic()


def parse_structured[T: BaseModel](
    system: str,
    user: str,
    output: type[T],
    max_tokens: int = 2500,
    model: str | None = None,
    *,
    think: bool = False,
    temperature: float = 0,
    seed: int | None = None,
) -> T:
    """Ein Structured-Output-Call, Backend-unabhängig. model überschreibt EXTRACT_MODEL
    (für Modellvergleiche wie `heimspiel eval-roles`)."""
    if BACKEND == "ollama":
        schema = output.model_json_schema()
        selected_model = model or EXTRACT_MODEL
        native_think = think and _ollama_supports_thinking(selected_model)
        grounded_system = (
            f"{system}\n\nAntworte ausschließlich entsprechend diesem JSON-Schema:\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": selected_model,
                "messages": [
                    {"role": "system", "content": grounded_system},
                    {"role": "user", "content": user},
                ],
                "format": schema,
                "stream": False,
                "think": native_think,
                "options": {
                    "num_predict": max_tokens,
                    "num_ctx": OLLAMA_CONTEXT,
                    "temperature": temperature,
                    "seed": OLLAMA_SEED if seed is None else seed,
                },
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
