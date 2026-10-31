"""Bearer-token verification. Signature, issuer, audience, expiry and role are
all checked; an unsigned decode or client-supplied user id is never accepted.
Authorization is NOT decided here: the database checks current membership on
every statement using the verified subject."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import jwt

from .config import Settings
from .errors import ServiceError

_HS_ALGS = ["HS256"]
_ASYM_ALGS = ["RS256", "ES256"]


@dataclass(frozen=True)
class Actor:
    user_id: str
    claims: dict

    def db_claims(self) -> dict:
        # Only verified, minimal claims reach the database session.
        return {"sub": self.user_id, "role": "authenticated", "aud": self.claims.get("aud")}


class TokenVerifier:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jwks = jwt.PyJWKClient(settings.jwks_url) if settings.jwks_url else None

    def _key_and_algs(self, token: str):
        if self._jwks is not None:
            header = jwt.get_unverified_header(token)
            if header.get("alg") in _ASYM_ALGS:
                return self._jwks.get_signing_key_from_jwt(token).key, _ASYM_ALGS
        if self.settings.jwt_secret:
            return self.settings.jwt_secret, _HS_ALGS
        raise ServiceError(401, "authentication_required")

    def verify(self, authorization: str | None) -> Actor:
        if not authorization or not authorization.startswith("Bearer "):
            raise ServiceError(401, "authentication_required")
        token = authorization[len("Bearer "):].strip()
        try:
            key, algs = self._key_and_algs(token)
            claims = jwt.decode(token, key, algorithms=algs, audience=self.settings.jwt_audience,
                                issuer=self.settings.jwt_issuer,
                                options={"require": ["exp", "sub", "iss", "aud"]})
        except jwt.PyJWTError:
            raise ServiceError(401, "authentication_required") from None
        if claims.get("role") != "authenticated":
            raise ServiceError(401, "authentication_required")
        try:
            user_id = str(uuid.UUID(str(claims["sub"])))
        except ValueError:
            raise ServiceError(401, "authentication_required") from None
        return Actor(user_id=user_id, claims=claims)
