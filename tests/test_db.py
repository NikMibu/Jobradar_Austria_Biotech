from heimspiel import db


def test_score_cache_key_includes_formula_and_model(conn):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME')")
    conn.execute(
        """INSERT INTO postings_raw(
               id, source, source_id, first_seen, last_seen, content_hash)
           VALUES (1, 'test', '1', '2026-01-01', '2026-01-01', 'hash')"""
    )
    conn.execute(
        """INSERT INTO postings(
               id, raw_id, extracted_json, schema_version, model, extracted_at)
           VALUES (1, 1, '{}', 1, 'extract-model', '2026-01-01')"""
    )
    base = (1, 7, 1, "{}", "2026-01-01")
    conn.execute(
        """INSERT INTO scores(
               posting_id, profile_version, hard_pass, hard_reasons,
               model, scored_at, score_version)
           VALUES (?, ?, ?, ?, 'model-a', ?, 1)""",
        base,
    )
    conn.execute(
        """INSERT INTO scores(
               posting_id, profile_version, hard_pass, hard_reasons,
               model, scored_at, score_version)
           VALUES (?, ?, ?, ?, 'model-a', ?, 2)""",
        base,
    )
    conn.execute(
        """INSERT INTO scores(
               posting_id, profile_version, hard_pass, hard_reasons,
               model, scored_at, score_version)
           VALUES (?, ?, ?, ?, 'model-b', ?, 2)""",
        base,
    )
    assert conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 3


def test_current_schema_has_versioned_score_primary_key(conn):
    primary_key = {
        row["name"]: row["pk"]
        for row in conn.execute("PRAGMA table_info(scores)")
        if row["pk"]
    }
    assert primary_key == {
        "posting_id": 1,
        "profile_version": 2,
        "score_version": 3,
        "model": 4,
    }
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_reset_keeps_geo_caches_and_drops_postings(tmp_path):
    path = tmp_path / "h.db"
    conn = db.connect(path)
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME')")
    conn.execute("INSERT INTO anchors (id, label, lat, lon) VALUES ('w', 'Wien', 48.2, 16.4)")
    conn.execute("INSERT INTO sites (id, company_id, label, lat, lon) VALUES (1, 1, 'Wien', 48.2, 16.4)")
    conn.execute("INSERT INTO travel_times (site_id, anchor_id, minutes, engine, computed_at) VALUES (1, 'w', 20, 't', 'x')")
    conn.execute(
        "INSERT INTO postings_raw (source, source_id, raw_title, content_hash, first_seen, last_seen) "
        "VALUES ('t', '1', 'Job', 'h', 'x', 'x')"
    )
    conn.commit()
    conn.close()
    backup = db.reset(path)
    assert backup.exists()
    fresh = db.connect(path)
    assert fresh.execute("SELECT COUNT(*) FROM postings_raw").fetchone()[0] == 0
    assert fresh.execute("SELECT minutes FROM travel_times").fetchone()[0] == 20
    assert fresh.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 1
    assert fresh.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
