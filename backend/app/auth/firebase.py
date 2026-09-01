"""Identity-token verification (master plan §6).

Two modes, chosen by ``AUTH_MODE``:

``firebase`` (production behaviour)
    Verify Google-issued Firebase ID tokens. Only the *project ID* is needed
    for verification — firebase-admin fetches Google's public signing certs,
    so no service account is required merely to authenticate requests. A
    service account (``FIREBASE_SERVICE_ACCOUNT``) is additionally supported
    for deployments that also manage users server-side.

``dev`` (local development / CI)
    Accepts an unsigned local bearer token of the form ``dev:<email>`` and
    derives a deterministic pseudo-UID. **Never available in production** —
    ``Settings`` aborts startup if this combination is configured
    (plan §70 — rule 1).

Security note: the token proves *identity only*. Role and status are always
read from the local user record, never from token claims.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from ..core.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Identity:
    """Verified identity extracted from a bearer token."""

    uid: str
    email: str
    display_name: str | None
    provider: str  # "firebase" | "dev"


class TokenVerificationError(Exception):
    """Raised when a bearer token cannot be verified."""


class _FirebaseVerifier:
    def __init__(self, settings: Settings) -> None:
        import firebase_admin

        options: dict = {}
        if settings.FIREBASE_PROJECT_ID:
            options["projectId"] = settings.FIREBASE_PROJECT_ID
        if settings.FIREBASE_SERVICE_ACCOUNT:
            from firebase_admin import credentials

            cred = credentials.Certificate(settings.FIREBASE_SERVICE_ACCOUNT)
            firebase_admin.initialize_app(cred, options or None)
        else:
            # Certificate-less verification: public certs come from Google.
            firebase_admin.initialize_app(options=options or None)
        self._project_id = settings.FIREBASE_PROJECT_ID
        log.info("firebase verifier initialised (project_id=%s)", self._project_id)

    def verify(self, token: str) -> Identity:
        from firebase_admin import auth as firebase_auth

        try:
            decoded = firebase_auth.verify_id_token(token)
        except Exception as exc:  # firebase raises several specific types
            raise TokenVerificationError(str(exc)) from exc
        email = (decoded.get("email") or "").strip().lower()
        if not email:
            raise TokenVerificationError("token contains no email claim")
        return Identity(
            uid=str(decoded["uid"]),
            email=email,
            display_name=decoded.get("name"),
            provider="firebase",
        )


class _DevVerifier:
    """Unsigned local identity for development and automated tests."""

    PREFIX = "dev:"

    def __init__(self, settings: Settings) -> None:
        if settings.is_production:  # defence in depth (config also gates this)
            raise RuntimeError("dev auth mode is forbidden in production")

    def verify(self, token: str) -> Identity:
        if not token.startswith(self.PREFIX):
            raise TokenVerificationError("not a dev token")
        email = token[len(self.PREFIX) :].strip().lower()
        if "@" not in email:
            raise TokenVerificationError("dev token must carry an email: 'dev:user@example.com'")
        uid = "dev-" + hashlib.sha256(email.encode()).hexdigest()[:24]
        return Identity(uid=uid, email=email, display_name=email.split("@")[0], provider="dev")


class TokenVerifier:
    """Mode dispatching verifier, constructed once at startup."""

    def __init__(self, settings: Settings) -> None:
        self._mode = settings.AUTH_MODE
        if self._mode == "firebase":
            self._impl: _FirebaseVerifier | _DevVerifier = _FirebaseVerifier(settings)
        else:
            self._impl = _DevVerifier(settings)
            log.warning("AUTH_MODE=dev — unsigned local tokens accepted (non-production only)")

    def verify(self, token: str) -> Identity:
        return self._impl.verify(token)


_verifier: TokenVerifier | None = None


def init_verifier(settings: Settings) -> TokenVerifier:
    global _verifier
    _verifier = TokenVerifier(settings)
    return _verifier


def get_verifier() -> TokenVerifier:
    if _verifier is None:  # lazy path (tests create their own app instances)
        raise RuntimeError("token verifier not initialised — app startup incomplete")
    return _verifier
