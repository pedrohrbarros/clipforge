import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import settings

bearer_scheme = HTTPBearer(auto_error=False, description="A ClipForge session token")
_google_request = google_requests.Request()
ALGORITHM = "HS256"


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str
    picture: str = ""


class AuthError(HTTPException):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(
            status,
            {"code": code, "message": message},
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )


def _verify_google(token: str) -> User:
    claims = id_token.verify_oauth2_token(token, _google_request, settings.google_client_id)
    if not claims.get("email_verified", False):
        raise ValueError("email not verified")
    return User(
        id=claims["sub"],
        email=claims["email"],
        name=claims.get("name") or "",
        picture=claims.get("picture") or "",
    )


async def verify_google_token(token: str) -> User:
    """Validate a Google ID token (signature, audience, expiry) and return the user."""
    if not settings.google_client_id:
        raise AuthError(503, "auth_unconfigured", "GOOGLE_CLIENT_ID is not set")
    try:
        return await asyncio.to_thread(_verify_google, token)
    except ValueError as e:
        raise AuthError(401, "unauthorized", f"Invalid Google sign-in: {e}") from e


def issue_session(user: User) -> tuple[str, dt.datetime]:
    """A signed session token for the user, valid for SESSION_HOURS."""
    if not settings.session_secret:
        raise AuthError(503, "auth_unconfigured", "SESSION_SECRET is not set")
    now = dt.datetime.now(dt.UTC)
    expires = now + dt.timedelta(hours=settings.session_hours)
    claims = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(claims, settings.session_secret, algorithm=ALGORITHM), expires


def read_session(token: str) -> User:
    if not settings.session_secret:
        raise AuthError(503, "auth_unconfigured", "SESSION_SECRET is not set")
    try:
        claims = jwt.decode(token, settings.session_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise AuthError(401, "session_expired", "Your session expired, sign in again") from e
    except jwt.PyJWTError as e:
        raise AuthError(401, "unauthorized", "Invalid session, sign in again") from e
    return User(
        id=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name", ""),
        picture=claims.get("picture", ""),
    )


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if not creds or not creds.credentials:
        raise AuthError(401, "unauthorized", "Sign in with Google to continue")
    return read_session(creds.credentials)


CurrentUser = Annotated[User, Depends(current_user)]
