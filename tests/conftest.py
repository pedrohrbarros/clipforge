import pytest

from app.config import settings

KEY = "test-key"


@pytest.fixture(autouse=True)
def _auth_key():
    settings.authorization_key = KEY
    yield
    settings.authorization_key = ""
