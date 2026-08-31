"""Regression: `daily` ruft die Stages als reine Funktionen auf, nicht über Typer.

Ausgelassene bool-Flags sind dann das `typer.Option(...)`-Sentinel (truthy) — so
übersprang `score(recompute=…)` einmal lautlos das komplette LLM-Scoring.
"""

from heimspiel import cli


def test_daily_runs_llm_scoring_not_only_status_recompute(monkeypatch):
    calls: list[str] = []

    # Alle Stages bis auf score neutralisieren.
    for name in ("fetch", "extract", "locations", "companies", "travel", "export", "report"):
        monkeypatch.setattr(cli, name, lambda *a, **k: None)

    import heimspiel.match as match

    monkeypatch.setattr(cli.db, "connect", lambda *a, **k: object())
    monkeypatch.setattr(cli.cfg, "load_profile", lambda: type("P", (), {"profile_version": 1})())
    monkeypatch.setattr(match, "score_pending", lambda *a, **k: calls.append("score_pending") or 0)
    monkeypatch.setattr(match, "recompute_statuses", lambda *a, **k: calls.append("recompute") or 0)

    cli.daily(career_pages=False)

    assert calls == ["score_pending"], f"daily muss LLM-Scoring auslösen, nicht {calls}"
