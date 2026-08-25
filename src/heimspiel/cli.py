"""Typer-CLI: fetch | extract | score | travel | export | daily | report (SPEC §10)."""

import os
from datetime import datetime
from pathlib import Path

import typer

from . import config as cfg
from . import db, normalize, paths

app = typer.Typer(help="Heimspiel — persönlicher Jobradar", no_args_is_help=True)


@app.command()
def fetch(
    jobspy: bool = typer.Option(True, help="Indeed/LinkedIn/Google via JobSpy"),
    karriere: bool = typer.Option(True, help="karriere.at"),
    biotech: bool = typer.Option(True, help="biotechjobs.at"),
    vbc: bool = typer.Option(True, help="Vienna BioCenter"),
    career_pages: bool = typer.Option(False, "--career-pages", help="Firmen-Karriereseiten (wöchentlich)"),
) -> None:
    """Alle aktivierten Quellen abrufen und in postings_raw schreiben."""
    conn = db.connect()
    search = cfg.load_search()
    from .sources.base import store_postings

    total = 0
    if biotech:
        from .sources import biotechjobs

        typer.echo("biotechjobs.at …")
        total += store_postings(conn, biotechjobs.fetch())
    if vbc:
        from .sources import vbc as vbc_src

        typer.echo("Vienna BioCenter …")
        total += store_postings(conn, vbc_src.fetch())
    if karriere:
        from .sources import karriere_at

        typer.echo("karriere.at …")
        total += store_postings(conn, karriere_at.fetch(search.terms, search.locations))
    if jobspy:
        from .sources import jobspy_src

        typer.echo("JobSpy (Indeed/LinkedIn/Google) …")
        total += store_postings(conn, jobspy_src.fetch(search.terms))
    if career_pages:
        from .sources import career_pages as cp

        typer.echo("Firmen-Karriereseiten …")
        total += cp.watch_all(conn)

    marked = normalize.dedup(conn)
    typer.echo(f"{total} neue Inserate, {marked} Duplikate markiert.")


@app.command()
def extract(limit: int | None = typer.Option(None, help="max. Anzahl")) -> None:
    """LLM-Extraktion aller noch nicht extrahierten Postings (gecacht)."""
    from . import extract as ex

    conn = db.connect()
    pending = len(ex.pending_raws(conn))
    typer.echo(f"{pending} Postings zu extrahieren …")
    done = ex.extract_pending(conn, limit=limit)
    typer.echo(f"{done} extrahiert.")


@app.command()
def score(limit: int | None = typer.Option(None)) -> None:
    """Harte Filter + LLM-Score gegen profile.local.yaml."""
    from . import match

    conn = db.connect()
    profile = cfg.load_profile()
    done = match.score_pending(conn, profile, limit=limit)
    typer.echo(f"{done} Postings gescort (profile_version={profile.profile_version}).")


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
    """Kompletter Tageslauf: fetch → extract → score → travel → export → report."""
    with_career = career_pages if career_pages is not None else datetime.now().weekday() == 6
    fetch(career_pages=with_career)
    extract(limit=None)
    score(limit=None)
    travel(rebuild=False)
    export()
    report(days=1, out=None)


def main() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    app()


if __name__ == "__main__":
    main()
