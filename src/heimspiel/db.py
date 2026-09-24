"""SQLite-Schema und Migrationen (eine Datei, siehe SPEC §4)."""

import sqlite3
from pathlib import Path

from . import paths

# Migrationen laufen über PRAGMA user_version; jede Liste = ein Versionssprung.
MIGRATIONS: list[list[str]] = [
    [
        """CREATE TABLE companies(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            website TEXT,
            career_url TEXT,
            seed_source TEXT,
            notes TEXT
        )""",
        """CREATE TABLE sites(
            id INTEGER PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id),
            label TEXT NOT NULL,
            lat REAL,
            lon REAL,
            address_text TEXT,
            is_hq INTEGER NOT NULL DEFAULT 0,
            geocode_source TEXT,
            UNIQUE(company_id, label)
        )""",
        """CREATE TABLE anchors(
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL
        )""",
        """CREATE TABLE postings_raw(
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            source_id TEXT NOT NULL,
            url TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            raw_title TEXT,
            raw_company TEXT,
            raw_location TEXT,
            raw_text TEXT,
            content_hash TEXT NOT NULL,
            duplicate_of INTEGER REFERENCES postings_raw(id),
            UNIQUE(source, source_id)
        )""",
        "CREATE INDEX idx_raw_hash ON postings_raw(content_hash)",
        "CREATE INDEX idx_raw_company ON postings_raw(raw_company)",
        """CREATE TABLE postings(
            id INTEGER PRIMARY KEY,
            raw_id INTEGER NOT NULL UNIQUE REFERENCES postings_raw(id),
            company_id INTEGER REFERENCES companies(id),
            site_id INTEGER REFERENCES sites(id),
            extracted_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            model TEXT NOT NULL,
            extracted_at TEXT NOT NULL
        )""",
        """CREATE TABLE scores(
            posting_id INTEGER NOT NULL REFERENCES postings(id),
            profile_version INTEGER NOT NULL,
            hard_pass INTEGER NOT NULL,
            hard_reasons TEXT,
            fit_score INTEGER,
            fit_reasons TEXT,
            gaps TEXT,
            angle TEXT,
            model TEXT,
            scored_at TEXT NOT NULL,
            PRIMARY KEY (posting_id, profile_version)
        )""",
        """CREATE TABLE travel_times(
            site_id INTEGER NOT NULL REFERENCES sites(id),
            anchor_id TEXT NOT NULL REFERENCES anchors(id),
            minutes INTEGER,
            transfers INTEGER,
            engine TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (site_id, anchor_id)
        )""",
        """CREATE TABLE career_snapshots(
            id INTEGER PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id),
            fetched_at TEXT NOT NULL,
            positions_json TEXT NOT NULL,
            diff_new TEXT,
            diff_closed TEXT
        )""",
    ],
    [
        # sites.company_id nullable machen (generische, firmenlose Standorte für
        # Postings ohne Company-Match) — SQLite kennt kein ALTER COLUMN DROP NOT NULL,
        # daher Table-Rebuild. id-Werte bleiben stabil (explizite Spaltenkopie), also
        # bleiben postings.site_id/travel_times.site_id gültig.
        "PRAGMA foreign_keys=OFF",
        """CREATE TABLE sites_new(
            id INTEGER PRIMARY KEY,
            company_id INTEGER REFERENCES companies(id),
            label TEXT NOT NULL,
            lat REAL,
            lon REAL,
            address_text TEXT,
            is_hq INTEGER NOT NULL DEFAULT 0,
            geocode_source TEXT,
            UNIQUE(company_id, label)
        )""",
        """INSERT INTO sites_new SELECT id, company_id, label, lat, lon,
           address_text, is_hq, geocode_source FROM sites""",
        "DROP TABLE sites",
        "ALTER TABLE sites_new RENAME TO sites",
        "CREATE UNIQUE INDEX idx_sites_generic_label ON sites(label) WHERE company_id IS NULL",
        "PRAGMA foreign_keys=ON",
        """CREATE TABLE location_cache(
            location_key TEXT PRIMARY KEY,
            location_text_raw TEXT NOT NULL,
            city TEXT,
            schema_version INTEGER NOT NULL,
            model TEXT NOT NULL,
            resolved_at TEXT NOT NULL
        )""",
    ],
    [
        # in_austria: city=NULL bisher zweideutig ("unklar" vs. "eindeutig
        # Ausland", z. B. XING-Treffer aus Hamburg/München/Zürich) — der harte
        # Filter (match.py) braucht das Signal, um Auslands-Stellen abzulehnen
        # statt nur zu flaggen. Default 1 ist sicher: LOCATION_SCHEMA_VERSION-Bump
        # in derselben Änderung erzwingt ohnehin die Neuauflösung aller Einträge.
        "ALTER TABLE location_cache ADD COLUMN in_austria INTEGER NOT NULL DEFAULT 1",
    ],
    [
        # Auslands-Sites sollen auf der Karte erscheinen (Nutzer-Wunsch), aber
        # keine Transitous-Fahrzeiten bekommen (sinnlose Routen, Policy: Last
        # klein halten) — travel/transitous.py filtert auf in_austria=1.
        "ALTER TABLE sites ADD COLUMN in_austria INTEGER NOT NULL DEFAULT 1",
    ],
    [
        # Evidenzbasiertes Ranking v2. Der alte Primärschlüssel
        # (posting_id, profile_version) konnte entgegen der Cache-Konvention nur
        # ein Modell/eine Formel je Profil halten. Der Rebuild erhält v1-Scores
        # und macht Formel + Modell zu echten Teilen des Cache-Keys.
        """CREATE TABLE scores_new(
            posting_id INTEGER NOT NULL REFERENCES postings(id),
            profile_version INTEGER NOT NULL,
            hard_pass INTEGER NOT NULL,
            hard_reasons TEXT,
            fit_score INTEGER,
            fit_reasons TEXT,
            gaps TEXT,
            angle TEXT,
            model TEXT NOT NULL,
            scored_at TEXT NOT NULL,
            score_version INTEGER NOT NULL DEFAULT 1,
            score_breakdown TEXT,
            score_confidence INTEGER,
            score_evidence TEXT,
            formal_status TEXT,
            formal_reasons TEXT,
            practical_status TEXT,
            practical_reasons TEXT,
            fallback_model TEXT,
            PRIMARY KEY (posting_id, profile_version, score_version, model)
        )""",
        """INSERT INTO scores_new(
               posting_id, profile_version, hard_pass, hard_reasons, fit_score,
               fit_reasons, gaps, angle, model, scored_at, score_version)
           SELECT posting_id, profile_version, hard_pass, hard_reasons, fit_score,
                  fit_reasons, gaps, angle, COALESCE(model, 'unknown'), scored_at, 1
           FROM scores""",
        "DROP TABLE scores",
        "ALTER TABLE scores_new RENAME TO scores",
    ],
    [
        # Jev-Scoring (SCORE_VERSION 4): Bewerbungsempfehlung + Datenbasis je Score.
        "ALTER TABLE scores ADD COLUMN recommendation TEXT",
        "ALTER TABLE scores ADD COLUMN recommendation_probs TEXT",
        "ALTER TABLE scores ADD COLUMN recommendation_notes TEXT",
        "ALTER TABLE scores ADD COLUMN text_quality TEXT",
    ],
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or paths.db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target, statements in enumerate(MIGRATIONS, start=1):
        if version < target:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {target}")
            conn.commit()


# Teure, extern rate-limitierte Caches (Nominatim-Geocodes, Transitous-Fahrzeiten,
# LLM-Standortauflösung), die einen Neuaufbau überleben. Reihenfolge = FK-Reihenfolge.
GEO_TABLES = ["companies", "anchors", "sites", "travel_times", "location_cache"]


def reset(path: Path | None = None, keep_geo: bool = True) -> Path:
    """DB sichern und frisch anlegen; optional die Geo-/Fahrzeit-Caches übernehmen.
    Rückgabe: Pfad des Backups."""
    from datetime import datetime

    p = path or paths.db_path()
    backup = p.with_name(f"{p.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    if p.exists():
        old = sqlite3.connect(p)
        old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        old.execute(f"VACUUM INTO '{backup}'")
        old.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{p}{suffix}").unlink(missing_ok=True)
    conn = connect(p)
    if keep_geo and backup.exists():
        conn.execute("ATTACH DATABASE ? AS old", (str(backup),))
        for table in GEO_TABLES:
            columns = [r["name"] for r in conn.execute(f"PRAGMA main.table_info({table})")]
            old_columns = {r["name"] for r in conn.execute(f"PRAGMA old.table_info({table})")}
            shared = ", ".join(c for c in columns if c in old_columns)
            conn.execute(f"INSERT INTO main.{table} ({shared}) SELECT {shared} FROM old.{table}")
        conn.commit()
        conn.execute("DETACH DATABASE old")
    conn.close()
    return backup
