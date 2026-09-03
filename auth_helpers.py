"""Streamlit integration for the server-side SARA PKCE state machine."""

from __future__ import annotations

import logging
import os
from typing import Any

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from sara_auth import (
    AuthConfig,
    AuthConfigurationError,
    AuthError,
    AuthorizationTransactionStore,
    CallbackRejected,
    IdentityClient,
    authorization_url,
    clear_local_session,
    complete_callback,
    current_profile,
    load_namespace_manifest,
    logout_session,
    namespace_for_profile,
)

logger = logging.getLogger("synapse.auth")


def is_user_logged_in() -> bool:
    return get_current_user_info() is not None


def get_current_user_info() -> dict[str, Any] | None:
    try:
        config = _configuration()
    except AuthConfigurationError:
        clear_local_session(st.session_state)
        return None
    return current_profile(st.session_state, IdentityClient(config))


def get_user_namespace(user_info: dict[str, Any]) -> str:
    config = _configuration()
    manifest = load_namespace_manifest(config.legacy_namespace_manifest)
    return namespace_for_profile(user_info, manifest)


def logout() -> None:
    """Revoke remotely when possible; local state is always erased first."""

    try:
        config = _configuration()
    except AuthConfigurationError:
        clear_local_session(st.session_state)
        logger.warning("sara_logout_local_only_configuration_invalid")
        return
    try:
        store = AuthorizationTransactionStore.from_config(config)
    except AuthError:
        store = None
    remote_revoked = logout_session(
        st.session_state,
        IdentityClient(config),
        store,
    )
    logger.info("sara_logout_completed" if remote_revoked else "sara_logout_local_only")


def display_auth_status_sidebar(
    app_name: str = "SynapseMD",
    *,
    user_info: dict[str, Any] | None = None,
) -> None:
    user_info = user_info or get_current_user_info()
    with st.sidebar:
        st.markdown(f"### {app_name}")
        st.divider()
        if user_info:
            display_name = user_info.get("name") or user_info.get("email") or "Cuenta SARA"
            st.success(f"Conectado como: **{display_name}**")
            email = user_info.get("email")
            if email:
                st.caption(str(email))
            if st.button(
                "🚪 Cerrar sesión",
                key="sidebar_logout_button",
                type="secondary",
                use_container_width=True,
            ):
                logout()
                st.rerun()
        else:
            st.warning("No has iniciado sesión.")


def ensure_authenticated(
    login_title: str = "Bienvenido",
    login_message: str = "Por favor, inicia sesión para continuar.",
    login_button_text: str = "Iniciar sesión con SARA",
) -> dict[str, Any]:
    try:
        config = _configuration()
    except AuthConfigurationError:
        clear_local_session(st.session_state)
        logger.error("sara_auth_configuration_invalid")
        st.error("La autenticación central no está configurada correctamente.")
        st.stop()

    client = IdentityClient(config)
    if _has_callback_parameters():
        code = _single_query_value("code")
        returned_state = _single_query_value("state")
        provider_error = _single_query_value("error")
        _clear_query_parameters()
        try:
            store = AuthorizationTransactionStore.from_config(config)
        except AuthError:
            clear_local_session(st.session_state)
            logger.error("sara_authorization_transaction_failed")
            st.error("No se pudo validar el inicio de sesión. Intenta de nuevo.")
            st.stop()
        if provider_error or not code or not returned_state:
            if returned_state:
                try:
                    store.invalidate(state=returned_state)
                except AuthError:
                    pass
            clear_local_session(st.session_state, store)
            logger.warning("sara_callback_rejected")
            st.error("No se pudo validar el inicio de sesión. Intenta de nuevo.")
            st.stop()
        try:
            complete_callback(
                st.session_state,
                config,
                client,
                store,
                returned_state=returned_state,
                code=code,
            )
        except (CallbackRejected, AuthError):
            logger.warning("sara_callback_rejected")
            st.error("No se pudo validar el inicio de sesión. Intenta de nuevo.")
            st.stop()
        logger.info("sara_login_completed")
        st.rerun()

    user_info = current_profile(st.session_state, client)
    if user_info is not None:
        return user_info

    st.title(login_title)
    st.subheader(login_message)
    try:
        store = AuthorizationTransactionStore.from_config(config)
        login_url = authorization_url(st.session_state, config, store)
    except AuthError:
        logger.error("sara_authorization_transaction_failed")
        st.error("No se pudo preparar el inicio de sesión.")
        st.stop()
    st.link_button(login_button_text, login_url, type="primary")
    st.caption("La autenticación se completa en el portal central de SARA.")
    st.stop()


def _configuration() -> AuthConfig:
    try:
        configured = st.secrets.to_dict()
    except StreamlitSecretNotFoundError:
        configured = {}
    values = {
        key: configured[key]
        for key in (
            "SARA_AUTH_PORTAL_URL",
            "SARA_IDENTITY_URL",
            "SARA_AUTH_CALLBACK_URL",
            "SARA_AUTH_TRANSACTION_DB",
            "SARA_AUTH_TRANSACTION_KEY",
            "SARA_AUTH_TRANSACTION_TTL_SECONDS",
            "SARA_AUTH_TRANSACTION_MAX_PENDING",
            "SARA_LEGACY_NAMESPACE_MANIFEST",
        )
        if key in configured
    }
    return AuthConfig.from_mapping(values, environment=os.environ)


def _has_callback_parameters() -> bool:
    return any(_query_values(name) for name in ("code", "state", "error"))


def _single_query_value(name: str) -> str | None:
    values = _query_values(name)
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    return values[0]


def _query_values(name: str) -> list[str]:
    try:
        return list(st.query_params.get_all(name))
    except (AttributeError, TypeError):
        raw = st.query_params.get(name)
        if raw is None:
            return []
        return [str(item) for item in raw] if isinstance(raw, list) else [str(raw)]


def _clear_query_parameters() -> None:
    try:
        st.query_params.clear()
    except AttributeError:
        st.experimental_set_query_params()


__all__ = [
    "display_auth_status_sidebar",
    "ensure_authenticated",
    "get_current_user_info",
    "get_user_namespace",
    "is_user_logged_in",
    "logout",
]
