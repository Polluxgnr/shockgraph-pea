"""API-key protection for the sensitive endpoints (LLM calls and GDS writes)."""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Depends(api_key_header), settings: Settings = Depends(get_settings)) -> None:
    """Reject the request with 401 unless the X-API-Key header matches API_KEY (constant-time compare)."""
    if key is None or not secrets.compare_digest(key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid X-API-Key")
