"""Typer-CLI: fetch | extract | locations | score | travel | export | daily | report (SPEC §10)."""

import os
from datetime import datetime
from pathlib import Path

import typer

from . import config as cfg
from . import db, normalize, paths

app = typer.Typer(help="Heimspiel — persönlicher Jobradar", no_args_is_help=True)


@app.command()
def fetch(
    jobspy: bool = typer.Option(True, help="Indeed/LinkedIn via JobSpy"),
    karriere: bool = typer.Option(True, help="karriere.at"),
    biotech: bool = typer.Option(True, help="biotechjobs.at"),
    vbc: bool = typer.Option(True, help="Vienna BioCenter"),
    ats: bool = typer.Option(True, help="eRecruiter/SuccessFactors/EURAXESS (config/ats.yaml)"),
    xing: bool = typer.Option(True, help="XING (Stadt-Suchen aus search.yaml)"),
    career_pages: bool = typer.Option(False, "--career-pages", help="Firmen-Karriereseiten (wöchentlich)"),
) -> None:
    """Alle aktivierten Quellen abrufen und in postings_raw schreiben."""
    conn = db.connect()
    search = cfg.load_search()
    from .sources.base import store_postings

    def run_source(label: str, fn) -> int:
        # Eine abstürzende Quelle darf die anderen nicht mitreißen (SPEC §13)
        typer.echo(f"{label} …")
        try:
            n = store_postings(conn, fn())
            typer.echo(f"  → {n} neu")
            return n
        except Exception as e:  # noqa: BLE001
            typer.secho(f"  → {label} fehlgeschlagen: {e}", fg=typer.colors.RED)
            return 0

    total = 0
    if biotech:
        from .sources import biotechjobs

        total += run_source("biotechjobs.at", biotechjobs.fetch)
    if vbc:
        from .sources import vbc as vbc_src

        total += run_source("Vienna BioCenter", vbc_src.fetch)
    if karriere:
        from .sources import karriere_at

        total += run_source(
            "karriere.at", lambda: karriere_at.fetch(search.terms, search.locations)
        )
    if ats:
        ats_cfg = cfg.load_ats()
        if ats_cfg.erecruiter_hosts:
            from .sources import erecruiter

            total += run_source(
                "eRecruiter", lambda: erecruiter.fetch(ats_cfg.erecruiter_hosts)
            )
        if ats_cfg.successfactors_tenants:
            from .sources import successfactors

            total += run_source(
                "SuccessFactors",
                lambda: successfactors.fetch(ats_cfg.successfactors_tenants),
            )
        from .sources import euraxess

        total += run_source(
            "EURAXESS", lambda: euraxess.fetch(max_pages=ats_cfg.euraxess_max_pages)
        )
    if xing:
        from .sources import xing as xing_src

        known = {
            r["source_id"]
            for r in conn.execute("SELECT source_id FROM postings_raw WHERE source='xing'")
        }
        total += run_source(
            "XING", lambda: xing_src.fetch(search.terms, search.locations, known_ids=known)
        )
    if jobspy:
        from .sources import jobspy_src

        total += run_source(
            "JobSpy (Indeed/LinkedIn)", lambda: jobspy_src.fetch(search.terms)
        )
    if career_pages:
        from .sources import career_pages as cp

        typer.echo("Firmen-Karriereseiten …")
        try:
            total += cp.watch_all(conn)
        except Exception as e:  # noqa: BLE001
            typer.secho(f"  → Karriereseiten fehlgeschlagen: {e}", fg=typer.colors.RED)

    marked = normalize.dedup(conn)
    typer.echo(f"{total} neue Inserate, {marked} Duplikate markiert.")


@app.command()
def extract(limit: int | None = typer.Option(None, help="max. Anzahl")) -> None:
    """LLM-Extraktion aller noch nicht extrahierten Postings (gecacht)."""
    from . import extract as ex

    conn = db.connect()
    pending = len(ex.pending_raws(conn))
    typer.echo(f"{pending} Postings zu extrahieren …")
    if pending:
        ex.llm.ensure_available([ex.llm.EXTRACT_MODEL])
    done = ex.extract_pending(conn, limit=limit)
    typer.echo(f"{done} extrahiert.")


@app.command()
def locations(limit: int | None = typer.Option(None, help="max. Anzahl")) -> None:
    """LLM-Normalisierung von location_text → sites.site_id (gecacht)."""
    from . import locations as loc

    conn = db.connect()
    done = loc.resolve_locations(conn, limit=limit)
    typer.echo(f"{done} Postings einem Standort zugeordnet.")


@app.command("eval-roles")
def eval_roles(
    models: str = typer.Option(
        "qwen3.5:9b,ministral-3:14b", help="Kommagetrennte Modellliste"
    ),
    n_random: int = typer.Option(20, help="Zufalls-Postings zusätzlich zu den Verdachtsfällen"),
) -> None:
    """role_family-Klassifikation mehrerer Modelle auf ~30 DB-Postings vergleichen."""
    from . import eval_roles as er
    from . import llm

    conn = db.connect()
    selected = [m.strip() for m in models.split(",") if m.strip()]
    llm.ensure_available(selected)
    er.run(conn, selected, n_random=n_random)


@app.command("eval-ranking")
def eval_ranking(
    labels: Path = typer.Option(..., exists=True, readable=True, help="JSONL aus dem UI"),
    models: str | None = typer.Option(None, help="Kommagetrennte Scoringmodelle"),
) -> None:
    """Scoringmodelle read-only gegen persönliche Passt/Vielleicht/Nein-Labels vergleichen."""
    from . import eval_ranking as ranking
    from . import llm

    conn = db.connect()
    profile = cfg.load_profile()
    selected = models or llm.SCORE_MODEL
    selected_models = [m.strip() for m in selected.split(",") if m.strip()]
    llm.ensure_available(selected_models)
    ranking.run(conn, profile, labels, selected_models)


@app.command("eval-extraction")
def eval_extraction(
    labels: Path = typer.Option(..., exists=True, readable=True, help="Feldlabels als JSONL"),
    models: str = typer.Option(
        "qwen3.5:9b,ministral-3:14b", help="Kommagetrennte Extraktionsmodelle"
    ),
) -> None:
    """Extraktionsmodelle read-only gegen handgelabelte Inseratsfelder vergleichen."""
    from . import eval_extraction as extraction_eval
    from . import llm

    conn = db.connect()
    selected = [model.strip() for model in models.split(",") if model.strip()]
    llm.ensure_available(selected)
    extraction_eval.run(conn, labels, selected)


@app.command()
def score(limit: int | None = typer.Option(None)) -> None:
    """Harte Filter + LLM-Score gegen profile.local.yaml."""
    from . import match

    conn = db.connect()
    profile = cfg.load_profile()
    done = match.score_pending(conn, profile, limit=limit)
    typer.echo(
        f"{done} Postings gescort (profile_version={profile.profile_version}, "
        f"score_version={match.SCORE_VERSION}, model={match.llm.SCORE_MODEL})."
    )


@app.command()
def travel(rebuild: bool = typer.Option(False, "--rebuild", help="Cache leeren (Fahrplanwechsel)")) -> None:
    """Öffi-Fahrzeiten Anker × Standort via Transitous (gecacht)."""
    from .travel import transitous

    conn = db.connect()
    profile = cfg.load_profile()
    done = transitous.rebuild(conn, profile) if rebuild else transitous.compute_missing(conn, profile)
    typer.echo(f"{done} Fahrzeiten berechnet.")


@app.command()
def export() -> None:
    """JSON-Export nach site/public/data/."""
    from . import export as exp

    conn = db.connect()
    profile = cfg.load_profile()
    meta = exp.export_all(conn, profile)
    typer.echo(f"Export: {meta['counts']}")


@app.command()
def companies(geocode: bool = typer.Option(False, help="fehlende Koordinaten via Nominatim vorschlagen")) -> None:
    """companies.yaml in die DB synchronisieren."""
    from . import companies as comp

    conn = db.connect()
    n = comp.sync_companies(conn, cfg.load_companies())
    typer.echo(f"{n} Firmen synchronisiert.")
    if geocode:
        g = comp.geocode_missing(conn)
        typer.echo(f"{g} Standorte geokodiert (Vorschläge — in companies.yaml prüfen!).")


@app.command()
def report(
    days: int = typer.Option(1, help="Zeitfenster in Tagen"),
    out: Path | None = typer.Option(None, help="Zieldatei (z. B. Obsidian-Vault); default stdout + data/"),
) -> None:
    """Markdown-Tagesreport: Top 10 neu, Initiativ-Top 5."""
    from . import report as rep

    conn = db.connect()
    profile = cfg.load_profile()
    md = rep.daily_report(conn, profile, days=days)
    target = out or paths.data_dir() / f"report-{datetime.now():%Y-%m-%d}.md"
    target.write_text(md, encoding="utf-8")
    typer.echo(md)
    typer.echo(f"\n→ {target}")


@app.command()
def daily(career_pages: bool | None = typer.Option(None, help="Karriereseiten erzwingen/übergehen (default: sonntags)")) -> None:
    """Kompletter Tageslauf: fetch → extract → locations → companies → travel → score → export → report."""
    with_career = career_pages if career_pages is not None else datetime.now().weekday() == 6
    fetch(career_pages=with_career)
    extract(limit=None)
    locations(limit=None)
    companies(geocode=True)
    travel(rebuild=False)
    score(limit=None)
    export()
    report(days=1, out=None)


def main() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    app()


if __name__ == "__main__":
    main()
