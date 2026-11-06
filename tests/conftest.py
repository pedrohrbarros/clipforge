import pytest

from app import main
from app.auth import User, current_user
from app.config import settings
from app.db import Db
from app.main import app
from app.storage import Storage

FAKE_USER = User(id="u1", email="pedro@example.com", name="Pedro")


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    """Tests act as a signed-in user with database and storage disabled."""
    settings.google_client_id = "test-client"
    settings.session_secret = "test-secret"
    app.dependency_overrides[current_user] = lambda: FAKE_USER
    offline_db, offline_storage = Db(""), Storage("", "", "shorts")
    for name, value in (("db", offline_db), ("storage", offline_storage)):
        monkeypatch.setattr(main, name, value)
        monkeypatch.setattr(main.services, name, value)
    yield
    app.dependency_overrides.clear()
    settings.google_client_id = ""
    settings.session_secret = ""


@pytest.fixture
def anonymous():
    app.dependency_overrides.clear()
    yield
