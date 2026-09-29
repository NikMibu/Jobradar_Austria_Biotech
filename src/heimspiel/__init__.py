"""Heimspiel — persönlicher Jobradar für Life-Science-Stellen in Österreich."""

from pathlib import Path

from dotenv import load_dotenv

# .env aus dem Repo-Wurzelverzeichnis laden, bevor Submodule (llm.py) ihre
# Umgebungsvariablen beim Import auslesen. override=False: schon gesetzte Vars
# (z. B. OPENAI_API_KEY aus der Shell) behalten Vorrang.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
load_dotenv(override=False)  # Fallback: von cwd aufwärts suchen

__version__ = "0.1.0"
