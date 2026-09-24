"""Jev-Scoring: Formel, Empfehlungs-Overrides, Stub-Deckel, Antwort-Parsing."""

from types import SimpleNamespace

from test_match import make_extraction, make_profile

from heimspiel import jev
from heimspiel.extract import Requirement
from heimspiel.jev import JevAssessment
from heimspiel.match import compute_score, decide_recommendation, formal_status, text_quality


def req(level, importance="must", name="Python", p_missing=0.0):
    return {
        "requirement": name,
        "importance": importance,
        "job_evidence": name,
        "level": level,
        "p_missing": p_missing,
        "p_transferable": 0.0,
        "p_direct": 1.0 - p_missing,
    }


def assessment(**overrides) -> JevAssessment:
    base = dict(
        requirements=[req(1.0), req(0.5, importance="nice", name="Nextflow")],
        domain=1.0,
        interest=1.0,
        recommendation_probs={"bewerben": 0.7, "stretch": 0.2, "nicht_bewerben": 0.1},
        confidence=0.9,
    )
    base.update(overrides)
    return JevAssessment(**base)


def test_formula_60_25_15_from_expected_levels():
    score = compute_score(make_extraction(), assessment())
    assert score.breakdown == {
        "skills": 55, "must_skills": 50, "nice_skills": 5, "domain": 25, "interests": 15,
    }
    assert score.fit_score == 95
    assert score.confidence == 90


def test_no_requirements_is_neutral_and_less_confident():
    score = compute_score(make_extraction(), assessment(requirements=[], domain=0.0, interest=0.0))
    assert score.fit_score == 30  # 25/50 + 5/10
    assert score.confidence == 60


def test_phd_position_uses_phd_topic_fit_instead_of_interest():
    ex = make_extraction(position_type="phd")
    score = compute_score(ex, assessment(interest=0.0, phd_topic=1.0))
    assert score.breakdown["interests"] == 15
    assert "PhD-Themenfit" in score.reasons[2]
    job = compute_score(make_extraction(), assessment(interest=0.0, phd_topic=1.0))
    assert job.breakdown["interests"] == 0


def test_stub_caps_confidence_and_recommendation():
    assert text_quality("kurz") == "stub"
    assert text_quality("x" * 2000) == "full"
    score = compute_score(make_extraction(), assessment(), quality="stub")
    assert score.confidence == 30
    rec, notes = decide_recommendation(assessment(), "green", "stub")
    assert rec == "stretch"
    assert notes


def test_gaps_are_likely_missing_requirements_must_first():
    reqs = [
        req(0.0, importance="nice", name="Docker", p_missing=0.9),
        req(0.0, name="GMP", p_missing=0.6),
        req(1.0, name="Python"),
    ]
    assert compute_score(make_extraction(), assessment(requirements=reqs)).gaps == ["GMP", "Docker"]


def test_recommendation_overrides():
    assert decide_recommendation(assessment(), "green", "full") == ("bewerben", [])
    assert decide_recommendation(assessment(), "red", "full")[0] == "nicht_bewerben"
    # Nicht-Claim: Einstiegsstelle höchstens stretch, erfahrene Stelle nicht_bewerben
    assert decide_recommendation(assessment(not_claim=0.9), "green", "full", "junior")[0] == "stretch"
    assert decide_recommendation(assessment(not_claim=0.9), "green", "full", "mid")[0] == "nicht_bewerben"
    stretch = assessment(recommendation_probs={"bewerben": 0.2, "stretch": 0.5, "nicht_bewerben": 0.3})
    assert decide_recommendation(stretch, "yellow", "full") == ("stretch", [])
    assert decide_recommendation(None, "green", "full")[0] == "nicht_bewerben"
    assert decide_recommendation(assessment(), "green", "full", abroad=True)[0] == "nicht_bewerben"
    assert decide_recommendation(assessment(domain=0.2), "green", "full")[0] == "nicht_bewerben"
    assert decide_recommendation(assessment(), "green", "full", fit_score=44)[0] == "stretch"
    assert decide_recommendation(assessment(), "green", "full", initiative=True)[0] == "stretch"
    # IT-/KI-Stellen ohne Life-Science-Bezug dürfen "bewerben" bleiben
    assert decide_recommendation(assessment(life_science=0.1), "green", "full")[0] == "bewerben"


def test_hard_no_probability_turns_formal_red():
    assert formal_status(make_extraction(), make_profile(), assessment(hard_no=0.95))[0] == "red"
    assert formal_status(make_extraction(), make_profile(), assessment(hard_no=0.3))[0] == "green"


def test_phd_position_asking_for_phd_is_not_formally_red():
    ex = make_extraction(position_type="phd", education_min="phd")
    assert formal_status(ex, make_profile(phd_wanted=True))[0] == "green"
    assert formal_status(make_extraction(education_min="phd"), make_profile())[0] == "red"


def test_questions_cover_requirements_and_phd_only_for_phd():
    ex = make_extraction(
        requirements=[
            Requirement(name="Python", importance="nice", evidence="Python"),
            Requirement(name="RNA-Seq", importance="must", evidence="RNA-Seq"),
        ]
    )
    q = jev.build_questions(ex, make_profile(hard_no=["Vertrieb", "Umzug ins Ausland"]))
    assert {"req_0", "req_1", "domain", "interest", "not_claim", "hard_no_0", "recommendation"} <= set(q)
    assert "hard_no_1" not in q  # Standortregel entscheidet in_austria, nicht Jev
    assert "phd_topic" not in q
    assert "RNA-Seq" in q["req_0"].instructions  # must zuerst
    assert "Ausland" not in q["hard_no_0"].instructions
    assert "phd_topic" in jev.build_questions(make_extraction(position_type="predoc"), make_profile())


def test_initiative_titles_are_marked_deterministically():
    from heimspiel.extract import mark_initiative

    ex = make_extraction()
    assert mark_initiative("Unsolicited Application – Talent Pool", ex).position_type == "initiative"
    assert mark_initiative("Initiativbewerbung", ex).position_type == "initiative"
    assert mark_initiative("Bioinformatiker NGS", ex).position_type == "job"


def test_soft_skills_are_not_jev_questions():
    ex = make_extraction(
        requirements=[
            Requirement(name="Teamfähigkeit", importance="must", evidence="Team", kind="soft"),
            Requirement(name="HPLC", importance="must", evidence="HPLC"),
        ]
    )
    assert [r.name for r in jev.requirements_for(ex)] == ["HPLC"]


def test_parse_response_normalizes_levels():
    ex = make_extraction(requirements=[Requirement(name="Python", importance="must", evidence="Python")])

    def score(value, probs):
        return SimpleNamespace(score=value, confidence=0.8, probabilities=probs)

    answers = {
        "req_0": score(2.0, {0: 0.0, 1: 0.0, 2: 1.0}),
        "domain": score(1.5, {}),
        "interest": score(3.0, {}),
        "not_claim": SimpleNamespace(noul=0.1),
        "recommendation": SimpleNamespace(
            confidence=0.8, probabilities={"bewerben": 0.6, "stretch": 0.3, "nicht_bewerben": 0.1}
        ),
    }
    a = jev.parse_response(ex, answers, 1234)
    assert a.requirements[0]["level"] == 1.0
    assert a.requirements[0]["p_direct"] == 1.0
    assert a.domain == 0.5
    assert a.interest == 1.0
    assert a.phd_topic is None
    assert a.confidence == 0.8
    assert JevAssessment.from_json(a.to_json()) == a


def test_score_pending_uses_jev_boundary(conn, monkeypatch):
    from heimspiel import match
    from heimspiel.extract import SCHEMA_VERSION

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(match.jev, "assess", lambda *a, **k: assessment())
    conn.execute(
        "INSERT INTO postings_raw (source, source_id, url, raw_title, raw_text, content_hash, "
        "first_seen, last_seen) VALUES ('t','1','u','Bioinformatiker',?, 'h','2026-09-24','2026-09-24')",
        ("Volltext " * 200,),
    )
    conn.execute(
        "INSERT INTO postings (raw_id, extracted_json, schema_version, model, extracted_at) "
        "VALUES (1, ?, ?, ?, '2026-09-24')",
        (make_extraction().model_dump_json(), SCHEMA_VERSION, match.llm.EXTRACT_MODEL),
    )
    profile = make_profile()
    assert match.score_pending(conn, profile) == 1
    row = conn.execute("SELECT fit_score, recommendation, text_quality FROM scores").fetchone()
    assert tuple(row) == (95, "bewerben", "full")
    assert match.recompute_statuses(conn, profile) == 1
