import pytest

from app import main
from app.auth import User, current_user
from app.config import settings
from app.db import Db
from app.main import app

FAKE_USER = User(id="u1", email="pedro@example.com", name="Pedro")


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    """Tests act as a signed-in user against a disabled database; `anonymous` removes the user."""
    settings.google_client_id = "test-client"
    app.dependency_overrides[current_user] = lambda: FAKE_USER
    offline = Db("")
    monkeypatch.setattr(main, "db", offline)
    monkeypatch.setattr(main.services, "db", offline)
    yield
    app.dependency_overrides.clear()
    settings.google_client_id = ""


@pytest.fixture
def anonymous():
    app.dependency_overrides.clear()
    yield
