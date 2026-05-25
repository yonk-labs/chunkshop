# src/chunkshop/oauth/storage.py
from __future__ import annotations
from typing import Optional, Protocol, runtime_checkable
from chunkshop.oauth.tokens import OAuthTokens


@runtime_checkable
class OAuthTokenStorage(Protocol):
    """Interface only — storage is tenancy-scoped, so consumers own the impl
    (PG table, Vault, KMS, …). chunkshop never persists tokens."""
    async def get(self, user_id: str, provider: str) -> Optional[OAuthTokens]: ...
    async def put(self, user_id: str, provider: str, tokens: OAuthTokens) -> None: ...
    async def delete(self, user_id: str, provider: str) -> None: ...
