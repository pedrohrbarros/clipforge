import asyncio
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import settings

bearer_scheme = HTTPBearer(auto_error=False, description="A Google ID token for this app")
_google_request = google_requests.Request()


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str


class AuthError(HTTPException):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(
            status,
            {"code": code, "message": message},
            headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
        )


def _verify(token: str) -> User:
    claims = id_token.verify_oauth2_token(token, _google_request, settings.google_client_id)
    if not claims.get("email_verified", False):
        raise ValueError("email not verified")
    return User(id=claims["sub"], email=claims["email"], name=claims.get("name") or "")


async def verify_google_token(token: str) -> User:
    """Validate a Google ID token (signature, audience, expiry) and return the user."""
    try:
        return await asyncio.to_thread(_verify, token)
    except ValueError as e:
        raise AuthError(401, "unauthorized", f"Invalid Google sign-in: {e}") from e


async def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if not settings.google_client_id:
        raise AuthError(503, "auth_unconfigured", "GOOGLE_CLIENT_ID is not set")
    token = creds.credentials if creds else ""
    if not token:
        raise AuthError(401, "unauthorized", "Sign in with Google to continue")
    return await verify_google_token(token)


CurrentUser = Annotated[User, Depends(current_user)]
