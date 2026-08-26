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
