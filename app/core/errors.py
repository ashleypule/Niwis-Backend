"""API errors.

Every error body follows the contract (`{"error": string, "issues"?: [...]}`). The data-unavailable convention
(docs/CONTRACT_CHANGES.md) adds `code`, `detail` and `missing` to that body:

    501 {"error": "...", "code": "DATA_UNAVAILABLE", "detail": "...", "missing": ["..."]}

Unexpected exceptions return a generic 500; details go to the server log only.
"""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    status_code = 500
    code: str | None = None

    def __init__(self, error: str, *, issues: list[Any] | None = None, **extra: Any) -> None:
        super().__init__(error)
        self.error = error
        self.issues = issues
        self.extra = extra

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error": self.error}
        if self.code:
            out["code"] = self.code
        if self.issues:
            out["issues"] = self.issues
        out.update({k: v for k, v in self.extra.items() if v is not None})
        return out


class BadRequest(APIError):
    status_code = 400
    code = "BAD_REQUEST"


class NotFound(APIError):
    status_code = 404
    code = "NOT_FOUND"


class DataUnavailable(APIError):
    """The database cannot serve this endpoint / dataset yet (missing, withheld or not yet modelled)."""

    status_code = 501
    code = "DATA_UNAVAILABLE"

    def __init__(self, detail: str, missing: list[str] | None = None) -> None:
        super().__init__(detail, detail=detail, missing=missing or [])


class ServiceUnavailable(APIError):
    status_code = 503
    code = "SERVICE_UNAVAILABLE"
