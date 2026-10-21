"""Authentication hook — a documented no-op.

NIWIS is a public, open-source system with no login (owner's decision, DECISIONS D23): every endpoint is a read-only
GET and every request is anonymous. Every router still depends on `get_current_user`, so that if access control is ever
added only this function changes; routes and the response contract stay the same. The contract's optional
`Authorization: Bearer <token>` header is accepted and ignored (never logged).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Header


@dataclass(frozen=True)
class User:
    id: str = "anonymous"
    roles: frozenset[str] = field(default_factory=frozenset)
    authenticated: bool = False


async def get_current_user(authorization: str | None = Header(default=None, include_in_schema=False)) -> User:
    # Public system: no token verification. Never log the header value.
    return User()
