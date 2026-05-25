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

    def __repr__(self) -> str:
        # Redact secrets so a naive `log.debug(tokens)` in a consumer's code
        # doesn't leak credentials to logs / crash reporters / aggregators.
        rt = "<redacted>" if self.refresh_token is not None else "None"
        return (f"OAuthTokens(access_token=<redacted>, refresh_token={rt}, "
                f"expires_at={self.expires_at!r}, scopes={self.scopes!r}, "
                f"provider={self.provider!r}, provider_extras={self.provider_extras!r})")
