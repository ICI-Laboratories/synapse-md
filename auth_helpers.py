# D:/synapse-md/synapse_md_app/auth_helpers.py
import streamlit as st
import time
import logging

# Configurar logging básico
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions using st.experimental_user ---

def is_user_logged_in() -> bool:
    """Comprueba si el usuario ha iniciado sesión usando st.experimental_user."""
    logged_in = False
    try:
        # Acceder a st.experimental_user puede lanzar una excepción si no está configurado
        logged_in = st.experimental_user.is_logged_in
    except Exception as e:
        logging.warning(f"SynapseMD: Could not access st.experimental_user: {e}. Assuming not logged in.")
        st.warning("Authentication configuration might be missing or invalid. Please check `.streamlit/config.toml`.", icon="⚙️")
    return logged_in

def get_current_user_info() -> dict | None:
    """
    Recupera la información del usuario de st.experimental_user.
    Devuelve un diccionario con 'email', 'name'. Puede devolver None si no está logueado.
    """
    if is_user_logged_in():
        user = st.experimental_user
        # st.experimental_user tiene atributos directamente (no es un dict)
        return {
            'email': getattr(user, 'email', None),
            'name': getattr(user, 'name', None),
            # 'sub': podrías necesitar obtenerlo de otra forma si es requerido,
            # st.experimental_user no lo expone directamente de forma estándar.
            # Podría estar en user.to_dict() si el proveedor lo incluye.
            # 'picture': Tampoco está garantizado.
        }
    return None

def logout():
    """Cierra la sesión del usuario usando st.logout() y reejecuta."""
    logging.info("SynapseMD: Logout initiated using st.logout().")
    st.logout() # Llama a la función incorporada de Streamlit
    # st.logout() maneja la redirección y limpieza, no se necesita rerun manual inmediato.

def display_auth_status_sidebar(app_name="SynapseMD"):
    """Muestra el estado de inicio de sesión y el botón de cierre de sesión en la barra lateral."""
    user_info = get_current_user_info() # Usa la nueva función helper
    with st.sidebar:
        st.markdown(f"### {app_name}") # Usa app name
        st.divider()
        if user_info:
            # Determinar mejor nombre para mostrar
            display_name = user_info.get('name') or user_info.get('email', 'Usuario Desconocido')

            st.success(f"Conectado como: **{display_name}**")

            email = user_info.get('email')
            if email: st.caption(f"{email}")

            # st.experimental_user no garantiza 'picture'
            # picture = user_info.get('picture')
            # if picture: st.image(picture, width=50, caption="Profile Picture")

            if st.button("🚪 Cerrar sesión", key="sidebar_logout_button", type="secondary", use_container_width=True):
                logout() # Llama a la nueva función logout
        else:
            # Esto no debería mostrarse si el flujo principal funciona
            st.warning("No has iniciado sesión.")

# --- Función de Autenticación Simplificada ---
def ensure_authenticated(
    login_title="Bienvenido",
    login_message="Por favor, inicia sesión para continuar.",
    login_button_text="Iniciar sesión con Google"
    ) -> dict | None:
    """
    Verifica si el usuario está autenticado. Si no, muestra la pantalla de login.
    Devuelve la información del usuario si está autenticado, o detiene la ejecución.
    """
    if not is_user_logged_in():
        logging.info("SynapseMD: User not logged in. Displaying login screen using st.login().")
        st.title(login_title)
        st.subheader(login_message)

        # Botón que llama a st.login()
        if st.button(login_button_text, type="primary"):
            st.login() # Inicia el flujo de autenticación incorporado

        st.stop() # Detiene la ejecución hasta que el usuario inicie sesión
    else:
        # Si ya está logueado, devuelve la información
        user_info = get_current_user_info()
        # Logging menos frecuente para usuarios ya logueados
        # logging.info(f"SynapseMD: User already logged in: {user_info.get('email')}")
        return user_info