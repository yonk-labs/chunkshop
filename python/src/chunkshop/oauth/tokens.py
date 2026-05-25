# src/chunkshop/oauth/tokens.py
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime
    scopes: list[str]
    provider: str
    provider_extras: dict = field(default_factory=dict)
