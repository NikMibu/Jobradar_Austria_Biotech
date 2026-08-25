"""Adapter-Tests mit gespeicherten Fixtures echter Seiten (SPEC §10)."""

import json

from heimspiel.sources import biotechjobs, erecruiter, euraxess, karriere_at, successfactors, vbc


def test_karriere_list_parsing(fixtures):
    data = json.loads((fixtures / "karriereat_list.json").read_text())
    jobs = karriere_at.parse_list(data)
    # Fixture-Suche hatte nur inaktive Treffer → aktive Liste darf leer sein,
    # aber die Struktur muss verstanden werden
    assert isinstance(jobs, list)
    inactive = data["data"]["jobsSearchList"]["inactiveItems"]["items"]
    assert len(inactive) == 15  # Strukturannahme der API abgesichert


def test_karriere_detail_parsing(fixtures):
    data = json.loads((fixtures / "karriereat_detail.json").read_text())
    text = karriere_at.parse_detail(data)
    assert text and len(text) > 200
    assert "<script" not in text and "<p>" not in text


def test_biotechjobs_search_parsing(fixtures):
    html = (fixtures / "biotechjobs_search.html").read_text(errors="replace")
    jobs = biotechjobs.parse_search(html)
    assert len(jobs) >= 20
    j = jobs[0]
    assert j["id"].isdigit()
    assert j["url"].startswith("https://www.biotechjobs.at/")
    assert j["title"]
    assert j["company"]


def test_biotechjobs_detail_parsing(fixtures):
    html = (fixtures / "biotechjobs_detail.html").read_text(errors="replace")
    d = biotechjobs.parse_detail(html)
    assert d.get("company") == "CBmed GmbH"
    assert d.get("location") == "Graz"


def test_vbc_parsing(fixtures):
    html = (fixtures / "vbc_positions.html").read_text(errors="replace")
    jobs = vbc.parse_positions(html)
    assert len(jobs) >= 15
    assert all(j["url"] and j["title"] for j in jobs)
    assert any(j["organisation"] for j in jobs)
    assert any(j["date"] for j in jobs)
    # relative PDF-Links müssen absolut werden (Frontend: new URL() im Drawer)
    assert all(j["url"].startswith("http") for j in jobs)


def test_erecruiter_parsing(fixtures):
    html = (fixtures / "erecruiter_ages.html").read_text(errors="replace")
    jobs = erecruiter.parse_jobs_json(html)
    assert len(jobs) >= 5
    assert all(isinstance(j["Id"], int) and j["Title"] for j in jobs)
    assert any(j.get("Location") for j in jobs)


def test_euraxess_parsing(fixtures):
    html = (fixtures / "euraxess_search.html").read_text(errors="replace")
    jobs = euraxess.parse_search(html)
    assert len(jobs) >= 5
    assert all(j["id"].isdigit() and j["title"] for j in jobs)
    assert all(j["url"].startswith("https://euraxess.ec.europa.eu/jobs/") for j in jobs)


def test_successfactors_parsing(fixtures):
    html = (fixtures / "successfactors_bi.html").read_text(errors="replace")
    jobs = successfactors.parse_list(html, "https://jobs.boehringer-ingelheim.com")
    assert len(jobs) >= 2
    assert all(j["id"].isdigit() and j["title"] and j["url"].startswith("https://") for j in jobs)
