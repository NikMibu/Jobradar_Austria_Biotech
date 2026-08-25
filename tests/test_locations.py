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
    monkeypatch.setattr(loc, "resolve_city", lambda conn, text: None)

    assert loc.resolve_locations(conn) == 0
    row = conn.execute("SELECT site_id FROM postings WHERE id=1").fetchone()
    assert row["site_id"] is None
    assert conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"] == 0


def test_resolve_locations_creates_generic_site_without_company(conn, monkeypatch):
    _seed_posting(conn, 1, company_id=None, location_text="Wien")
    monkeypatch.setattr(loc, "resolve_city", lambda conn, text: "Wien")

    assert loc.resolve_locations(conn) == 1
    site = conn.execute("SELECT * FROM sites").fetchone()
    assert site["company_id"] is None
    assert site["label"] == "Wien"
    assert site["lat"] is None  # geocode_missing() füllt das später
    row = conn.execute("SELECT site_id FROM postings WHERE id=1").fetchone()
    assert row["site_id"] == site["id"]


def test_resolve_locations_prefers_company_site_over_generic(conn, monkeypatch):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME GmbH')")
    conn.execute(
        "INSERT INTO sites (company_id, label, lat, lon, is_hq) VALUES (1, 'Wien Zentrale', 48.2, 16.3, 1)"
    )
    conn.commit()
    _seed_posting(conn, 1, company_id=1, location_text="Wien")
    monkeypatch.setattr(loc, "resolve_city", lambda conn, text: "Wien")

    assert loc.resolve_locations(conn) == 1
    row = conn.execute("SELECT site_id FROM postings WHERE id=1").fetchone()
    site = conn.execute("SELECT * FROM sites WHERE id=?", (row["site_id"],)).fetchone()
    assert site["label"] == "Wien Zentrale"
    assert conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"] == 1  # keine neue generische Site


def test_resolve_locations_dedupes_generic_site_across_postings(conn, monkeypatch):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME GmbH'), (2, 'Beta AG')")
    _seed_posting(conn, 1, company_id=1, location_text="Linz")
    _seed_posting(conn, 2, company_id=2, location_text="Linz")
    monkeypatch.setattr(loc, "resolve_city", lambda conn, text: "Linz")

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
