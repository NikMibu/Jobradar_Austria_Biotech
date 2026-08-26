"""Laden der YAML-Konfigurationen: search.yaml, companies.yaml, profile.local.yaml."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import paths


def _load_yaml(p: Path) -> Any:
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class SearchConfig:
    terms: list[str]
    locations: list[str]


@dataclass
class Anchor:
    id: str
    label: str
    max_minutes: int
    lat: float | None = None
    lon: float | None = None


@dataclass
class Profile:
    profile_version: int
    earliest_start: str | None
    education: str | None
    phd_wanted: bool
    role_families_allowed: list[str]
    seniority_allowed: list[str]
    interests: list[str]
    skills: dict[str, Any]
    hard_no: list[str]
    anchors: list[Anchor]
    max_years_experience: int = 3
    travel_policy: str = "any_anchor"
    raw: dict[str, Any] = field(default_factory=dict)


def load_search(path: Path | None = None) -> SearchConfig:
    d = _load_yaml(path or paths.config_dir() / "search.yaml")
    return SearchConfig(terms=d.get("terms", []), locations=d.get("locations", []))


@dataclass
class AtsConfig:
    erecruiter_hosts: dict[str, str]
    successfactors_tenants: dict[str, str]
    euraxess_max_pages: int


def load_ats(path: Path | None = None) -> AtsConfig:
    p = path or paths.config_dir() / "ats.yaml"
    d = _load_yaml(p) if p.exists() else {}
    d = d or {}
    return AtsConfig(
        erecruiter_hosts=d.get("erecruiter_hosts") or {},
        successfactors_tenants=d.get("successfactors_tenants") or {},
        euraxess_max_pages=int(d.get("euraxess_max_pages", 5)),
    )


def load_companies(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or paths.config_dir() / "companies.yaml"
    if not p.exists():
        return []
    return _load_yaml(p) or []


def load_profile(path: Path | None = None) -> Profile:
    p = path or paths.config_dir() / "profile.local.yaml"
    if not p.exists():
        example = paths.config_dir() / "profile.example.yaml"
        raise FileNotFoundError(
            f"{p} fehlt. Kopiere {example} nach profile.local.yaml und fülle sie aus."
        )
    d = _load_yaml(p)
    anchors = [Anchor(**a) for a in d.get("anchors", [])]
    return Profile(
        profile_version=d.get("profile_version", 1),
        earliest_start=str(d["earliest_start"]) if d.get("earliest_start") else None,
        education=d.get("education"),
        phd_wanted=bool(d.get("phd_wanted", False)),
        role_families_allowed=d.get("role_families_allowed", []),
        seniority_allowed=d.get("seniority_allowed", ["entry", "junior", "mid"]),
        max_years_experience=int(d.get("max_years_experience", 3)),
        interests=d.get("interests", []),
        skills=d.get("skills", {}) or {},
        hard_no=d.get("hard_no", []),
        anchors=anchors,
        travel_policy=d.get("travel_policy", "any_anchor"),
        raw=d,
    )
