"""Zentrale Pfade. HEIMSPIEL_ROOT überschreibt die Repo-Wurzel (Tests, fremde Checkouts)."""

import os
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("HEIMSPIEL_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def config_dir() -> Path:
    return repo_root() / "config"


def data_dir() -> Path:
    d = repo_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    env = os.environ.get("HEIMSPIEL_DB")
    if env:
        return Path(env)
    return data_dir() / "heimspiel.db"


def site_data_dir() -> Path:
    d = repo_root() / "site" / "public" / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
