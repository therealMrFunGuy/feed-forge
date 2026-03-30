"""Shared auth client — drop this file into each service directory.

Usage in any FastAPI service:

    from auth_client import require_auth

    @app.get("/my-endpoint")
    async def my_endpoint(auth: dict = Depends(require_auth)):
        user_id = auth["user_id"]
        tier = auth["tier"]
"""

import os
import httpx
from fastapi import HTTPException, Request

AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "http://localhost:8499")
_client = None

def _get_client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(base_url=AUTH_SERVICE_URL, timeout=5.0)
    return _client


async def validate_key(api_key: str) -> dict:
    """Validate an API key against the auth service."""
    client = _get_client()
    try:
        resp = await client.post("/validate", json={"api_key": api_key})
        return resp.json()
    except Exception:
        # Auth service down — allow request with free tier (graceful degradation)
        return {"valid": True, "tier": "free", "user_id": "anonymous", "degraded": True}


async def require_auth(request: Request) -> dict:
    """FastAPI dependency — extracts and validates API key from request."""
    api_key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not api_key:
        # Allow unauthenticated requests with free tier limits
        return {"valid": True, "tier": "free", "user_id": "anonymous"}

    result = await validate_key(api_key)
    if not result.get("valid"):
        error = result.get("error", "Invalid API key")
        if "rate limit" in error.lower():
            raise HTTPException(429, detail=error)
        raise HTTPException(401, detail=error)

    return result
