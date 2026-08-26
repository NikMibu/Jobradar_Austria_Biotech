from datetime import date, timedelta

from heimspiel.config import Anchor, Profile
from heimspiel.extract import Extraction, Requirement
from heimspiel.match import (
    HardNoHit,
    ScoreAssessment,
    SkillAssessment,
    compute_score,
    formal_status,
    hard_filter,
    practical_status,
)


def make_profile(**overrides) -> Profile:
    base = dict(
        profile_version=1,
        earliest_start="2026-10-01",
        education="msc_fh_bioinformatics",
        phd_wanted=False,
        role_families_allowed=["bioinformatics", "data_science"],
        seniority_allowed=["entry", "junior", "mid"],
        interests=["Massenspektrometrie"],
        skills={"programming": ["Python"]},
        hard_no=["Vertrieb"],
        anchors=[Anchor(id="wien", label="Wien Hbf", max_minutes=60)],
    )
    base.update(overrides)
    return Profile(**base, raw=dict(base))


def make_extraction(**overrides) -> Extraction:
    base = dict(
        title_norm="Bioinformatiker",
        role_family="bioinformatics",
        seniority="junior",
        education_min="msc",
        phd_required=False,
        german_required=False,
        location_text="Wien",
        workplace_mode="onsite",
        contract_type="permanent",
        summary_2_lines="Test.",
    )
    base.update(overrides)
    return Extraction(**base)


def test_pass_baseline():
    res = hard_filter(make_extraction(), make_profile(), travel_ok=True)
    assert res.passed and not res.reasons and not res.flags


def test_rule1_phd_required():
    res = hard_filter(make_extraction(phd_required=True), make_profile(), travel_ok=True)
    assert res.passed
    assert any("PhD" in r for r in res.flags)
    assert formal_status(make_extraction(phd_required=True), make_profile())[0] == "red"


def test_rule2_seniority_and_experience():
    assert hard_filter(make_extraction(seniority="senior"), make_profile(), True).passed
    assert hard_filter(make_extraction(years_experience_min=5), make_profile(), True).passed
    assert formal_status(make_extraction(seniority="senior"), make_profile())[0] == "red"
    assert formal_status(make_extraction(years_experience_min=5), make_profile())[0] == "red"
    assert hard_filter(make_extraction(years_experience_min=3), make_profile(), True).passed


def test_unstated_experience_is_not_a_formal_warning():
    assert formal_status(make_extraction(), make_profile()) == ("green", [])


def test_rule3_role_family():
    res = hard_filter(make_extraction(role_family="other"), make_profile(), travel_ok=True)
    assert not res.passed


def test_rule4_travel():
    # Kein Ausschluss mehr (Nutzer-Feedback) — nur Flag, sonst verschwinden
    # echte Österreich-Stellen außerhalb des Fahrzeit-Limits einfach aus dem Radar.
    outside = hard_filter(make_extraction(), make_profile(), travel_ok=False)
    assert outside.passed
    assert any("Fahrzeit-Limit" in f for f in outside.flags)
    unknown = hard_filter(make_extraction(), make_profile(), travel_ok=None)
    assert unknown.passed
    assert any("unbekannt" in f for f in unknown.flags)


def test_rule_foreign_location_flagged_not_dropped():
    # XING-Stadtsuche zieht auch deutsche/schweizer Städte mit (Hamburg, Zürich, ...)
    # — nicht ausschließen (kann trotzdem relevant sein), aber im Frontend filterbar machen.
    res = hard_filter(make_extraction(), make_profile(), travel_ok=None, in_austria=False)
    assert res.passed
    assert any("Österreich" in f for f in res.flags)


def test_rule_foreign_location_no_flag_if_fully_remote():
    # Bei workplace_mode=remote ist der Firmensitz irrelevant für die Kommute.
    res = hard_filter(
        make_extraction(workplace_mode="remote"), make_profile(), travel_ok=None, in_austria=False
    )
    assert res.passed
    assert not res.flags
    res2 = hard_filter(
        make_extraction(workplace_mode="remote"), make_profile(), travel_ok=False, in_austria=True
    )
    assert res2.passed
    assert not res2.flags


def test_rule5_short_contract_flagged_not_dropped():
    soon = (date.today() + timedelta(days=200)).isoformat()
    res = hard_filter(make_extraction(contract_end=soon), make_profile(), travel_ok=True)
    assert res.passed
    assert any("Befristung" in f for f in res.flags)


def test_deterministic_fachscore_uses_evidence_categories():
    ex = make_extraction(
        requirements=[
            Requirement(name="Python", importance="must", evidence="Python erforderlich"),
            Requirement(name="Nextflow", importance="nice", evidence="Nextflow von Vorteil"),
        ]
    )
    assessment = ScoreAssessment(
        skills=[
            SkillAssessment(requirement="Python", match="direct", profile_evidence="Python"),
            SkillAssessment(requirement="Nextflow", match="transferable", profile_evidence="Python"),
        ],
        domain_fit="strong",
        domain_evidence="Bioinformatik",
        interest_fit="strong",
        interest_evidence="Massenspektrometrie",
        angle="Ich verbinde Python mit Bioinformatik.",
    )
    score = compute_score(ex, make_profile(), assessment)
    assert score.breakdown == {
        "skills": 56,
        "must_skills": 50,
        "nice_skills": 6,
        "domain": 25,
        "interests": 15,
    }
    assert score.fit_score == 96
    assert score.confidence == 100


def test_missing_requirements_are_neutral_but_low_confidence():
    assessment = ScoreAssessment(angle="Test", domain_fit="unknown", interest_fit="unknown")
    score = compute_score(make_extraction(), make_profile(), assessment)
    assert score.fit_score == 49  # 25/50 + 5/10 + 12/25 + 7/15
    assert score.confidence == 0


def test_unsubstantiated_positive_fit_is_downgraded_to_unknown():
    ex = make_extraction(
        requirements=[Requirement(name="Python", importance="must", evidence="Python")]
    )
    assessment = ScoreAssessment(
        skills=[SkillAssessment(requirement="Python", match="direct", profile_evidence="Python")],
        domain_fit="strong",
        domain_evidence="erfundene Domäne",
        interest_fit="strong",
        interest_evidence="erfundenes Interesse",
        angle="Test",
    )
    score = compute_score(ex, make_profile(), assessment)
    assert score.fit_score == 74  # 50/50 + 5/10 + 12/25 + 7/15
    assert score.breakdown["domain"] == 12
    assert score.breakdown["interests"] == 7
    assert score.confidence == 80


def test_direct_match_requires_shared_skill_terms_in_profile_quote():
    ex = make_extraction(
        requirements=[
            Requirement(
                name="statistische Modellierung",
                importance="must",
                evidence="statistische Modellierung erforderlich",
            )
        ]
    )
    assessment = ScoreAssessment(
        skills=[
            SkillAssessment(
                requirement="statistische Modellierung",
                match="direct",
                profile_evidence="Computational Drug Discovery",
            )
        ],
        angle="Test",
    )
    profile = make_profile(
        skills={"domain": ["Computational Drug Discovery"]},
    )
    score = compute_score(ex, profile, assessment)
    assert score.breakdown["must_skills"] == 12  # direct wurde zu unknown gehärtet


def test_direct_match_accepts_short_and_symbolic_skill_names():
    for skill in ("R", "AI", "C++", "C#"):
        ex = make_extraction(
            requirements=[Requirement(name=skill, importance="must", evidence=skill)]
        )
        assessment = ScoreAssessment(
            skills=[SkillAssessment(requirement=skill, match="direct", profile_evidence=skill)],
            angle="Test",
        )
        profile = make_profile(skills={"programming": [skill]})
        assert compute_score(ex, profile, assessment).breakdown["must_skills"] == 50


def test_hard_no_requires_a_profile_rule_and_job_evidence():
    ex = make_extraction(requirements=[Requirement(name="Python", importance="must", evidence="Python")])
    invented = ScoreAssessment(
        angle="Test", hard_no_hits=[HardNoHit(rule="Python", evidence="Python")]
    )
    assert formal_status(ex, make_profile(), invented) == ("green", [])

    supported = ScoreAssessment(
        angle="Test", hard_no_hits=[HardNoHit(rule="Vertrieb", evidence="Python")]
    )
    status, reasons = formal_status(ex, make_profile(), supported)
    assert status == "red"
    assert reasons == ["Hard-no: Vertrieb"]


def test_practical_traffic_light_is_separate_from_fachscore():
    assert practical_status(make_extraction(), travel_ok=True, in_austria=True)[0] == "green"
    assert practical_status(make_extraction(), travel_ok=None, in_austria=True)[0] == "yellow"
    assert practical_status(make_extraction(), travel_ok=False, in_austria=True)[0] == "red"
