from heimspiel.normalize import content_hash, dedup, match_company, norm_company, norm_text
from heimspiel.sources.base import RawPosting, store_postings


def test_norm_text_strips_gender_and_noise():
    assert norm_text("Bioinformatiker (m/w/d) – NGS!") == norm_text("Bioinformatiker NGS")
    assert norm_text(None) == ""


def test_norm_company_strips_legal_forms():
    assert norm_company("Takeda GmbH") == norm_company("TAKEDA")


def test_content_hash_stable_across_formatting():
    a = content_hash("Data Scientist (m/w/d)", "Firma GmbH", "Wien")
    b = content_hash("data scientist", "FIRMA", "wien")
    assert a == b
    assert a != content_hash("Data Engineer", "Firma GmbH", "Wien")


def _p(source, sid, title, company, text):
    return RawPosting(source, sid, f"https://x/{sid}", title, company, "Wien", text)


def test_store_postings_idempotent(conn):
    p = _p("indeed", "1", "Bioinformatiker", "ACME", "kurz")
    assert store_postings(conn, [p]) == 1
    assert store_postings(conn, [p]) == 0
    assert conn.execute("SELECT COUNT(*) FROM postings_raw").fetchone()[0] == 1


def test_store_postings_keeps_longer_text(conn):
    store_postings(conn, [_p("indeed", "1", "Bioinformatiker", "ACME", "kurz")])
    store_postings(conn, [_p("indeed", "1", "Bioinformatiker", "ACME", "viel längerer Text")])
    row = conn.execute("SELECT raw_text FROM postings_raw").fetchone()
    assert row["raw_text"] == "viel längerer Text"


def test_dedup_cross_source_fuzzy(conn):
    store_postings(
        conn,
        [
            _p("indeed", "a", "Bioinformatiker (m/w/d) NGS", "ACME GmbH", "x" * 100),
            _p("karriere_at", "b", "Bioinformatiker NGS (w/m/d)", "ACME", "x" * 500),
            _p("indeed", "c", "Vertriebsleiter", "ACME GmbH", "y"),
        ],
    )
    assert dedup(conn) == 1
    rows = conn.execute(
        "SELECT source_id, duplicate_of FROM postings_raw ORDER BY id"
    ).fetchall()
    by_sid = {r["source_id"]: r["duplicate_of"] for r in rows}
    # Gewinner = längster Text (karriere_at), indeed-a wird Duplikat
    assert by_sid["a"] is not None
    assert by_sid["b"] is None
    assert by_sid["c"] is None


def test_dedup_different_companies_untouched(conn):
    store_postings(
        conn,
        [
            _p("indeed", "a", "Data Scientist", "Alpha", "x"),
            _p("indeed", "b", "Data Scientist", "Beta", "x"),
        ],
    )
    assert dedup(conn) == 0


def test_match_company_fuzzy(conn):
    conn.execute("INSERT INTO companies (name) VALUES ('Boehringer Ingelheim RCV GmbH & Co KG')")
    conn.commit()
    cid = match_company(conn, "Boehringer Ingelheim RCV")
    assert cid is not None
    assert match_company(conn, "Novartis") is None
