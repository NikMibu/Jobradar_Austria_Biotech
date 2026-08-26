"""Backend-Dispatch (Ollama) und NaN-Härtung des JobSpy-Adapters."""

import pytest
from pydantic import BaseModel

from heimspiel import llm
from heimspiel.normalize import content_hash, norm_text
from heimspiel.sources.jobspy_src import _rows_to_postings

NAN = float("nan")


def test_jobspy_rows_clean_nan():
    rows = [
        {"id": "j1", "site": "indeed", "title": "Bioinformatiker", "company": NAN,
         "location": NAN, "description": NAN, "job_url": "https://x/1"},
        {"id": NAN, "site": "indeed", "title": NAN},  # unbrauchbar → übersprungen
    ]
    postings = _rows_to_postings(rows)
    assert len(postings) == 1
    p = postings[0]
    assert p.company is None and p.location is None and p.text is None
    # der Crash-Pfad aus dem ersten daily-Lauf: content_hash mit NaN-Feldern
    assert content_hash(p.title, p.company, p.location)


def test_norm_text_survives_non_strings():
    assert norm_text(NAN) == ""  # type: ignore[arg-type]
    assert norm_text(42) == ""  # type: ignore[arg-type]


class Tiny(BaseModel):
    value: int


def test_parse_structured_ollama_dispatch(monkeypatch):
    calls = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": '{"value": 7}'}}

    def fake_post(url, json=None, timeout=None):
        calls["url"] = url
        calls["payload"] = json
        return FakeResp()

    monkeypatch.setattr(llm, "BACKEND", "ollama")
    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.parse_structured("sys", "user", Tiny)
    assert result == Tiny(value=7)
    assert calls["url"].endswith("/api/chat")
    payload = calls["payload"]
    assert payload["format"]["properties"]["value"]["type"] == "integer"
    assert '"properties"' in payload["messages"][0]["content"]
    assert payload["think"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["options"]["seed"] == llm.OLLAMA_SEED


def test_ollama_preflight_checks_connection_and_models(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "models": [
                    {"name": "ministral-3:14b"},
                    {"model": "hf.co/example/reasoning:Q4_K_M"},
                ]
            }

    monkeypatch.setattr(llm, "BACKEND", "ollama")
    monkeypatch.setattr(llm.requests, "get", lambda *args, **kwargs: FakeResp())
    llm.ensure_available(["ministral-3:14b", "hf.co/example/reasoning:q4_k_m"])

    with pytest.raises(RuntimeError, match="missing-model"):
        llm.ensure_available(["missing-model"])


def test_ollama_preflight_reports_stopped_server(monkeypatch):
    def fail(*args, **kwargs):
        raise llm.requests.ConnectionError("refused")

    monkeypatch.setattr(llm, "BACKEND", "ollama")
    monkeypatch.setattr(llm.requests, "get", fail)
    with pytest.raises(RuntimeError, match="ollama serve"):
        llm.ensure_available(["ministral-3:14b"])


def test_reasoning_name_does_not_force_unsupported_native_thinking(monkeypatch):
    calls = {}

    class FakeResp:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/show"):
            return FakeResp({"capabilities": ["completion"]})
        calls["payload"] = json
        return FakeResp({"message": {"content": '{"value": 8}'}})

    monkeypatch.setattr(llm, "BACKEND", "ollama")
    monkeypatch.setattr(llm.requests, "post", fake_post)
    model = "test/reasoning-without-native-thinking"
    llm._ollama_supports_thinking.cache_clear()
    result = llm.parse_structured("sys", "user", Tiny, model=model, think=True)
    assert result == Tiny(value=8)
    assert calls["payload"]["think"] is False


def test_native_thinking_is_enabled_only_when_model_reports_capability(monkeypatch):
    calls = {}

    class FakeResp:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def fake_post(url, json=None, timeout=None):
        if url.endswith("/api/show"):
            return FakeResp({"capabilities": ["completion", "thinking"]})
        calls["payload"] = json
        return FakeResp({"message": {"content": '{"value": 9}'}})

    monkeypatch.setattr(llm, "BACKEND", "ollama")
    monkeypatch.setattr(llm.requests, "post", fake_post)
    llm._ollama_supports_thinking.cache_clear()
    result = llm.parse_structured("sys", "user", Tiny, model="native-thinker", think=True)
    assert result == Tiny(value=9)
    assert calls["payload"]["think"] is True
