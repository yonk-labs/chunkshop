# src/chunkshop/oauth/refresh.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from chunkshop.oauth.tokens import OAuthTokens
from chunkshop.oauth.base import OAuthProvider


def proactive_refresh(tokens: OAuthTokens, *, provider: OAuthProvider,
                      leeway_minutes: int = 5) -> Optional[OAuthTokens]:
    """Refresh tokens if they expire within leeway_minutes, else return None.

    Avoids the reactive-401 refresh race where two concurrent callers both see
    a 401 and both try to refresh, one losing its refresh token."""
    if tokens.refresh_token is None:
        return None
    now = datetime.now(timezone.utc)
    if tokens.expires_at - now <= timedelta(minutes=leeway_minutes):
        return provider.refresh_token(tokens.refresh_token)
    return None
