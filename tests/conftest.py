import pytest

from app import main
from app.auth import User, current_user
from app.config import settings
from app.db import Db
from app.mailer import Mailer
from app.main import app
from app.storage import Storage

FAKE_USER = User(id="u1", email="pedro@example.com", name="Pedro")


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    """Tests act as a signed-in user with database, storage and mail disabled."""
    settings.google_client_id = "test-client"
    app.dependency_overrides[current_user] = lambda: FAKE_USER
    offline_db, offline_storage = Db(""), Storage("", "", "shorts")
    offline_mailer = Mailer("", "ClipForge <x@y.z>", "http://app", 24)
    for name, value in (
        ("db", offline_db),
        ("storage", offline_storage),
        ("mailer", offline_mailer),
    ):
        monkeypatch.setattr(main, name, value)
        monkeypatch.setattr(main.services, name, value)
    yield
    app.dependency_overrides.clear()
    settings.google_client_id = ""


@pytest.fixture
def anonymous():
    app.dependency_overrides.clear()
    yield
