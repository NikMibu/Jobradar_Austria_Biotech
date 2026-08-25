import json

from test_extract import SAMPLE
from test_match import make_profile

from heimspiel import export as exp
from heimspiel import report
from heimspiel.match import initiative_scores


def _seed(conn, fit_score=80):
    conn.execute("INSERT INTO companies (id, name) VALUES (1, 'ACME GmbH')")
    conn.execute(
        "INSERT INTO postings_raw (id, source, source_id, url, first_seen, last_seen, raw_title, raw_company, content_hash) "
        "VALUES (1, 'indeed', 'x', 'https://x', datetime('now'), datetime('now'), 'Bioinformatiker', 'ACME GmbH', 'h')"
    )
    conn.execute(
        "INSERT INTO postings (id, raw_id, company_id, extracted_json, schema_version, model, extracted_at) "
        "VALUES (1, 1, 1, ?, 1, 'test', datetime('now'))",
        (json.dumps(SAMPLE),),
    )
    conn.execute(
        "INSERT INTO scores (posting_id, profile_version, hard_pass, hard_reasons, fit_score, fit_reasons, gaps, angle, model, scored_at) "
        "VALUES (1, 1, 1, '{}', ?, '[\"passt\"]', '[]', 'mein Angle', 'test', datetime('now'))",
        (fit_score,),
    )
    conn.commit()


def test_export_writes_summary_and_lazy_details(conn, tmp_path):
    _seed(conn)
    meta = exp.export_all(conn, make_profile(), out_dir=tmp_path)
    assert meta["counts"]["jobs"] == 1
    jobs = json.loads((tmp_path / "jobs.json").read_text())
    assert jobs[0]["fit_score"] == 80
    assert jobs[0]["company"] == "ACME GmbH"
    assert jobs[0]["role_family"] == SAMPLE["role_family"]
    assert "extraction" not in jobs[0]
    details = json.loads((tmp_path / "job-details.json").read_text())
    assert details["1"]["extraction"] == SAMPLE
    assert (tmp_path / "companies.json").exists()
    assert (tmp_path / "meta.json").exists()
    assert meta["data_schema_version"] == 2


def test_report_contains_top_and_angle(conn):
    _seed(conn)
    md = report.daily_report(conn, make_profile())
    assert "Bioinformatiker" in md
    assert "mein Angle" in md
    assert "80/100" in md


def test_initiative_score_counts_history_minus_open(conn):
    _seed(conn)
    # frisch gesehenes Inserat zählt als offen → Score 1*1.0 - 1 = 0 → nicht gelistet
    assert initiative_scores(conn, make_profile()) == []
    # Historie: last_seen alt → nichts offen → Score 1.0
    conn.execute("UPDATE postings_raw SET last_seen = datetime('now', '-90 days')")
    conn.commit()
    res = initiative_scores(conn, make_profile())
    assert len(res) == 1
    assert res[0]["initiative_score"] == 1.0
    assert "Initiativbewerbung" in res[0]["summary"]
