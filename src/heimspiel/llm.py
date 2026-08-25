"""Gemeinsamer Anthropic-Client und Modellwahl.

SPEC §5: Haiku 4.5 für Extraktion und Score. HEIMSPIEL_MODEL überschreibt
(z. B. für den Ollama/LiteLLM-Switch der Null-Kosten-Variante via ANTHROPIC_BASE_URL).
"""

import os
from functools import lru_cache

import anthropic

EXTRACT_MODEL = os.environ.get("HEIMSPIEL_MODEL", "claude-haiku-4-5")


@lru_cache(maxsize=1)
def client() -> anthropic.Anthropic:
    return anthropic.Anthropic()
