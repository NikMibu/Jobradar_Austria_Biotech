import sqlite3
from pathlib import Path

import pytest

from heimspiel import db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    return db.connect(tmp_path / "test.db")


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
