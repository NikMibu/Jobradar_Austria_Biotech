"""location_text → sites.site_id: Caching, Firmen-Site-Präferenz, generische Sites."""

import json
import sqlite3

import pytest
from test_extract import SAMPLE

from heimspiel import locations as loc
from heimspiel.locations import LocationResolution


def _seed_posting(conn, id_, company_id=None, location_text="Wien"):
    conn.execute(
        "INSERT INTO postings_raw (id, source, source_id, url, first_seen, last_seen, "
        "raw_title, raw_company, content_hash) "
        "VALUES (?, 'indeed', ?, 'https://x', datetime('now'), datetime('now'), 'T', 'F', ?)",
        (id_, str(id_), f"h{id_}"),
    )
    ex = {**SAMPLE, "location_text": location_text}
    conn.execute(
        "INSERT INTO postings (id, raw_id, company_id, extracted_json, schema_version, model, extracted_at) "
        "VALUES (?, ?, ?, ?, 1, 'test', datetime('now'))",
        (id_, id_, company_id, json.dumps(ex)),
    )
    conn.commit()


def test_resolve_city_caches_llm_call(conn, monkeypatch):
    calls = []

    def fake_parse_structured(system, user, output, max_tokens=2500):
        calls.append(user)
        return LocationResolution(city="Wien")

    monkeypatch.setattr(loc.llm, "parse_structured", fake_parse_structured)
    assert loc.resolve_city(conn, "Wien oder Homeoffice") == "Wien"
    assert loc.resolve_city(conn, "Wien oder Homeoffice") == "Wien"
    assert len(calls) == 1


def _no_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("LLM-Call, obwohl der Vorpass greifen sollte")

    monkeypatch.setattr(loc.llm, "parse_structured", boom)


def test_static_city_strips_country_and_state(conn, monkeypatch):
    # v1-Regression: qwen3:8b löste "Vienna, Austria" u. Ä. auf null auf
    _no_llm(monkeypatch)
    assert loc.resolve_city(conn, "Vienna, Austria") == "Wien"
    assert loc.resolve_city(conn, "Graz, Styria, Austria") == "Graz"
    assert loc.resolve_city(conn, "GRAZ, Austria") == "Graz"
    assert loc.resolve_city(conn, "Salzburg, Salzburg, Austria") == "Salzburg"
    assert loc.resolve_city(conn, "Klosterneuburg, N, AT") == "Klosterneuburg"


def test_static_city_leaves_ambiguous_cases_to_llm():
    # Nacktes Einzelwort: kein entfernter Teil belegt Österreich-Kontext → LLM
    assert loc._static_city("Wien") is None
    assert loc._static_city("Homeoffice") is None
    # Remote-Wörter und Mehrfachorte entscheidet das LLM
    assert loc._static_city("Homeoffice, Österreich") is None
    assert loc._static_city("Linz, Wels, Salzburg") is None
    assert loc._static_city("Wien (Vienna), Austria") is None


def test_is_in_austria_false_recovers_city_via_fallback(conn, monkeypatch):
    # XING-Stadtsuche zieht auch DACH-Nachbarländer mit rein (Hamburg, Zürich, ...).
    # Das LLM meldet in_austria=false zuverlässig, vergisst bei knappem Text aber
    # manchmal den Stadtnamen — _best_effort_foreign_city holt ihn zurück, sonst
    # kein Karten-Pin trotz bekanntem Standort.
    monkeypatch.setattr(
        loc.llm, "parse_structured", lambda *a, **k: LocationResolution(city=None, in_austria=False)
    )
    assert loc.is_in_austria(conn, "Hamburg") is False
    assert loc.resolve_city(conn, "Hamburg") == "Hamburg"


def test_best_effort_foreign_city_handles_parens_and_noise():
    assert loc._best_effort_foreign_city("Hamburg (Hybrid)") == "Hamburg"
    assert loc._best_effort_foreign_city("Bensheim, Deutschland (D)") == "Bensheim"
    assert loc._best_effort_foreign_city("Bern (CH)") == "Bern"
    # Mehrdeutig (zwei echte Ortsteile übrig) -> lieber nichts als geraten
    assert loc._best_effort_foreign_city("Karlsruhe, Heidelberg, Mannheim") is None
    assert loc._best_effort_foreign_city("Deutschland (Helmholtz-Assoziation)") is None
    # Land/Kontinent ohne Stadt -> kein Fake-Pin auf Kontinent-Zentroid
    assert loc._best_effort_foreign_city("Europa (Baustelle Ausland)") is None
    assert loc._best_effort_foreign_city("Australia") is None


def test_is_in_austria_true_for_ambiguous_case(conn, monkeypatch):
    monkeypatch.setattr(
        loc.llm, "parse_structured", lambda *a, **k: LocationResolution(city=None, in_austria=True)
    )
    assert loc.is_in_austria(conn, "Homeoffice") is True


def test_resolve_city_treats_literal_null_string_as_none(conn, monkeypatch):
    # Lokale Modelle geben bei Optional-Feldern manchmal den String "null" statt
    # JSON null zurück (beobachtet mit qwen2.5:7b bei mehrdeutigem Freitext).
    monkeypatch.setattr(
        loc.llm, "parse_structured", lambda *a, **k: LocationResolution(city="null")
    )
    assert loc.resolve_city(conn, "Österreich, Schweiz, Slowenien") is None


def test_resolve_locations_leaves_unresolvable_without_site(conn, monkeypatch):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME GmbH')")
    _seed_posting(conn, 1, location_text="Homeoffice")
    monkeypatch.setattr(loc, "_resolve", lambda conn, text: (None, True))

    assert loc.resolve_locations(conn) == 0
    row = conn.execute("SELECT site_id FROM postings WHERE id=1").fetchone()
    assert row["site_id"] is None
    assert conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"] == 0


def test_resolve_locations_creates_generic_site_without_company(conn, monkeypatch):
    _seed_posting(conn, 1, company_id=None, location_text="Wien")
    monkeypatch.setattr(loc, "_resolve", lambda conn, text: ("Wien", True))

    assert loc.resolve_locations(conn) == 1
    site = conn.execute("SELECT * FROM sites").fetchone()
    assert site["company_id"] is None
    assert site["label"] == "Wien"
    assert site["lat"] is None  # geocode_missing() füllt das später
    row = conn.execute("SELECT site_id FROM postings WHERE id=1").fetchone()
    assert row["site_id"] == site["id"]


def test_resolve_locations_creates_foreign_site_without_at_suffix(conn, monkeypatch):
    # Auslands-Städte sollen auf der Karte erscheinen, aber ohne ", Österreich"-Suffix
    # (sonst geokodiert Nominatim ins Leere) und ohne spätere Transitous-Fahrzeiten.
    _seed_posting(conn, 1, company_id=None, location_text="Hamburg")
    monkeypatch.setattr(loc, "_resolve", lambda conn, text: ("Hamburg", False))

    assert loc.resolve_locations(conn) == 1
    site = conn.execute("SELECT * FROM sites").fetchone()
    assert site["label"] == "Hamburg"
    assert site["address_text"] == "Hamburg"
    assert site["in_austria"] == 0


def test_resolve_locations_prefers_company_site_over_generic(conn, monkeypatch):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME GmbH')")
    conn.execute(
        "INSERT INTO sites (company_id, label, lat, lon, is_hq) VALUES (1, 'Wien Zentrale', 48.2, 16.3, 1)"
    )
    conn.commit()
    _seed_posting(conn, 1, company_id=1, location_text="Wien")
    monkeypatch.setattr(loc, "_resolve", lambda conn, text: ("Wien", True))

    assert loc.resolve_locations(conn) == 1
    row = conn.execute("SELECT site_id FROM postings WHERE id=1").fetchone()
    site = conn.execute("SELECT * FROM sites WHERE id=?", (row["site_id"],)).fetchone()
    assert site["label"] == "Wien Zentrale"
    assert conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"] == 1  # keine neue generische Site


def test_resolve_locations_dedupes_generic_site_across_postings(conn, monkeypatch):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME GmbH'), (2, 'Beta AG')")
    _seed_posting(conn, 1, company_id=1, location_text="Linz")
    _seed_posting(conn, 2, company_id=2, location_text="Linz")
    monkeypatch.setattr(loc, "_resolve", lambda conn, text: ("Linz", True))

    assert loc.resolve_locations(conn) == 2
    assert conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"] == 1
    site_ids = {
        r["site_id"] for r in conn.execute("SELECT site_id FROM postings").fetchall()
    }
    assert len(site_ids) == 1


def test_generic_label_unique_index_rejects_duplicate(conn):
    conn.execute("INSERT INTO sites (company_id, label) VALUES (NULL, 'Graz')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO sites (company_id, label) VALUES (NULL, 'Graz')")


def test_company_site_same_label_as_generic_is_allowed(conn):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME GmbH')")
    conn.execute("INSERT INTO sites (company_id, label) VALUES (NULL, 'Graz')")
    conn.execute("INSERT INTO sites (company_id, label) VALUES (1, 'Graz')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"] == 2
