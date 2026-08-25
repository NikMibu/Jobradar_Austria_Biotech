"""Backend-Dispatch (Ollama) und NaN-Härtung des JobSpy-Adapters."""

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
        calls["format"] = json["format"]
        return FakeResp()

    monkeypatch.setattr(llm, "BACKEND", "ollama")
    monkeypatch.setattr(llm.requests, "post", fake_post)
    result = llm.parse_structured("sys", "user", Tiny)
    assert result == Tiny(value=7)
    assert calls["url"].endswith("/api/chat")
    assert calls["format"]["properties"]["value"]["type"] == "integer"
