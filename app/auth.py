import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings

OPEN_PATHS = {"/health"}


def _token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # Media URLs (<video src>, download links) cannot send headers, so accept ?token= too.
    return request.query_params.get("token", "")


async def require_key(request: Request, call_next):
    """Reject any request without the shared AUTHORIZATION_KEY, except health and preflight."""
    if request.url.path in OPEN_PATHS or request.method == "OPTIONS":
        return await call_next(request)
    if not settings.authorization_key:
        return JSONResponse(
            {"detail": {"code": "auth_unconfigured", "message": "AUTHORIZATION_KEY is not set"}},
            status_code=503,
        )
    if not secrets.compare_digest(_token(request), settings.authorization_key):
        return JSONResponse(
            {"detail": {"code": "unauthorized", "message": "Invalid or missing access key"}},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await call_next(request)
