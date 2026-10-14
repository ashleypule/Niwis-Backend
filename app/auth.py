"""Authentication hook.

Every router depends on `get_current_user`. Today it allows every request (the contract's optional
`Authorization: Bearer <token>` header is accepted and ignored). When logins and roles arrive, only this function
changes — routes and the response contract stay the same.
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
    # Placeholder: token verification goes here. Never log the header value.
    return User()
