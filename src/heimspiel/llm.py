"""LLM-Backend-Abstraktion: Anthropic, OpenAI oder Ollama (lokaler Standard).

Konfiguration über Umgebungsvariablen:
  HEIMSPIEL_LLM=anthropic|openai|ollama   Backend (default: ollama)
  HEIMSPIEL_EXTRACT_MODEL=<name>   Modell für strukturierte Extraktion
  HEIMSPIEL_SCORE_MODEL=<name>     Modell für das fachliche Assessment
  HEIMSPIEL_MODEL=<name>           kompatibler Fallback für beide Aufgaben
  HEIMSPIEL_OLLAMA_URL=<url>       Ollama-Server (default: http://localhost:11434)
  HEIMSPIEL_OPENAI_REASONING=<effort>  reasoning_effort für den Score-Pfad (default: low)
  HEIMSPIEL_OPENAI_SERVICE_TIER=fast   Fast Mode (2x Preis, nur unterstützte Modelle); leer = Standard
  HEIMSPIEL_LLM_CONCURRENCY=<n>         parallele API-Calls (default: openai 6, anthropic 8, ollama 1)

Rollen-Defaults: Ollama qwen3.8:27b für beide Rollen, Anthropic claude-haiku-4-5,
OpenAI gpt-6-luna. OPENAI_API_KEY / OPENAI_BASE_URL liest das openai-SDK selbst.

Alle drei Backends liefern Pydantic-validierte Structured Outputs: Anthropic über
messages.parse (mit Prompt-Caching), OpenAI über chat.completions.parse (strict
JSON-Schema), Ollama über /api/chat. Das Schema trägt beim Ollama-Pfad zuerst nur
der Prompt; Ollamas `format`-Grammar unterdrückt bei einem großen verschachtelten
Schema optionale Arrays (z. B. `requirements` blieb leer) und wird deshalb nur als
Fallback bei einem Parse-Fehler geschickt.
"""

import json
import os
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import requests
from pydantic import BaseModel, ValidationError

BACKEND = os.environ.get("HEIMSPIEL_LLM", "ollama")
_LEGACY_MODEL = os.environ.get("HEIMSPIEL_MODEL")
_ANTHROPIC_DEFAULT = "claude-haiku-4-5"
_OPENAI_DEFAULT = "gpt-6-luna"
_OLLAMA_EXTRACT_DEFAULT = "qwen3.8:27b"
_OLLAMA_SCORE_DEFAULT = "qwen3.8:27b"


def _default_model(ollama_default: str) -> str:
    if BACKEND == "anthropic":
        return _ANTHROPIC_DEFAULT
    if BACKEND == "openai":
        return _OPENAI_DEFAULT
    return ollama_default


EXTRACT_MODEL = os.environ.get(
    "HEIMSPIEL_EXTRACT_MODEL", _LEGACY_MODEL or _default_model(_OLLAMA_EXTRACT_DEFAULT)
)
SCORE_MODEL = os.environ.get(
    "HEIMSPIEL_SCORE_MODEL", _LEGACY_MODEL or _default_model(_OLLAMA_SCORE_DEFAULT)
)
OLLAMA_URL = os.environ.get("HEIMSPIEL_OLLAMA_URL", "http://localhost:11434")
OLLAMA_CONTEXT = int(os.environ.get("HEIMSPIEL_OLLAMA_CONTEXT", "16384"))
OLLAMA_SEED = int(os.environ.get("HEIMSPIEL_OLLAMA_SEED", "42"))
OPENAI_REASONING_EFFORT = os.environ.get("HEIMSPIEL_OPENAI_REASONING", "low")
# Fast Mode: service_tier="fast" (bzw. "priority"). ~2,5x Durchsatz zum
# doppelten Token-Preis, nur auf unterstützten Modellen (dokumentiert für
# gpt-5.6-sol; für luna nicht garantiert). Leer/None = Standard-Tier.
OPENAI_SERVICE_TIER = os.environ.get("HEIMSPIEL_OPENAI_SERVICE_TIER") or None

# Parallele API-Calls (Extraktion/Scoring). Die Requests sind fast reine
# Netzw-Wartezeit, Threads geben dabei den GIL frei. Lokales Ollama profitiert
# nicht und würde nur das GPU-Queueing verschlechtern → dort Default 1.
# OpenAI-Default bewusst niedriger: bei einem 200k-TPM-Org-Limit sättigen
# schon ~6 gleichzeitige luna-Extraktionscalls die Minute; höher heißt nur
# mehr 429-Retries (die das SDK abfängt, s. _openai_client), kein Durchsatz.
_CONCURRENCY_DEFAULTS = {"openai": 6, "anthropic": 8}
LLM_CONCURRENCY = int(
    os.environ.get(
        "HEIMSPIEL_LLM_CONCURRENCY", str(_CONCURRENCY_DEFAULTS.get(BACKEND, 1))
    )
)


def parallel_map[I, O](
    fn: Callable[[I], O], items: Sequence[I], workers: int | None = None
) -> Iterator[tuple[I, O | Exception]]:
    """`fn` über `items` laufen lassen, bis zu LLM_CONCURRENCY gleichzeitig.

    Yields `(item, ergebnis)` bzw. `(item, exception)` in Abschlussreihenfolge —
    der Aufrufer entscheidet, wie er mit Fehlern umgeht. Bei Concurrency 1 ein
    simpler serieller Durchlauf (kein Thread-Overhead, stabile Reihenfolge).
    """
    workers = workers or LLM_CONCURRENCY
    if workers <= 1:
        for item in items:
            try:
                yield item, fn(item)
            except Exception as error:  # noqa: BLE001 — Fehler reicht der Aufrufer weiter
                yield item, error
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                yield item, future.result()
            except Exception as error:  # noqa: BLE001
                yield item, error


def _canonical_model_name(name: str) -> str:
    normalized = name.strip().lower()
    return normalized.removesuffix(":latest")


def ensure_available(models: Iterable[str]) -> None:
    """Ollama-Verbindung und lokale Modelle einmal vor einem Batch prüfen.

    Für die API-Backends nur ein Schlüssel-Check — kein Netzcall.
    """
    if BACKEND == "anthropic":
        return
    if BACKEND == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY ist nicht gesetzt. In .env eintragen (Repo-Wurzel) "
                "oder in der Shell exportieren."
            )
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


@lru_cache(maxsize=1)
def _openai_client():
    import openai

    # Das SDK macht bei 429/5xx exponentiellen Backoff und respektiert den
    # Retry-After-Header. Default sind 2 Versuche — zu wenig, wenn mehrere
    # Worker gleichzeitig das TPM-Limit der Org sättigen (typische Wartezeit
    # dann <1 s, aber in Serie). 8 Versuche fangen das ohne Handarbeit ab.
    return openai.OpenAI(max_retries=8)


def _json_object(text: str) -> str:
    """Das äußerste JSON-Objekt aus einer Modellantwort schneiden (Fences,
    Vor-/Nachtext tolerieren) — für den Ollama-Pfad ohne `format`-Grammar."""
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S)
    if fence:
        return fence.group(1)
    start = s.find("{")
    end = s.rfind("}")
    return s[start : end + 1] if start != -1 and end > start else s


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
            f"{system}\n\nAntworte ausschließlich mit einem JSON-Objekt nach diesem "
            f"Schema. Fülle jedes zutreffende Feld, auch verschachtelte Listen wie "
            f"`requirements` und `field_evidence`:\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )

        def _chat(use_format: bool) -> str:
            body = {
                "model": selected_model,
                "messages": [
                    {"role": "system", "content": grounded_system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "think": native_think,
                "options": {
                    "num_predict": max_tokens,
                    "num_ctx": OLLAMA_CONTEXT,
                    "temperature": temperature,
                    "seed": OLLAMA_SEED if seed is None else seed,
                },
            }
            if use_format:
                body["format"] = schema
            resp = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=600)
            resp.raise_for_status()
            return resp.json()["message"]["content"]

        # Erst ohne `format`-Grammar (die bei großem Schema optionale Arrays
        # verschluckt), dann mit Grammar als Sicherheitsnetz gegen Parse-Fehler.
        for use_format in (False, True):
            try:
                return output.model_validate_json(_json_object(_chat(use_format)))
            except (ValidationError, ValueError):
                if use_format:
                    raise
        raise RuntimeError("unerreichbar")

    if BACKEND == "openai":
        # chat.completions.parse baut aus dem Pydantic-Modell ein strict
        # JSON-Schema. reasoning_effort steuert die Denk-Tiefe (Extraktion: immer
        # low; Score: HEIMSPIEL_OPENAI_REASONING, default low). Reasoning-Tokens
        # zählen gegen max_completion_tokens, daher der Puffer.
        extra = {"service_tier": OPENAI_SERVICE_TIER} if OPENAI_SERVICE_TIER else {}
        completion = _openai_client().chat.completions.parse(
            model=model or EXTRACT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=output,
            max_completion_tokens=max_tokens + 8000,
            reasoning_effort=OPENAI_REASONING_EFFORT if think else "low",
            seed=OLLAMA_SEED if seed is None else seed,
            **extra,
        )
        message = completion.choices[0].message
        if message.refusal:
            raise ValueError(f"OpenAI hat die Anfrage abgelehnt: {message.refusal}")
        if message.parsed is None:
            raise ValueError(
                "OpenAI-Antwort ohne parsebares Objekt "
                f"(finish_reason={completion.choices[0].finish_reason})"
            )
        return message.parsed

    response = client().messages.parse(
        model=model or EXTRACT_MODEL,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_format=output,
    )
    return response.parsed_output
