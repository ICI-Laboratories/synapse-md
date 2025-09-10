import streamlit as st
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def is_user_logged_in() -> bool:
    logged_in = False
    try:
        logged_in = st.experimental_user.is_logged_in
    except Exception as e:
        logging.warning(
            f"SynapseMD: Could not access st.experimental_user: {e}. "
            "Assuming not logged in."
        )
        st.warning(
            "Authentication configuration might be missing or invalid. "
            "Please check `.streamlit/config.toml`.",
            icon="⚙️"
        )
    return logged_in

def get_current_user_info() -> dict | None:
    if is_user_logged_in():
        user = st.experimental_user
        return {
            'email': getattr(user, 'email', None),
            'name': getattr(user, 'name', None),
        }
    return None

def logout():
    logging.info("SynapseMD: Logout initiated using st.logout().")
    st.logout()

def display_auth_status_sidebar(app_name="SynapseMD"):
    user_info = get_current_user_info()
    with st.sidebar:
        st.markdown(f"### {app_name}")
        st.divider()
        if user_info:
            display_name = (
                user_info.get('name') or
                user_info.get('email', 'Usuario Desconocido')
            )
            st.success(f"Conectado como: **{display_name}**")
            email = user_info.get('email')
            if email:
                st.caption(f"{email}")
            if st.button(
                "🚪 Cerrar sesión",
                key="sidebar_logout_button",
                type="secondary",
                use_container_width=True
            ):
                logout()
        else:
            st.warning("No has iniciado sesión.")

def ensure_authenticated(
    login_title="Bienvenido",
    login_message="Por favor, inicia sesión para continuar.",
    login_button_text="Iniciar sesión con Google"
) -> dict | None:
    if not is_user_logged_in():
        logging.info(
            "SynapseMD: User not logged in. "
            "Displaying login screen using st.login()."
        )
        st.title(login_title)
        st.subheader(login_message)
        if st.button(login_button_text, type="primary"):
            st.login()
        st.stop()
    else:
        user_info = get_current_user_info()
        return user_info