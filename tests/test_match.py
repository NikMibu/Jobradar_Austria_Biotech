from datetime import date, timedelta

from heimspiel.config import Anchor, Profile
from heimspiel.extract import Extraction
from heimspiel.match import hard_filter


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
    assert not res.passed
    assert any("PhD" in r for r in res.reasons)


def test_rule2_seniority_and_experience():
    assert not hard_filter(make_extraction(seniority="senior"), make_profile(), True).passed
    assert not hard_filter(
        make_extraction(years_experience_min=5), make_profile(), True
    ).passed
    assert hard_filter(make_extraction(years_experience_min=3), make_profile(), True).passed


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
