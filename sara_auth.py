"""Server-side SARA Authorization Code + PKCE support for SynapseMD.

The module is deliberately independent from Streamlit so the security-sensitive
state machine can be unit tested without a browser runtime.  Callers must pass
``st.session_state`` as the mutable state mapping.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import requests
from cryptography.fernet import Fernet, InvalidToken

CLIENT_ID = "synapse-web"
RESOURCE_AUDIENCE = "synapse"
DEVICE_NAME = "SynapseMD"
HTTP_TIMEOUT = (3.05, 8.0)

_AUTH_SESSION_KEY = "_sara_auth_session"
_AUTH_TRANSACTION_KEY = "_sara_auth_transaction"
_DEVICE_ID_KEY = "_sara_device_id"
_STATE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,256}$")
_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_LEGACY_FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._-]{0,254}$")
_CONSUMED_RETENTION_SECONDS = 600


class AuthError(Exception):
    """Base class whose messages never contain credentials or personal data."""


class AuthConfigurationError(AuthError):
    pass


class CallbackRejected(AuthError):
    pass


class IdentityUnavailable(AuthError):
    pass


class IdentityRejected(AuthError):
    def __init__(self, status_code: int):
        super().__init__(f"identity_rejected_{status_code}")
        self.status_code = status_code


class InvalidIdentityResponse(AuthError):
    pass


class NamespaceManifestError(AuthError):
    pass


class TransactionRejected(AuthError):
    pass


class TransactionStoreFull(AuthError):
    pass


class AuthorizationTransactionStore:
    """Short-lived, single-use PKCE transactions stored without raw state.

    Each operation opens its own SQLite connection so consumption is safe across
    Streamlit threads and processes that share this database file.  SQLite is a
    single-host deployment choice; multi-host deployments need a shared store
    with the same atomic consume contract.
    """

    def __init__(
        self,
        database_path: str,
        encryption_key: str,
        *,
        ttl_seconds: int = 300,
        max_pending: int = 1_000,
    ) -> None:
        if not database_path or database_path == ":memory:" or "\x00" in database_path:
            raise AuthConfigurationError("invalid_transaction_database")
        if not 60 <= ttl_seconds <= 600 or not 10 <= max_pending <= 100_000:
            raise AuthConfigurationError("invalid_transaction_store_limits")
        try:
            self._fernet = Fernet(encryption_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise AuthConfigurationError("invalid_transaction_key") from exc
        try:
            self._database_path = Path(database_path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise AuthConfigurationError("invalid_transaction_database") from exc
        self._ttl_seconds = ttl_seconds
        self._max_pending = max_pending
        self._initialize()

    @classmethod
    def from_config(cls, config: AuthConfig) -> AuthorizationTransactionStore:
        return cls(
            config.transaction_db_path,
            config.transaction_key,
            ttl_seconds=config.transaction_ttl_seconds,
            max_pending=config.transaction_max_pending,
        )

    def create(
        self,
        *,
        state: str,
        verifier: str,
        callback_url: str,
        now: float | None = None,
    ) -> None:
        if (
            _STATE_PATTERN.fullmatch(state) is None
            or _VERIFIER_PATTERN.fullmatch(verifier) is None
            or not callback_url
        ):
            raise TransactionRejected("authorization_transaction_rejected")
        current_time = time.time() if now is None else _valid_timestamp(now)
        state_hash = _hash_state(state)
        ciphertext = self._fernet.encrypt(verifier.encode("ascii"))
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._cleanup_in_transaction(connection, current_time)
                pending = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM oauth_transactions
                    WHERE consumed_at IS NULL AND expires_at > ?
                    """,
                    (current_time,),
                ).fetchone()[0]
                if pending >= self._max_pending:
                    raise TransactionStoreFull("authorization_transaction_limit")
                connection.execute(
                    """
                    INSERT INTO oauth_transactions (
                        state_hash, verifier_ciphertext, client_id, callback_url,
                        created_at, expires_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        state_hash,
                        ciphertext,
                        CLIENT_ID,
                        callback_url,
                        current_time,
                        current_time + self._ttl_seconds,
                    ),
                )
        except TransactionStoreFull:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise TransactionRejected("authorization_transaction_unavailable") from exc

    def is_pending(
        self,
        *,
        state: str,
        callback_url: str,
        now: float | None = None,
    ) -> bool:
        if _STATE_PATTERN.fullmatch(state) is None:
            return False
        current_time = time.time() if now is None else _valid_timestamp(now)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT 1
                    FROM oauth_transactions
                    WHERE state_hash = ? AND client_id = ? AND callback_url = ?
                      AND consumed_at IS NULL AND expires_at > ?
                    """,
                    (_hash_state(state), CLIENT_ID, callback_url, current_time),
                ).fetchone()
        except (sqlite3.Error, OSError) as exc:
            raise TransactionRejected("authorization_transaction_unavailable") from exc
        return row is not None

    def consume(
        self,
        *,
        state: str,
        callback_url: str,
        now: float | None = None,
    ) -> str:
        """Atomically consume one unexpired transaction and return its verifier."""

        if _STATE_PATTERN.fullmatch(state) is None or not callback_url:
            raise TransactionRejected("authorization_transaction_rejected")
        current_time = time.time() if now is None else _valid_timestamp(now)
        ciphertext: bytes | None = None
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT verifier_ciphertext
                    FROM oauth_transactions
                    WHERE state_hash = ? AND client_id = ? AND callback_url = ?
                      AND consumed_at IS NULL AND expires_at > ?
                    """,
                    (_hash_state(state), CLIENT_ID, callback_url, current_time),
                ).fetchone()
                if row is None:
                    raise TransactionRejected("authorization_transaction_rejected")
                updated = connection.execute(
                    """
                    UPDATE oauth_transactions
                    SET consumed_at = ?
                    WHERE state_hash = ? AND consumed_at IS NULL AND expires_at > ?
                    """,
                    (current_time, _hash_state(state), current_time),
                ).rowcount
                if updated != 1:
                    raise TransactionRejected("authorization_transaction_rejected")
                ciphertext = row[0]
        except TransactionRejected:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise TransactionRejected("authorization_transaction_unavailable") from exc

        try:
            verifier = self._fernet.decrypt(bytes(ciphertext)).decode("ascii")
        except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
            # Decryption occurs after commit: corrupt/key-mismatched rows remain
            # consumed and can never be retried with another authorization code.
            raise TransactionRejected("authorization_transaction_rejected") from exc
        if _VERIFIER_PATTERN.fullmatch(verifier) is None:
            raise TransactionRejected("authorization_transaction_rejected")
        return verifier

    def invalidate(self, *, state: str, now: float | None = None) -> bool:
        if _STATE_PATTERN.fullmatch(state) is None:
            return False
        current_time = time.time() if now is None else _valid_timestamp(now)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                updated = connection.execute(
                    """
                    UPDATE oauth_transactions
                    SET consumed_at = ?
                    WHERE state_hash = ? AND consumed_at IS NULL
                    """,
                    (current_time, _hash_state(state)),
                ).rowcount
        except (sqlite3.Error, OSError) as exc:
            raise TransactionRejected("authorization_transaction_unavailable") from exc
        return updated == 1

    def cleanup(self, *, now: float | None = None) -> int:
        current_time = time.time() if now is None else _valid_timestamp(now)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return self._cleanup_in_transaction(connection, current_time)
        except (sqlite3.Error, OSError) as exc:
            raise TransactionRejected("authorization_transaction_unavailable") from exc

    def _initialize(self) -> None:
        try:
            self._database_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._database_path, timeout=5.0) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA busy_timeout = 5000")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS oauth_transactions (
                        state_hash TEXT PRIMARY KEY,
                        verifier_ciphertext BLOB NOT NULL,
                        client_id TEXT NOT NULL,
                        callback_url TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL,
                        consumed_at REAL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS oauth_transactions_expiry_idx
                    ON oauth_transactions (expires_at, consumed_at)
                    """
                )
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(oauth_transactions)"
                    ).fetchall()
                }
                expected = {
                    "state_hash",
                    "verifier_ciphertext",
                    "client_id",
                    "callback_url",
                    "created_at",
                    "expires_at",
                    "consumed_at",
                }
                if columns != expected:
                    raise AuthConfigurationError("transaction_database_schema_invalid")
            if os.name != "nt":
                os.chmod(self._database_path, 0o600)
        except AuthConfigurationError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise AuthConfigurationError("transaction_store_unavailable") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _cleanup_in_transaction(connection: sqlite3.Connection, now: float) -> int:
        result = connection.execute(
            """
            DELETE FROM oauth_transactions
            WHERE expires_at <= ?
               OR (consumed_at IS NOT NULL AND consumed_at <= ?)
            """,
            (now, now - _CONSUMED_RETENTION_SECONDS),
        )
        return result.rowcount


@dataclass(frozen=True, slots=True)
class AuthConfig:
    portal_url: str
    identity_url: str
    callback_url: str
    transaction_db_path: str = field(repr=False)
    transaction_key: str = field(repr=False)
    transaction_ttl_seconds: int = 300
    transaction_max_pending: int = 1_000
    legacy_namespace_manifest: str | None = field(default=None, repr=False)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> AuthConfig:
        environment = environment or {}

        def value(name: str) -> str:
            configured = values.get(name)
            raw = configured if configured not in (None, "") else environment.get(name, "")
            return str(raw).strip()

        portal_url = value("SARA_AUTH_PORTAL_URL")
        identity_url = value("SARA_IDENTITY_URL")
        callback_url = value("SARA_AUTH_CALLBACK_URL")
        if not portal_url or not identity_url or not callback_url:
            raise AuthConfigurationError("missing_sara_auth_configuration")

        transaction_key = value("SARA_AUTH_TRANSACTION_KEY")
        if not transaction_key:
            raise AuthConfigurationError("missing_transaction_key")
        try:
            Fernet(transaction_key.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            raise AuthConfigurationError("invalid_transaction_key") from exc

        transaction_db_path = value("SARA_AUTH_TRANSACTION_DB") or str(
            Path(".data") / "sara_auth_transactions.sqlite3"
        )
        ttl_seconds = _bounded_integer(
            value("SARA_AUTH_TRANSACTION_TTL_SECONDS"),
            default=300,
            minimum=60,
            maximum=600,
            label="transaction_ttl",
        )
        max_pending = _bounded_integer(
            value("SARA_AUTH_TRANSACTION_MAX_PENDING"),
            default=1_000,
            minimum=10,
            maximum=100_000,
            label="transaction_limit",
        )
        manifest = value("SARA_LEGACY_NAMESPACE_MANIFEST") or None
        return cls(
            portal_url=_validate_base_url(portal_url, "portal"),
            identity_url=_validate_base_url(identity_url, "identity"),
            callback_url=_validate_callback_url(callback_url),
            transaction_db_path=transaction_db_path,
            transaction_key=transaction_key,
            transaction_ttl_seconds=ttl_seconds,
            transaction_max_pending=max_pending,
            legacy_namespace_manifest=manifest,
        )


@dataclass(frozen=True, slots=True)
class TokenGrant:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in: int
    refresh_expires_in: int


@dataclass(frozen=True, slots=True)
class AuthSession:
    grant: TokenGrant
    profile: dict[str, Any] = field(repr=False)
    subject: str = field(repr=False)


class IdentityClientProtocol(Protocol):
    def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        callback_url: str,
        device_id: str,
    ) -> TokenGrant: ...

    def profile(self, access_token: str) -> dict[str, Any]: ...

    def refresh(self, refresh_token: str) -> TokenGrant: ...

    def logout(self, *, refresh_token: str, access_token: str) -> None: ...


class IdentityClient:
    """Small fail-closed HTTP client; it never follows redirects with tokens."""

    def __init__(
        self,
        config: AuthConfig,
        *,
        http: requests.Session | None = None,
    ) -> None:
        self._config = config
        self._http = http or requests.Session()

    def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        callback_url: str,
        device_id: str,
    ) -> TokenGrant:
        response = self._request(
            "POST",
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": callback_url,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={
                "Accept": "application/json",
                "X-Device-ID": device_id,
                "X-Device-Name": DEVICE_NAME,
            },
        )
        return _parse_grant(response)

    def profile(self, access_token: str) -> dict[str, Any]:
        response = self._request(
            "GET",
            "/auth/introspect",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {access_token}",
                "X-Resource-Audience": RESOURCE_AUDIENCE,
            },
        )
        if not isinstance(response, dict):
            raise InvalidIdentityResponse("invalid_profile")
        central_subject(response)
        return dict(response)

    def refresh(self, refresh_token: str) -> TokenGrant:
        response = self._request(
            "POST",
            "/auth/refresh",
            json={"refresh_token": refresh_token},
            headers={"Accept": "application/json"},
        )
        return _parse_grant(response)

    def logout(self, *, refresh_token: str, access_token: str) -> None:
        if refresh_token:
            self._request(
                "POST",
                "/auth/logout/refresh",
                json={"refresh_token": refresh_token},
                headers={"Accept": "application/json"},
                expect_json=False,
            )
            return
        if access_token:
            self._request(
                "POST",
                "/auth/logout",
                json={},
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {access_token}",
                },
                expect_json=False,
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        expect_json: bool = True,
        **kwargs: Any,
    ) -> Any:
        try:
            response = self._http.request(
                method,
                f"{self._config.identity_url}{path}",
                timeout=HTTP_TIMEOUT,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise IdentityUnavailable("identity_unavailable") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise IdentityRejected(response.status_code)
        if not expect_json:
            return None
        try:
            return response.json()
        except (TypeError, ValueError) as exc:
            raise InvalidIdentityResponse("invalid_json") from exc


def authorization_url(
    state: MutableMapping[str, Any],
    config: AuthConfig,
    store: AuthorizationTransactionStore,
    *,
    now: float | None = None,
) -> str:
    """Return the portal URL, reusing only a pending server-side transaction."""

    current_time = time.time() if now is None else now
    transaction = state.get(_AUTH_TRANSACTION_KEY)
    locally_valid = _valid_transaction(
        transaction,
        current_time,
        config.transaction_ttl_seconds,
    )
    if locally_valid:
        locally_valid = store.is_pending(
            state=transaction["state"],
            callback_url=config.callback_url,
            now=current_time,
        )
    if not locally_valid:
        previous_state = transaction.get("state") if isinstance(transaction, dict) else None
        if isinstance(previous_state, str) and _STATE_PATTERN.fullmatch(previous_state):
            try:
                store.invalidate(state=previous_state, now=current_time)
            except TransactionRejected:
                # A new transaction still must not be created while the store is
                # unavailable, so the following create call remains fail closed.
                pass
        raw_state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _pkce_challenge(verifier)
        store.create(
            state=raw_state,
            verifier=verifier,
            callback_url=config.callback_url,
            now=current_time,
        )
        transaction = {
            "state": raw_state,
            "challenge": challenge,
            "created_at": current_time,
        }
        state[_AUTH_TRANSACTION_KEY] = transaction

    query = urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": config.callback_url,
            "code_challenge": transaction["challenge"],
            "code_challenge_method": "S256",
            "state": transaction["state"],
        }
    )
    return f"{config.portal_url}/authorize?{query}"


def complete_callback(
    state: MutableMapping[str, Any],
    config: AuthConfig,
    client: IdentityClientProtocol,
    store: AuthorizationTransactionStore,
    *,
    returned_state: str,
    code: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Consume one callback exactly once and establish a verified local session."""

    current_time = time.time() if now is None else now
    transaction = state.pop(_AUTH_TRANSACTION_KEY, None)
    if (
        not isinstance(returned_state, str)
        or _STATE_PATTERN.fullmatch(returned_state) is None
    ):
        _wipe_session(state)
        raise CallbackRejected("callback_rejected")

    try:
        verifier = store.consume(
            state=returned_state,
            callback_url=config.callback_url,
            now=current_time,
        )
    except TransactionRejected as exc:
        _invalidate_local_transaction(store, transaction, current_time)
        _wipe_session(state)
        raise CallbackRejected("callback_rejected") from exc

    # A callback commonly arrives in a fresh Streamlit browser session.  When a
    # local transaction is present it is an additional binding, never a fallback
    # for the authoritative transaction consumed above.  Even a mismatch has now
    # burned the presented state and cannot be replayed from a fresh session.
    if transaction is not None and (
        not _valid_transaction(
            transaction,
            current_time,
            config.transaction_ttl_seconds,
        )
        or not hmac.compare_digest(transaction["state"], returned_state)
    ):
        _invalidate_local_transaction(store, transaction, current_time)
        _wipe_session(state)
        raise CallbackRejected("callback_rejected")
    if not isinstance(code, str) or _CODE_PATTERN.fullmatch(code) is None:
        _wipe_session(state)
        raise CallbackRejected("callback_rejected")

    device_id = _device_id(state)
    grant: TokenGrant | None = None
    try:
        grant = client.exchange_code(
            code=code,
            verifier=verifier,
            callback_url=config.callback_url,
            device_id=device_id,
        )
        profile = client.profile(grant.access_token)
        subject = central_subject(profile)
    except AuthError:
        # Once the code has been exchanged, auth_services owns a live refresh
        # family even if profile verification fails. Revoke it best-effort so a
        # rejected callback cannot leave an orphaned central session behind.
        if grant is not None:
            try:
                client.logout(
                    refresh_token=grant.refresh_token,
                    access_token=grant.access_token,
                )
            except AuthError:
                pass
        _wipe_session(state)
        raise

    # A successful account transition must not retain editor/widget state from a
    # previous account on the same browser connection.
    _wipe_session(state, preserve_device_id=device_id)
    state[_AUTH_SESSION_KEY] = AuthSession(
        grant=grant,
        profile=dict(profile),
        subject=subject,
    )
    return dict(profile)


def current_profile(
    state: MutableMapping[str, Any],
    client: IdentityClientProtocol,
) -> dict[str, Any] | None:
    """Introspect the product token, rotate once on 401, and fail closed."""

    session = state.get(_AUTH_SESSION_KEY)
    if not isinstance(session, AuthSession):
        state.pop(_AUTH_SESSION_KEY, None)
        return None

    try:
        profile = client.profile(session.grant.access_token)
        if central_subject(profile) != session.subject:
            raise InvalidIdentityResponse("subject_changed")
    except IdentityRejected as exc:
        if exc.status_code != 401:
            _wipe_session(state)
            return None
        # Remove the old credentials before rotation. A transport failure after
        # this point is ambiguous and the old refresh token is never retried.
        state.pop(_AUTH_SESSION_KEY, None)
        try:
            grant = client.refresh(session.grant.refresh_token)
            if hmac.compare_digest(
                grant.refresh_token,
                session.grant.refresh_token,
            ):
                raise InvalidIdentityResponse("refresh_not_rotated")
            profile = client.profile(grant.access_token)
            if central_subject(profile) != session.subject:
                raise InvalidIdentityResponse("subject_changed")
        except AuthError:
            _wipe_session(state)
            return None
        state[_AUTH_SESSION_KEY] = AuthSession(
            grant=grant,
            profile=dict(profile),
            subject=session.subject,
        )
        return dict(profile)
    except AuthError:
        _wipe_session(state)
        return None

    state[_AUTH_SESSION_KEY] = AuthSession(
        grant=session.grant,
        profile=dict(profile),
        subject=session.subject,
    )
    return dict(profile)


def logout_session(
    state: MutableMapping[str, Any],
    client: IdentityClientProtocol,
    store: AuthorizationTransactionStore | None = None,
) -> bool:
    """Clear all local state first, then make one best-effort remote revoke."""

    session = state.get(_AUTH_SESSION_KEY)
    transaction = state.get(_AUTH_TRANSACTION_KEY)
    device_id = state.get(_DEVICE_ID_KEY)
    _wipe_session(state, preserve_device_id=device_id if isinstance(device_id, str) else None)
    transaction_invalidated = True
    pending_state = transaction.get("state") if isinstance(transaction, dict) else None
    if store is not None and isinstance(pending_state, str):
        try:
            store.invalidate(state=pending_state)
        except TransactionRejected:
            transaction_invalidated = False
    if not isinstance(session, AuthSession):
        return transaction_invalidated
    try:
        client.logout(
            refresh_token=session.grant.refresh_token,
            access_token=session.grant.access_token,
        )
        return transaction_invalidated
    except AuthError:
        return False


def clear_local_session(
    state: MutableMapping[str, Any],
    store: AuthorizationTransactionStore | None = None,
) -> None:
    transaction = state.get(_AUTH_TRANSACTION_KEY)
    pending_state = transaction.get("state") if isinstance(transaction, dict) else None
    _wipe_session(state)
    if store is not None and isinstance(pending_state, str):
        try:
            store.invalidate(state=pending_state)
        except TransactionRejected:
            pass


def central_subject(profile: Mapping[str, Any]) -> str:
    raw = profile.get("id")
    try:
        subject = uuid.UUID(str(raw))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidIdentityResponse("invalid_central_subject") from exc
    return str(subject)


def load_namespace_manifest(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NamespaceManifestError("manifest_unavailable") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise NamespaceManifestError("manifest_version_invalid")
    mappings = payload.get("mappings")
    if not isinstance(mappings, dict):
        raise NamespaceManifestError("manifest_mappings_invalid")

    validated: dict[str, str] = {}
    claimed_folders: set[str] = set()
    for raw_subject, raw_folder in mappings.items():
        try:
            subject = str(uuid.UUID(str(raw_subject)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise NamespaceManifestError("manifest_subject_invalid") from exc
        if not isinstance(raw_folder, str) or not _safe_legacy_folder(raw_folder):
            raise NamespaceManifestError("manifest_folder_invalid")
        if subject in validated or raw_folder in claimed_folders:
            raise NamespaceManifestError("manifest_mapping_duplicated")
        validated[subject] = raw_folder
        claimed_folders.add(raw_folder)
    return validated


def namespace_for_profile(
    profile: Mapping[str, Any],
    manifest: Mapping[str, str] | None = None,
) -> str:
    subject = central_subject(profile)
    if not manifest:
        return subject
    folder = manifest.get(subject)
    if folder is None:
        return subject
    if not _safe_legacy_folder(folder):
        raise NamespaceManifestError("manifest_folder_invalid")
    return folder


def _parse_grant(payload: Any) -> TokenGrant:
    if not isinstance(payload, dict):
        raise InvalidIdentityResponse("invalid_grant")
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    refresh_expires_in = payload.get("refresh_expires_in")
    if (
        not isinstance(access_token, str)
        or len(access_token) < 20
        or not isinstance(refresh_token, str)
        or len(refresh_token) < 40
        or not isinstance(expires_in, int)
        or isinstance(expires_in, bool)
        or expires_in <= 0
        or not isinstance(refresh_expires_in, int)
        or isinstance(refresh_expires_in, bool)
        or refresh_expires_in <= 0
        or payload.get("client_id") != CLIENT_ID
    ):
        raise InvalidIdentityResponse("invalid_grant")
    return TokenGrant(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


def _valid_transaction(transaction: Any, now: float, ttl_seconds: int) -> bool:
    if not isinstance(transaction, dict):
        return False
    state = transaction.get("state")
    challenge = transaction.get("challenge")
    created_at = transaction.get("created_at")
    return (
        isinstance(state, str)
        and _STATE_PATTERN.fullmatch(state) is not None
        and isinstance(challenge, str)
        and _CHALLENGE_PATTERN.fullmatch(challenge) is not None
        and isinstance(created_at, (int, float))
        and not isinstance(created_at, bool)
        and 0 <= now - float(created_at) <= ttl_seconds
    )


def _hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("ascii")).hexdigest()


def _invalidate_local_transaction(
    store: AuthorizationTransactionStore,
    transaction: Any,
    now: float,
) -> None:
    expected_state = transaction.get("state") if isinstance(transaction, dict) else None
    if isinstance(expected_state, str):
        try:
            store.invalidate(state=expected_state, now=now)
        except TransactionRejected:
            pass


def _valid_timestamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TransactionRejected("authorization_transaction_rejected")
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise TransactionRejected("authorization_transaction_rejected") from exc
    if numeric < 0 or not math.isfinite(numeric):
        raise TransactionRejected("authorization_transaction_rejected")
    return numeric


def _bounded_integer(
    raw: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise AuthConfigurationError(f"invalid_{label}") from exc
    if not minimum <= value <= maximum:
        raise AuthConfigurationError(f"invalid_{label}")
    return value


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _device_id(state: MutableMapping[str, Any]) -> str:
    existing = state.get(_DEVICE_ID_KEY)
    if isinstance(existing, str) and _STATE_PATTERN.fullmatch(existing):
        return existing
    generated = secrets.token_urlsafe(24)
    state[_DEVICE_ID_KEY] = generated
    return generated


def _wipe_session(
    state: MutableMapping[str, Any],
    *,
    preserve_device_id: str | None = None,
) -> None:
    state.clear()
    if preserve_device_id and _STATE_PATTERN.fullmatch(preserve_device_id):
        state[_DEVICE_ID_KEY] = preserve_device_id


def _safe_legacy_folder(value: str) -> bool:
    return (
        value not in {".", ".."}
        and _LEGACY_FOLDER_PATTERN.fullmatch(value) is not None
        and "/" not in value
        and "\\" not in value
    )


def _validate_base_url(value: str, label: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AuthConfigurationError(f"invalid_{label}_url")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_callback_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise AuthConfigurationError("invalid_callback_url")
    return value


__all__ = [
    "CLIENT_ID",
    "RESOURCE_AUDIENCE",
    "AuthConfig",
    "AuthConfigurationError",
    "AuthError",
    "AuthSession",
    "AuthorizationTransactionStore",
    "CallbackRejected",
    "IdentityClient",
    "IdentityRejected",
    "IdentityUnavailable",
    "InvalidIdentityResponse",
    "NamespaceManifestError",
    "TokenGrant",
    "TransactionRejected",
    "TransactionStoreFull",
    "authorization_url",
    "central_subject",
    "clear_local_session",
    "complete_callback",
    "current_profile",
    "load_namespace_manifest",
    "logout_session",
    "namespace_for_profile",
]
