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
    # Erstversuch geht ohne `format`-Grammar; das Schema trägt der Prompt.
    assert "format" not in payload
    assert '"properties"' in payload["messages"][0]["content"]
    assert payload["think"] is False
    assert payload["options"]["temperature"] == 0
    assert payload["options"]["seed"] == llm.OLLAMA_SEED


def test_parse_structured_openai_dispatch(monkeypatch):
    calls = {}

    class FakeParsed:
        def __init__(self):
            self.message = type("M", (), {"parsed": Tiny(value=7), "refusal": None})()
            self.finish_reason = "stop"

    class FakeCompletion:
        choices = [FakeParsed()]

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def parse(**kwargs):
                    calls.update(kwargs)
                    return FakeCompletion()

    monkeypatch.setattr(llm, "BACKEND", "openai")
    monkeypatch.setattr(llm, "_openai_client", lambda: FakeClient())
    result = llm.parse_structured("sys", "user", Tiny, model="gpt-x", think=False)
    assert result == Tiny(value=7)
    assert calls["model"] == "gpt-x"
    assert calls["response_format"] is Tiny
    assert calls["reasoning_effort"] == "low"
    assert calls["seed"] == llm.OLLAMA_SEED
    assert "max_tokens" not in calls  # Reasoning-Modelle: nur max_completion_tokens


def test_openai_score_path_uses_configured_reasoning_effort(monkeypatch):
    calls = {}

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def parse(**kwargs):
                    calls.update(kwargs)
                    msg = type("M", (), {"parsed": Tiny(value=1), "refusal": None})()
                    return type("C", (), {"choices": [type("Ch", (), {"message": msg, "finish_reason": "stop"})()]})()

    monkeypatch.setattr(llm, "BACKEND", "openai")
    monkeypatch.setattr(llm, "OPENAI_REASONING_EFFORT", "medium")
    monkeypatch.setattr(llm, "_openai_client", lambda: FakeClient())
    llm.parse_structured("sys", "user", Tiny, think=True)
    assert calls["reasoning_effort"] == "medium"


def test_openai_service_tier_passed_only_when_set(monkeypatch):
    calls = {}

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def parse(**kwargs):
                    calls.clear()
                    calls.update(kwargs)
                    msg = type("M", (), {"parsed": Tiny(value=1), "refusal": None})()
                    return type("C", (), {"choices": [type("Ch", (), {"message": msg, "finish_reason": "stop"})()]})()

    monkeypatch.setattr(llm, "BACKEND", "openai")
    monkeypatch.setattr(llm, "_openai_client", lambda: FakeClient())

    monkeypatch.setattr(llm, "OPENAI_SERVICE_TIER", None)
    llm.parse_structured("s", "u", Tiny)
    assert "service_tier" not in calls

    monkeypatch.setattr(llm, "OPENAI_SERVICE_TIER", "fast")
    llm.parse_structured("s", "u", Tiny)
    assert calls["service_tier"] == "fast"


def test_openai_refusal_raises(monkeypatch):
    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def parse(**kwargs):
                    msg = type("M", (), {"parsed": None, "refusal": "nein"})()
                    return type("C", (), {"choices": [type("Ch", (), {"message": msg, "finish_reason": "stop"})()]})()

    monkeypatch.setattr(llm, "BACKEND", "openai")
    monkeypatch.setattr(llm, "_openai_client", lambda: FakeClient())
    with pytest.raises(ValueError, match="abgelehnt"):
        llm.parse_structured("sys", "user", Tiny)


@pytest.mark.parametrize("concurrency", [1, 4])
def test_parallel_map_returns_all_items_and_isolates_errors(monkeypatch, concurrency):
    monkeypatch.setattr(llm, "LLM_CONCURRENCY", concurrency)

    def fn(n):
        if n == 3:
            raise ValueError("boom")
        return n * 10

    out = dict(llm.parallel_map(fn, [1, 2, 3, 4]))
    assert out[1] == 10 and out[2] == 20 and out[4] == 40
    assert isinstance(out[3], ValueError)


def test_openai_preflight_requires_key(monkeypatch):
    monkeypatch.setattr(llm, "BACKEND", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.ensure_available(["gpt-5.6-luna"])


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
