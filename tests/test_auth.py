import datetime as dt

import jwt
import pytest
from fastapi.testclient import TestClient

from app import auth, main
from app.auth import AuthError, User, issue_session, read_session
from app.config import settings
from app.main import app

USER = User(id="g1", email="p@x.y", name="P", picture="https://pic")


def test_session_round_trip_and_expiry():
    token, expires = issue_session(USER)
    assert expires - dt.datetime.now(dt.UTC) > dt.timedelta(hours=23)
    assert read_session(token) == USER
    expired = jwt.encode(
        {"sub": "g1", "exp": int(dt.datetime.now(dt.UTC).timestamp()) - 1},
        settings.session_secret,
        algorithm="HS256",
    )
    with pytest.raises(AuthError) as e:
        read_session(expired)
    assert e.value.detail["code"] == "session_expired"
    with pytest.raises(AuthError):
        read_session(jwt.encode({"sub": "g1"}, "other-secret", algorithm="HS256"))


def test_sign_in_exchanges_a_google_token_for_a_session(monkeypatch, anonymous):
    async def fake_verify(token):
        assert token == "google-token"
        return USER

    monkeypatch.setattr(main, "verify_google_token", fake_verify)
    with TestClient(app) as client:
        res = client.post("/auth/google", json={"credential": "google-token"})
        assert res.status_code == 200
        body = res.json()
        assert body["user"]["email"] == "p@x.y"
        me = client.get("/me", headers={"Authorization": f"Bearer {body['token']}"})
        assert me.status_code == 200 and me.json()["email"] == "p@x.y"


def test_requests_without_a_session_are_rejected(anonymous):
    with TestClient(app) as client:
        assert client.get("/me").status_code == 401
        assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert auth.ALGORITHM == "HS256"
