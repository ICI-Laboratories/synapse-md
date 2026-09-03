from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet

from sara_auth import (
    AuthConfig,
    AuthError,
    AuthorizationTransactionStore,
    CallbackRejected,
    IdentityClient,
    IdentityRejected,
    IdentityUnavailable,
    NamespaceManifestError,
    TokenGrant,
    TransactionRejected,
    TransactionStoreFull,
    authorization_url,
    complete_callback,
    current_profile,
    load_namespace_manifest,
    logout_session,
    namespace_for_profile,
)

SUBJECT = "12345678-1234-4234-9234-1234567890ab"
OTHER_SUBJECT = "22345678-1234-4234-9234-1234567890ab"
CODE = "one-time-code-abcdefghijklmnop"


def _grant(label: str) -> TokenGrant:
    return TokenGrant(
        access_token=f"access-{label}-" + "a" * 40,
        refresh_token=f"refresh-{label}-" + "r" * 48,
        expires_in=900,
        refresh_expires_in=2_592_000,
    )


def _profile(subject: str = SUBJECT) -> dict[str, Any]:
    return {
        "id": subject,
        "email": "synthetic@example.invalid",
        "name": "Synthetic User",
    }


def _config(tmp_path, *, ttl: int = 300, limit: int = 1_000) -> AuthConfig:
    return AuthConfig.from_mapping(
        {
            "SARA_AUTH_PORTAL_URL": "https://identity.example/portal",
            "SARA_IDENTITY_URL": "https://identity-api.example",
            "SARA_AUTH_CALLBACK_URL": "https://synapse.example/auth/callback",
            "SARA_AUTH_TRANSACTION_DB": str(tmp_path / "transactions.sqlite3"),
            "SARA_AUTH_TRANSACTION_KEY": Fernet.generate_key().decode("ascii"),
            "SARA_AUTH_TRANSACTION_TTL_SECONDS": str(ttl),
            "SARA_AUTH_TRANSACTION_MAX_PENDING": str(limit),
            # Deliberately ignored: product audience is a compile-time contract,
            # not configuration or caller-controlled input.
            "SARA_RESOURCE_AUDIENCE": "attacker-controlled",
        }
    )


class FakeIdentityClient:
    def __init__(
        self,
        *,
        grant: TokenGrant | None = None,
        profiles: Iterator[dict[str, Any] | AuthError] | None = None,
        refreshed_grant: TokenGrant | AuthError | None = None,
        logout_error: AuthError | None = None,
    ) -> None:
        self.grant = grant or _grant("initial")
        self.profiles = profiles or iter([_profile()])
        self.refreshed_grant = refreshed_grant or _grant("rotated")
        self.logout_error = logout_error
        self.exchange_calls: list[dict[str, str]] = []
        self.profile_calls: list[str] = []
        self.refresh_calls: list[str] = []
        self.logout_calls: list[tuple[str, str]] = []

    def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        callback_url: str,
        device_id: str,
    ) -> TokenGrant:
        self.exchange_calls.append(
            {
                "code": code,
                "verifier": verifier,
                "callback_url": callback_url,
                "device_id": device_id,
            }
        )
        return self.grant

    def profile(self, access_token: str) -> dict[str, Any]:
        self.profile_calls.append(access_token)
        result = next(self.profiles)
        if isinstance(result, AuthError):
            raise result
        return result

    def refresh(self, refresh_token: str) -> TokenGrant:
        self.refresh_calls.append(refresh_token)
        if isinstance(self.refreshed_grant, AuthError):
            raise self.refreshed_grant
        return self.refreshed_grant

    def logout(self, *, refresh_token: str, access_token: str) -> None:
        self.logout_calls.append((refresh_token, access_token))
        if self.logout_error:
            raise self.logout_error


class FakeResponse:
    def __init__(self, payload: Any = None, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload


class RecordingHttp:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def _begin(
    state: dict[str, Any],
    config: AuthConfig,
    store: AuthorizationTransactionStore,
    *,
    now: float = 100.0,
) -> tuple[str, str]:
    url = authorization_url(state, config, store, now=now)
    query = parse_qs(urlsplit(url).query)
    return query["state"][0], url


def _grant_payload(label: str) -> dict[str, Any]:
    grant = _grant(label)
    return {
        "access_token": grant.access_token,
        "refresh_token": grant.refresh_token,
        "expires_in": grant.expires_in,
        "refresh_expires_in": grant.refresh_expires_in,
        "client_id": "synapse-web",
    }


def test_identity_http_contract_uses_exact_routes_form_and_no_redirects(tmp_path) -> None:
    config = _config(tmp_path)
    http = RecordingHttp(
        [
            FakeResponse(_grant_payload("exchange")),
            FakeResponse(_profile()),
            FakeResponse(_grant_payload("refresh")),
            FakeResponse(),
        ]
    )
    client = IdentityClient(config, http=http)

    exchanged = client.exchange_code(
        code=CODE,
        verifier="v" * 64,
        callback_url=config.callback_url,
        device_id="device-" + "d" * 32,
    )
    assert client.profile(exchanged.access_token) == _profile()
    rotated = client.refresh(exchanged.refresh_token)
    client.logout(
        refresh_token=rotated.refresh_token,
        access_token=rotated.access_token,
    )

    exchange = http.calls[0]
    assert exchange[:2] == ("POST", "https://identity-api.example/oauth/token")
    assert exchange[2]["data"] == {
        "grant_type": "authorization_code",
        "code": CODE,
        "redirect_uri": config.callback_url,
        "client_id": "synapse-web",
        "code_verifier": "v" * 64,
    }
    assert exchange[2]["allow_redirects"] is False
    assert exchange[2]["timeout"] == (3.05, 8.0)
    assert [call[1] for call in http.calls[1:]] == [
        "https://identity-api.example/auth/introspect",
        "https://identity-api.example/auth/refresh",
        "https://identity-api.example/auth/logout/refresh",
    ]
    assert http.calls[1][2]["headers"]["Authorization"].startswith("Bearer ")
    assert http.calls[1][2]["headers"]["X-Resource-Audience"] == "synapse"
    assert http.calls[2][2]["json"] == {"refresh_token": exchanged.refresh_token}
    assert http.calls[3][2]["json"] == {"refresh_token": rotated.refresh_token}


def test_authorization_url_keeps_verifier_only_encrypted_server_side(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    state: dict[str, Any] = {}
    returned_state, url = _begin(state, config, store)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    transaction = state["_sara_auth_transaction"]

    assert parsed.path == "/portal/authorize"
    assert query["client_id"] == ["synapse-web"]
    assert query["redirect_uri"] == ["https://synapse.example/auth/callback"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == [returned_state]
    assert transaction["state"] == returned_state
    assert "verifier" not in transaction
    assert query["code_challenge"] == [transaction["challenge"]]
    assert authorization_url(state, config, store, now=101.0) == url

    with sqlite3.connect(config.transaction_db_path) as connection:
        row = connection.execute(
            """
            SELECT state_hash, verifier_ciphertext, client_id, callback_url,
                   created_at, expires_at, consumed_at
            FROM oauth_transactions
            """
        ).fetchone()
    assert row is not None
    assert row[0] == hashlib.sha256(returned_state.encode("ascii")).hexdigest()
    assert returned_state not in row[0]
    assert row[2:4] == ("synapse-web", config.callback_url)
    assert row[4:7] == (100.0, 400.0, None)
    assert returned_state.encode("ascii") not in bytes(row[1])


def test_store_never_persists_raw_state_or_verifier(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    raw_state = "state-" + "s" * 40
    verifier = "v" * 64

    store.create(
        state=raw_state,
        verifier=verifier,
        callback_url=config.callback_url,
        now=100.0,
    )

    with sqlite3.connect(config.transaction_db_path) as connection:
        state_hash, ciphertext = connection.execute(
            "SELECT state_hash, verifier_ciphertext FROM oauth_transactions"
        ).fetchone()
    assert state_hash != raw_state
    assert verifier.encode("ascii") not in bytes(ciphertext)
    assert CODE.encode("ascii") not in bytes(ciphertext)
    assert store.consume(
        state=raw_state,
        callback_url=config.callback_url,
        now=101.0,
    ) == verifier


def test_callback_works_in_fresh_streamlit_session_and_never_persists_code(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    originating_state: dict[str, Any] = {"old_editor_content": "must remain here"}
    returned_state, _ = _begin(originating_state, config, store)
    callback_state: dict[str, Any] = {"new_tab_widget": "must be cleared"}
    client = FakeIdentityClient()

    profile = complete_callback(
        callback_state,
        config,
        client,
        store,
        returned_state=returned_state,
        code=CODE,
        now=101.0,
    )

    assert profile["id"] == SUBJECT
    assert len(client.exchange_calls) == 1
    assert client.exchange_calls[0]["callback_url"] == config.callback_url
    assert CODE not in repr(callback_state)
    assert "_sara_auth_transaction" not in callback_state
    assert "new_tab_widget" not in callback_state


@pytest.mark.parametrize(
    "logout_error",
    [None, IdentityUnavailable("logout_unavailable")],
)
def test_callback_revokes_exchanged_grant_when_profile_verification_fails(
    tmp_path,
    logout_error,
) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    returned_state, _ = _begin({}, config, store)
    grant = _grant("orphan-prevention")
    state: dict[str, Any] = {"private_widget": "must be cleared"}
    client = FakeIdentityClient(
        grant=grant,
        profiles=iter([IdentityUnavailable("profile_unavailable")]),
        logout_error=logout_error,
    )

    with pytest.raises(IdentityUnavailable, match="profile_unavailable"):
        complete_callback(
            state,
            config,
            client,
            store,
            returned_state=returned_state,
            code=CODE,
            now=101.0,
        )

    assert client.logout_calls == [(grant.refresh_token, grant.access_token)]
    assert state == {}


def test_wrong_state_invalidates_local_attempt_and_replay_never_exchanges_twice(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    state: dict[str, Any] = {"draft_content": "private"}
    returned_state, _ = _begin(state, config, store)
    client = FakeIdentityClient(profiles=iter([_profile()]))

    with pytest.raises(CallbackRejected):
        complete_callback(
            state,
            config,
            client,
            store,
            returned_state="different-state-value-that-is-long-enough",
            code=CODE,
            now=101.0,
        )
    assert client.exchange_calls == []
    assert state == {}
    with pytest.raises(CallbackRejected):
        complete_callback(
            {},
            config,
            client,
            store,
            returned_state=returned_state,
            code=CODE,
            now=102.0,
        )

    returned_state, _ = _begin(state, config, store, now=103.0)
    complete_callback(
        {},
        config,
        client,
        store,
        returned_state=returned_state,
        code=CODE,
        now=104.0,
    )
    with pytest.raises(CallbackRejected):
        complete_callback(
            {},
            config,
            client,
            store,
            returned_state=returned_state,
            code=CODE,
            now=105.0,
        )
    assert len(client.exchange_calls) == 1


def test_malformed_code_consumes_state_before_rejection(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    returned_state, _ = _begin({}, config, store)
    client = FakeIdentityClient()

    with pytest.raises(CallbackRejected):
        complete_callback(
            {},
            config,
            client,
            store,
            returned_state=returned_state,
            code="short",
            now=101.0,
        )
    with pytest.raises(CallbackRejected):
        complete_callback(
            {},
            config,
            client,
            store,
            returned_state=returned_state,
            code=CODE,
            now=102.0,
        )
    assert client.exchange_calls == []


def test_expired_transaction_is_rejected_before_exchange(tmp_path) -> None:
    config = _config(tmp_path, ttl=60)
    store = AuthorizationTransactionStore.from_config(config)
    returned_state, _ = _begin({}, config, store, now=100.0)
    client = FakeIdentityClient()

    with pytest.raises(CallbackRejected):
        complete_callback(
            {},
            config,
            client,
            store,
            returned_state=returned_state,
            code=CODE,
            now=160.0,
        )
    assert client.exchange_calls == []


def test_transaction_is_bound_to_the_exact_callback(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    raw_state = "state-" + "s" * 40
    store.create(
        state=raw_state,
        verifier="v" * 64,
        callback_url=config.callback_url,
        now=100.0,
    )

    with pytest.raises(TransactionRejected):
        store.consume(
            state=raw_state,
            callback_url=f"{config.callback_url}/different",
            now=101.0,
        )
    assert store.consume(
        state=raw_state,
        callback_url=config.callback_url,
        now=102.0,
    ) == "v" * 64


def test_atomic_consume_allows_exactly_one_concurrent_winner(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    raw_state = "state-" + "s" * 40
    verifier = "v" * 64
    store.create(
        state=raw_state,
        verifier=verifier,
        callback_url=config.callback_url,
        now=100.0,
    )
    barrier = threading.Barrier(3)

    def consume() -> str:
        barrier.wait()
        try:
            return store.consume(
                state=raw_state,
                callback_url=config.callback_url,
                now=101.0,
            )
        except TransactionRejected:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(consume) for _ in range(2)]
        barrier.wait()
        results = [future.result(timeout=10) for future in futures]

    assert sorted(results) == ["rejected", verifier]


def test_pending_limit_and_expiry_cleanup(tmp_path) -> None:
    config = _config(tmp_path, ttl=60, limit=10)
    store = AuthorizationTransactionStore.from_config(config)
    for index in range(10):
        store.create(
            state=f"state-{index:02d}-" + "s" * 32,
            verifier="v" * 64,
            callback_url=config.callback_url,
            now=100.0,
        )
    with pytest.raises(TransactionStoreFull):
        store.create(
            state="state-full-" + "s" * 32,
            verifier="v" * 64,
            callback_url=config.callback_url,
            now=101.0,
        )

    store.create(
        state="state-after-expiry-" + "s" * 32,
        verifier="v" * 64,
        callback_url=config.callback_url,
        now=161.0,
    )
    assert store.is_pending(
        state="state-after-expiry-" + "s" * 32,
        callback_url=config.callback_url,
        now=161.0,
    )


def test_401_rotates_refresh_once_and_revalidates_same_subject(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    old_grant = _grant("old")
    rotated = _grant("new")
    client = FakeIdentityClient(
        grant=old_grant,
        profiles=iter([_profile(), IdentityRejected(401), _profile()]),
        refreshed_grant=rotated,
    )
    state: dict[str, Any] = {}
    returned_state, _ = _begin(state, config, store)
    complete_callback(
        state,
        config,
        client,
        store,
        returned_state=returned_state,
        code=CODE,
        now=101.0,
    )

    assert current_profile(state, client) == _profile()
    assert client.refresh_calls == [old_grant.refresh_token]
    stored = state["_sara_auth_session"]
    assert stored.grant == rotated
    assert old_grant.access_token not in repr(state)
    assert old_grant.refresh_token not in repr(state)
    assert rotated.access_token not in repr(state)


def test_ambiguous_refresh_failure_is_not_retried_and_clears_local_state(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    old_grant = _grant("old")
    client = FakeIdentityClient(
        grant=old_grant,
        profiles=iter([_profile(), IdentityRejected(401)]),
        refreshed_grant=IdentityUnavailable("transient"),
    )
    state: dict[str, Any] = {}
    returned_state, _ = _begin(state, config, store)
    complete_callback(
        state,
        config,
        client,
        store,
        returned_state=returned_state,
        code=CODE,
        now=101.0,
    )

    assert current_profile(state, client) is None
    assert current_profile(state, client) is None
    assert client.refresh_calls == [old_grant.refresh_token]
    assert old_grant.refresh_token not in repr(state)


def test_refresh_must_rotate_the_refresh_token(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    old_grant = _grant("old")
    client = FakeIdentityClient(
        grant=old_grant,
        profiles=iter([_profile(), IdentityRejected(401)]),
        refreshed_grant=old_grant,
    )
    state: dict[str, Any] = {}
    returned_state, _ = _begin(state, config, store)
    complete_callback(
        state,
        config,
        client,
        store,
        returned_state=returned_state,
        code=CODE,
        now=101.0,
    )

    assert current_profile(state, client) is None
    assert client.refresh_calls == [old_grant.refresh_token]
    assert "_sara_auth_session" not in state


def test_refresh_cannot_replace_session_with_another_subject(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    client = FakeIdentityClient(
        profiles=iter([_profile(), IdentityRejected(401), _profile(OTHER_SUBJECT)]),
    )
    state: dict[str, Any] = {}
    returned_state, _ = _begin(state, config, store)
    complete_callback(
        state,
        config,
        client,
        store,
        returned_state=returned_state,
        code=CODE,
        now=101.0,
    )

    assert current_profile(state, client) is None
    assert "_sara_auth_session" not in state


def test_logout_is_remote_best_effort_and_always_locally_fail_closed(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    client = FakeIdentityClient(
        profiles=iter([_profile()]),
        logout_error=IdentityUnavailable("transient"),
    )
    state: dict[str, Any] = {"draft_content": "private"}
    returned_state, _ = _begin(state, config, store)
    complete_callback(
        state,
        config,
        client,
        store,
        returned_state=returned_state,
        code=CODE,
        now=101.0,
    )
    state["draft_content"] = "private"

    assert logout_session(state, client, store) is False
    assert len(client.logout_calls) == 1
    assert "draft_content" not in state
    assert "_sara_auth_session" not in state


def test_logout_invalidates_pending_authorization(tmp_path) -> None:
    config = _config(tmp_path)
    store = AuthorizationTransactionStore.from_config(config)
    state: dict[str, Any] = {}
    returned_state, _ = _begin(state, config, store)

    assert logout_session(state, FakeIdentityClient(), store) is True
    assert state == {}
    with pytest.raises(TransactionRejected):
        store.consume(
            state=returned_state,
            callback_url=config.callback_url,
            now=101.0,
        )


def test_sensitive_dataclass_representations_are_redacted(tmp_path) -> None:
    config = _config(tmp_path)
    grant = _grant("secret")

    assert config.transaction_key not in repr(config)
    assert config.transaction_db_path not in repr(config)
    assert grant.access_token not in repr(grant)
    assert grant.refresh_token not in repr(grant)


def test_namespace_uses_uuid_unless_explicit_manifest_maps_legacy_folder(tmp_path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "mappings": {SUBJECT: "legacy-folder"},
            }
        ),
        encoding="utf-8",
    )
    manifest = load_namespace_manifest(str(path))

    assert namespace_for_profile(_profile(), manifest) == "legacy-folder"
    assert namespace_for_profile(_profile(OTHER_SUBJECT), manifest) == OTHER_SUBJECT
    assert namespace_for_profile(
        {"id": OTHER_SUBJECT, "email": "legacy-folder", "name": "legacy-folder"},
        manifest,
    ) == OTHER_SUBJECT


@pytest.mark.parametrize(
    "mappings",
    [
        {SUBJECT: "../escape"},
        {SUBJECT: "same-folder", OTHER_SUBJECT: "same-folder"},
        {"not-a-uuid": "legacy-folder"},
    ],
)
def test_namespace_manifest_rejects_unsafe_or_ambiguous_mappings(tmp_path, mappings) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"version": 1, "mappings": mappings}), encoding="utf-8")

    with pytest.raises(NamespaceManifestError):
        load_namespace_manifest(str(path))
