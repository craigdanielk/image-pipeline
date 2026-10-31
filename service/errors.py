"""Typed refusals. SQL raises SQLSTATE CS4xx/CS5xx; the API maps them 1:1."""
from __future__ import annotations

from dataclasses import dataclass

# SQLSTATE -> HTTP status. Anything unmapped is a real server error and is
# surfaced as 500 with a redacted code, never swallowed.
_SQLSTATE_HTTP = {
    "CS401": 401,
    "CS403": 403,
    "CS404": 404,
    "CS409": 409,
    "CS422": 422,
    "CS503": 503,
    "42501": 403,   # insufficient_privilege: RLS / grant refusal
}


@dataclass
class ServiceError(Exception):
    status: int
    code: str
    detail: str = ""

    def body(self) -> dict:
        out = {"error": self.code}
        if self.detail:
            out["detail"] = self.detail
        return out


def from_db_error(exc) -> ServiceError:
    """Translate a psycopg2 error raised by a cs_* procedure."""
    sqlstate = getattr(exc, "pgcode", None) or ""
    diag = getattr(exc, "diag", None)
    message = (getattr(diag, "message_primary", None) or "").strip()
    if sqlstate in _SQLSTATE_HTTP:
        code = message.split(":")[0].split()[0] if message else "refused"
        if sqlstate == "42501":
            code = "capability_denied"
        return ServiceError(_SQLSTATE_HTTP[sqlstate], code)
    return ServiceError(500, "internal_error", f"sqlstate {sqlstate or 'unknown'}")
