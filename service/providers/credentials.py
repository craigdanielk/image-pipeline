"""Provider credential sealing and short-lived materialisation.

The database stores only AES-256-GCM ciphertext (cs_private.cs_credentials);
key material comes from the runtime environment (CS_CREDENTIAL_KEYS, provisioned
by CS-B5 deployment, never committed). The associated data binds a ciphertext
to its provider account fingerprint so a row cannot be moved to another account.

The Higgsfield adapter consumes a CLI credentials *file*; materialise() writes
the decrypted bytes to a 0600 temp file for the duration of one provider call
and removes it afterwards. The machine's own CLI login is never used here.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYS_ENV = "CS_CREDENTIAL_KEYS"


class CredentialError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class SealedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: str


class KeyRing:
    """{version: 32-byte key}. The newest version (last in sorted order) seals."""

    def __init__(self, keys: dict[str, bytes]):
        if not keys:
            raise CredentialError("credential_keys_unconfigured")
        for version, key in keys.items():
            if len(key) != 32:
                raise CredentialError("credential_key_invalid", f"version {version} is not 256-bit")
        self._keys = dict(keys)

    @classmethod
    def from_env(cls, env=None) -> "KeyRing":
        raw = (env if env is not None else os.environ).get(KEYS_ENV, "").strip()
        if not raw:
            single = (env if env is not None else os.environ).get("CS_CREDENTIAL_ENCRYPTION_KEY_V1", "").strip()
            if single:
                raw = json.dumps({"1": single})
            else:
                raise CredentialError("credential_keys_unconfigured", f"set {KEYS_ENV} or CS_CREDENTIAL_ENCRYPTION_KEY_V1")
        try:
            parsed = json.loads(raw)
            return cls({str(k): base64.b64decode(v, validate=True) for k, v in parsed.items()})
        except (ValueError, TypeError, AttributeError):
            raise CredentialError("credential_keys_invalid", f"{KEYS_ENV} must be a JSON object of base64 keys") from None

    @staticmethod
    def _aad(provider: str, fingerprint: str) -> bytes:
        return f"cs-credential-v1:{provider}:{fingerprint}".encode()

    def seal(self, plaintext: bytes, *, provider: str, fingerprint: str) -> SealedCredential:
        version = sorted(self._keys)[-1]
        nonce = os.urandom(12)
        ct = AESGCM(self._keys[version]).encrypt(nonce, plaintext, self._aad(provider, fingerprint))
        return SealedCredential(ct, nonce, version)

    # -- frozen dispatch envelopes (encrypted at rest; DATABASE_CONTRACT.md, CS-INT F5) ----------
    @staticmethod
    def _envelope_aad(workspace_id: str, attempt_id: str, envelope_sha256: str) -> bytes:
        return f"cs-dispatch-envelope-v1:{workspace_id}:{attempt_id}:{envelope_sha256}".encode()

    def seal_envelope(self, envelope: dict) -> SealedCredential:
        version = sorted(self._keys)[-1]
        nonce = os.urandom(12)
        plaintext = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        aad = self._envelope_aad(envelope["workspace_id"], envelope["attempt_id"], envelope["sha256"])
        return SealedCredential(AESGCM(self._keys[version]).encrypt(nonce, plaintext, aad), nonce, version)

    def open_envelope(self, sealed: SealedCredential, *, workspace_id: str, attempt_id: str,
                      envelope_sha256: str) -> dict:
        key = self._keys.get(sealed.key_version)
        if key is None:
            raise CredentialError("credential_key_unavailable", f"key version {sealed.key_version}")
        try:
            raw = AESGCM(key).decrypt(bytes(sealed.nonce), bytes(sealed.ciphertext),
                                      self._envelope_aad(workspace_id, attempt_id, envelope_sha256))
        except Exception:
            raise CredentialError("envelope_unreadable", "ciphertext does not open for this attempt") from None
        return json.loads(raw)

    def open(self, sealed: SealedCredential, *, provider: str, fingerprint: str) -> bytes:
        key = self._keys.get(sealed.key_version)
        if key is None:
            raise CredentialError("credential_key_unavailable", f"key version {sealed.key_version}")
        try:
            return AESGCM(key).decrypt(bytes(sealed.nonce), bytes(sealed.ciphertext), self._aad(provider, fingerprint))
        except Exception:
            raise CredentialError("credential_unreadable", "ciphertext does not open for this account") from None


@contextmanager
def materialise(plaintext: bytes):
    """Yield the absolute path of a private temp file holding the credential; always removed."""
    fd, path = tempfile.mkstemp(prefix="cs-provider-cred-")
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, plaintext)
        os.close(fd)
        fd = -1
        yield path
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
