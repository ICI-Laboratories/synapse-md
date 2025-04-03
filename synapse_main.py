# D:/synapse-md/synapse_md_app/synapse_main.py
import streamlit as st
import os
import time
import logging
import re # Importado para limpieza de nombre de archivo

# --- NUEVO Auth Import ---
# Asegúrate de que auth_helpers.py exista en la misma carpeta
try:
    # Importa el nuevo módulo de helpers
    import auth_helpers as auth
except ImportError:
    st.error("FATAL: No se pudo importar 'auth_helpers.py'. Asegúrate de que el archivo exista en la ubicación correcta.")
    st.stop()
# --- FIN NUEVO Auth Import ---


# --- Configuración de Logging Básico ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# --- Fin Configuración Logging ---


# --- NUEVO Authenticate ---
# Esta llamada ahora usa st.login() internamente si es necesario.
# Requiere que la configuración OAuth esté en .streamlit/config.toml
logging.info("SynapseMD: Verificando estado de autenticación...")
user_info = auth.ensure_authenticated(
    login_title="Bienvenido a SynapseMD",
    login_message="Tu asistente médico inteligente para documentos.",
    login_button_text="Iniciar sesión con tu cuenta de Google"
)
# Si el script continúa, user_info contiene {'email': ..., 'name': ...}
logging.info(f"SynapseMD: Usuario autenticado: {user_info.get('email')}")
# --- FIN NUEVO Authenticate ---


# Local Imports (solo necesarios si el usuario está autenticado)
from config import APP_NAME, BASE_STORAGE_PATH, ACTIVE_LLM_BACKEND, validate_google_api_key
import storage # Importar el módulo completo
import context_processing
import editor_features
from llm_interface import get_llm_client # Asegúrate que get_llm_client siga funcionando

# --- Page Config ---
st.set_page_config(page_title=APP_NAME, layout="wide", initial_sidebar_state="expanded")

# --- Initialize Session State (App Specific) ---
# Asegurarse de que todas las claves necesarias estén inicializadas
default_session_state_app = {
    "current_draft_name": None,
    "draft_content": "",
    "selected_context": [],
    "sidebar_suggestions": [],
    "inline_suggestion": "",
    "last_edit_time": time.time(),
    "llm_backend": ACTIVE_LLM_BACKEND,
    "_scheduled_load_draft": None, # Para el selector de borradores
}
for key, default_value in default_session_state_app.items():
    if key not in st.session_state:
        st.session_state[key] = default_value
# --- End Session State Init ---


# --- User Setup (Using info returned by ensure_authenticated) ---
user_email = user_info.get('email')
if not user_email:
    user_name_fallback = user_info.get('name', f'unknown_synapse_user_{int(time.time())}')
    user_email = f"user_{user_name_fallback.replace(' ', '_')}"
    st.warning(f"Email no encontrado para el usuario, usando identificador alternativo: {user_email}", icon="⚠️")
    logging.warning(f"SynapseMD: Email not found for user '{user_name_fallback}', using fallback identifier: {user_email}")

# Sanitize identifier
user_folder_id = "".join(c for c in user_email if c.isalnum() or c in ('_', '-', '@', '.')).rstrip('.').strip() # Limpieza extra
user_folder_id = user_folder_id or "default_synapse_user"
logging.info(f"SynapseMD: User folder identifier set to: {user_folder_id}")

# Creación de carpetas
user_folder = storage.get_user_dir_path(user_folder_id)
try:
    os.makedirs(user_folder, exist_ok=True)
    os.makedirs(os.path.join(user_folder, storage.DRAFTS_FOLDER), exist_ok=True)
    os.makedirs(os.path.join(user_folder, storage.CONTEXT_FOLDER), exist_ok=True)
    logging.info(f"SynapseMD: User folder structure ensured at: {user_folder}")
except OSError as e:
    st.error(f"Error Crítico: No se pudo crear la estructura de directorios del usuario en {user_folder}. Error: {e}")
    logging.error(f"SynapseMD: Failed to create user directory structure at {user_folder}. Error: {e}")
    st.stop()
# --- End User Setup ---


# --- Display Auth Status in Sidebar & Validate LLM Key ---
auth.display_auth_status_sidebar(app_name=APP_NAME) # Llama a la función del helper
# La validación de la clave API de Google (para LLM) sigue siendo relevante
is_google_key_valid = validate_google_api_key() # Verifica si la clave de Gemini está configurada
# --- FIN Sidebar Auth & Key Validation ---


# --- Helper Functions ---
def load_draft_into_editor(draft_name):
    content = storage.load_draft(user_folder_id, draft_name)
    if content is not None:
        st.session_state.current_draft_name = draft_name
        st.session_state.draft_content = content
        st.session_state.sidebar_suggestions = []
        st.session_state.inline_suggestion = ""
        # Limpiar valor previo del editor para forzar actualización en text_area
        draft_name_for_key = st.session_state.get('current_draft_name') or 'new_draft'
        editor_key_base = draft_name_for_key.replace(' ', '_').lower().replace('.', '_')
        editor_key = f"editor_area_{editor_key_base}"
        if f'_{editor_key}_prev' in st.session_state:
             del st.session_state[f'_{editor_key}_prev']
        st.success(f"Borrador cargado: {draft_name}")
        logging.info(f"SynapseMD: Loaded draft '{draft_name}' for user '{user_folder_id}'")
        st.rerun()
    else:
        st.error(f"Fallo al cargar borrador: {draft_name}")
        logging.error(f"SynapseMD: Failed to load draft '{draft_name}' for user '{user_folder_id}'")

def save_current_draft():
    if st.session_state.current_draft_name:
        try:
            storage.save_draft(user_folder_id, st.session_state.current_draft_name, st.session_state.draft_content)
            st.toast(f"Borrador '{st.session_state.current_draft_name}' guardado.", icon="💾")
            logging.info(f"SynapseMD: Saved draft '{st.session_state.current_draft_name}' for user '{user_folder_id}'")
        except Exception as e:
            st.error(f"Error guardando borrador: {e}")
            logging.error(f"SynapseMD: Error saving draft '{st.session_state.current_draft_name}' for user '{user_folder_id}': {e}")
    else:
        st.warning("No se puede guardar, no hay nombre de borrador. Usa 'Guardar Como'.")
        logging.warning(f"SynapseMD: Save attempt failed for user '{user_folder_id}', no draft name set.")

def clear_editor():
    st.session_state.current_draft_name = None
    st.session_state.draft_content = ""
    st.session_state.sidebar_suggestions = []
    st.session_state.inline_suggestion = ""
    # Limpiar valor previo del editor si existe
    editor_key_base = "editor_area_new_draft"
    if f'_{editor_key_base}_prev' in st.session_state:
         del st.session_state[f'_{editor_key_base}_prev']
    logging.info(f"SynapseMD: Editor cleared for user '{user_folder_id}'")
    st.rerun()

def delete_current_draft():
     if st.session_state.current_draft_name:
        draft_to_delete = st.session_state.current_draft_name
        try:
            storage.delete_draft(user_folder_id, draft_to_delete)
            st.success(f"Borrador eliminado: {draft_to_delete}")
            logging.info(f"SynapseMD: Deleted draft '{draft_to_delete}' for user '{user_folder_id}'")
            clear_editor() # Llama a rerun
        except Exception as e:
            st.error(f"No se pudo eliminar el borrador: {e}")
            logging.error(f"SynapseMD: Failed to delete draft '{draft_to_delete}' for user '{user_folder_id}': {e}")
     else:
        st.warning("No hay borrador seleccionado para eliminar.")
        logging.warning(f"SynapseMD: Delete draft attempt failed for user '{user_folder_id}', no draft selected.")

# --- Sidebar ---
with st.sidebar:
    # La info de Auth ya se muestra arriba por auth.display_auth_status_sidebar()
    st.header("⚙️ Ajustes")
    # Selección Backend LLM
    backend_options = ["google", "lm_studio"]
    # Manejo seguro del índice y estado inicial
    try:
        if "llm_backend" not in st.session_state or st.session_state.llm_backend not in backend_options:
             st.session_state.llm_backend = ACTIVE_LLM_BACKEND # Usar default de config.py
             logging.warning(f"SynapseMD: Resetting LLM backend to default '{ACTIVE_LLM_BACKEND}' due to invalid state for user '{user_folder_id}'.")
        current_backend_index = backend_options.index(st.session_state.llm_backend)
    except ValueError:
        current_backend_index = 0
        st.session_state.llm_backend = backend_options[current_backend_index]
        logging.warning(f"SynapseMD: Invalid LLM backend '{st.session_state.llm_backend}' in session state for user '{user_folder_id}'. Defaulting to '{backend_options[current_backend_index]}'.")

    new_backend = st.selectbox(
        "Selecciona Backend LLM",
        options=backend_options,
        index=current_backend_index,
        key="llm_backend_selector",
        help="Elige el motor de lenguaje a usar (Google Gemini o un modelo local vía LM Studio)."
    )
    # Cambiar backend solo si es diferente y limpiar caché del cliente LLM
    if new_backend != st.session_state.llm_backend:
        st.session_state.llm_backend = new_backend
        try:
            get_llm_client.clear() # Asume que get_llm_client usa @st.cache_resource o similar
            logging.info(f"SynapseMD: Cleared LLM client cache for user '{user_folder_id}'.")
        except Exception as e:
             logging.error(f"SynapseMD: Could not clear LLM client cache: {e}") # Informar si falla
        st.success(f"Backend LLM cambiado a: {new_backend.upper()}.")
        logging.info(f"SynapseMD: LLM backend changed to '{new_backend}' for user '{user_folder_id}'")
        st.rerun()

    st.caption(f"Usando: {st.session_state.llm_backend.upper()}")
    st.divider()
    st.header("📝 Gestión de Borradores")

    # Carga de Borradores
    draft_files = storage.list_drafts(user_folder_id)
    draft_select_key = f"draft_selector_{user_folder_id}_{'_'.join(sorted(draft_files))}" # Clave más única
    selected_draft = st.selectbox(
        "Cargar Borrador",
        options=[""] + draft_files,
        index=0,
        format_func=lambda x: "Seleccionar..." if x == "" else x,
        key=draft_select_key,
        help="Selecciona un borrador existente para cargarlo en el editor."
        )
    # Programar carga solo si se selecciona algo diferente y no es la opción vacía
    if selected_draft and selected_draft != st.session_state.get("current_draft_name"):
         st.session_state._scheduled_load_draft = selected_draft
         st.rerun() # Rerun para procesar la carga

    # Procesar carga programada (al principio del script después del rerun)
    if st.session_state.get('_scheduled_load_draft'):
        draft_to_load = st.session_state._scheduled_load_draft
        st.session_state._scheduled_load_draft = None # Limpiar bandera inmediatamente
        load_draft_into_editor(draft_to_load) # Llama a rerun

    # Botones Nuevo/Guardar
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📄 Nuevo", use_container_width=True, key="new_draft_btn", help="Limpia el editor para empezar un nuevo borrador."):
            clear_editor() # Llama a rerun
    with col2:
        can_save = bool(st.session_state.get("current_draft_name"))
        if st.button("💾 Guardar", use_container_width=True, type="primary", disabled=not can_save, key="save_btn", help="Guarda los cambios del borrador actual."):
            save_current_draft() # Muestra toast, no necesita rerun

    # Formulario Guardar Como
    with st.form("save_as_form"):
        st.write("**Guardar Como / Renombrar**")
        # Corrección del error NoneType
        current_name = st.session_state.get("current_draft_name")
        current_name_str = current_name if current_name is not None else ""
        default_save_as_name = current_name_str.removesuffix(".md")

        new_draft_name_input = st.text_input(
            "Nuevo nombre:",
            value=default_save_as_name,
            placeholder="Introduce un nombre para el borrador",
            key="save_as_input",
            help="Escribe un nombre y presiona 'Guardar Como' para guardar el contenido actual con ese nombre (o renombrar el actual)."
        )
        submitted_save_as = st.form_submit_button("Guardar Como", use_container_width=True)
        if submitted_save_as:
            new_name_stripped = new_draft_name_input.strip()
            if new_name_stripped:
                # Limpieza robusta del nombre
                clean_name = "".join(c for c in new_name_stripped if c.isalnum() or c in ('_', '-', '@', '.', ' ')).strip()
                clean_name = re.sub(r'\s+', '_', clean_name) # Espacios a guion bajo
                clean_name = re.sub(r'[\\/*?:"<>|]+', '', clean_name) # Eliminar caracteres inválidos en Windows/Linux
                clean_name = re.sub(r'\.+', '.', clean_name).strip('.') # Puntos múltiples/extremos

                if clean_name and clean_name.lower() != ".md":
                    if not clean_name.lower().endswith(".md"):
                        clean_name += ".md"

                    # Verificar si el nombre ya existe (opcional, para evitar sobrescribir sin querer)
                    # if clean_name in storage.list_drafts(user_folder_id) and clean_name != st.session_state.get("current_draft_name"):
                    #     st.warning(f"Ya existe un borrador llamado '{clean_name}'. Elige otro nombre si no quieres sobrescribirlo.")
                    # else:
                    st.session_state.current_draft_name = clean_name
                    save_current_draft()
                    logging.info(f"SynapseMD: Draft saved/renamed as '{clean_name}' for user '{user_folder_id}'")
                    st.rerun()
                else:
                    st.warning("Nombre de borrador inválido. Evita solo puntos o caracteres especiales.")
                    logging.warning(f"SynapseMD: Invalid draft name after cleaning for user '{user_folder_id}': '{new_draft_name_input}' -> '{clean_name}'")
            else:
                st.warning("Por favor, introduce un nombre.")

    # Botón Eliminar
    if st.session_state.get("current_draft_name"):
        st.divider()
        st.warning(f"¿Seguro que quieres eliminar '{st.session_state.current_draft_name}' permanentemente?", icon="⚠️")
        if st.button(f"❌ Eliminar Borrador Actual", use_container_width=True, type="secondary", key="delete_draft_btn", help="Elimina el borrador actual del disco. Esta acción no se puede deshacer."):
            delete_current_draft() # Llama a rerun

# --- Main Application Tabs ---
tab1, tab2 = st.tabs(["📝 Editor", "📚 Gestión de Contexto"])

# --- Editor Tab ---
with tab1:
    current_draft_display = st.session_state.get("current_draft_name", "Sin Título")
    st.header(f"📝 Editor ({current_draft_display})")
    editor_col, suggestion_col = st.columns([3, 1]) # Ajustar proporción si es necesario

    with editor_col:
        # Generación de Borrador Inicial
        with st.expander("🚀 Generar Borrador Inicial con IA", expanded=not st.session_state.draft_content):
            with st.form("draft_generation_form"):
                draft_prompt = st.text_area("Instrucciones:", height=100, key="draft_gen_prompt", placeholder="Ej: Escribe un resumen sobre...")
                available_contexts_draft = storage.list_context_sources(user_folder_id)
                selected_context_draft = st.multiselect("Usar contexto (opcional):", options=available_contexts_draft, key="draft_gen_context_select")
                submitted_generate = st.form_submit_button("Generar Borrador", type="primary")
                if submitted_generate and draft_prompt.strip():
                    logging.info(f"SynapseMD: Draft generation requested by user '{user_folder_id}' with prompt: '{draft_prompt[:50]}...'")
                    with st.spinner("🤖 Generando borrador inicial..."):
                        generated_content = editor_features.generate_initial_draft(user_folder_id, draft_prompt, selected_context_draft)
                        if generated_content and "error" not in generated_content.lower()[:20]:
                            st.session_state.draft_content = generated_content
                            if not st.session_state.current_draft_name:
                                st.session_state.current_draft_name = f"borrador_ia_{int(time.time())}.md"
                            # Limpiar valor previo del editor para forzar refresco
                            draft_name_for_key = st.session_state.get('current_draft_name') or 'new_draft'
                            editor_key_base = draft_name_for_key.replace(' ', '_').lower().replace('.', '_')
                            editor_key = f"editor_area_{editor_key_base}"
                            if f'_{editor_key}_prev' in st.session_state:
                                del st.session_state[f'_{editor_key}_prev']
                            logging.info(f"SynapseMD: Initial draft generated successfully for user '{user_folder_id}'. Assigned name: {st.session_state.current_draft_name}")
                            st.rerun()
                        elif generated_content:
                            st.error(f"Fallo en generación de borrador: {generated_content}")
                            logging.error(f"SynapseMD: Draft generation failed for user '{user_folder_id}'. Response: {generated_content}")
                        else:
                            st.error("Fallo en generación de borrador (respuesta vacía).")
                            logging.error(f"SynapseMD: Draft generation failed for user '{user_folder_id}' (empty response).")
                elif submitted_generate:
                    st.warning("Por favor, introduce instrucciones.")

        st.divider()
        st.subheader("Editar Documento")

        # Clave única y segura para el editor basada en el nombre del borrador o 'new_draft'
        # Ensure we have a string ('new_draft' if current_draft_name is None or empty)
        draft_name_for_key = st.session_state.get('current_draft_name') or 'new_draft'
        editor_key_base = draft_name_for_key.replace(' ', '_').lower().replace('.', '_')

        editor_key = f"editor_area_{editor_key_base}"
        editor_content = st.text_area(
            "Contenido (Markdown):",
            value=st.session_state.draft_content, # El valor se actualiza vía estado
            height=600,
            key=editor_key,
            help="Escribe o edita tu documento aquí usando sintaxis Markdown."
            )
        # Detectar cambios y actualizar estado sin rerun inmediato
        # Usar un valor previo guardado en estado para comparar
        prev_content_key = f'_{editor_key}_prev'
        if editor_content != st.session_state.get(prev_content_key, ''):
            st.session_state.draft_content = editor_content
            st.session_state.last_edit_time = time.time()
            st.session_state.inline_suggestion = "" # Limpiar sugerencia
            st.session_state[prev_content_key] = editor_content # Guardar valor actual como previo
            # No st.rerun() aquí para permitir escritura fluida

        # Botón/Display de Autocompletado Inline
        ac_button_placeholder = st.empty()
        can_suggest_inline = st.session_state.draft_content and not st.session_state.inline_suggestion
        if can_suggest_inline:
            if ac_button_placeholder.button("✨ Sugerir Completado", key="get_suggestion_btn", help="Obtener una sugerencia de la IA para continuar el texto."):
                    logging.info(f"SynapseMD: Inline suggestion requested by user '{user_folder_id}'")
                    with st.spinner("🤔 Pensando..."):
                        suggestion = editor_features.get_inline_suggestion(st.session_state.draft_content)
                        if suggestion and "error" not in suggestion.lower():
                            st.session_state.inline_suggestion = suggestion
                            logging.info(f"SynapseMD: Inline suggestion generated: '{suggestion[:50]}...'")
                        else:
                            st.warning(f"No se pudo obtener sugerencia: {suggestion}")
                            logging.warning(f"SynapseMD: Failed to get inline suggestion for user '{user_folder_id}'. Response: {suggestion}")
                        st.rerun() # Rerun para mostrar sugerencia o advertencia
        # Mostrar sugerencia si existe
        if st.session_state.inline_suggestion:
            suggestion_text = st.session_state.inline_suggestion
            col_sug_text, col_sug_btn = ac_button_placeholder.columns([4, 1])
            col_sug_text.info(f"Sugerencia: `{suggestion_text}`")
            if col_sug_btn.button("➕ Añadir", key="accept_suggestion_btn", help="Añadir la sugerencia al final del texto."):
                 st.session_state.draft_content += suggestion_text
                 st.session_state.inline_suggestion = "" # Limpiar sugerencia
                 st.session_state[prev_content_key] = st.session_state.draft_content # Actualizar previo
                 logging.info(f"SynapseMD: Inline suggestion accepted by user '{user_folder_id}'.")
                 st.rerun() # Rerun para actualizar editor

    # Columna de Sugerencias Laterales
    with suggestion_col:
        st.subheader("💡 Sugerencias IA")
        available_contexts_edit = storage.list_context_sources(user_folder_id)
        # Selección de contexto persistente usando session_state
        selected_context_editor = st.multiselect(
            "Contexto para Sugerencias:",
            options=available_contexts_edit,
            default=st.session_state.selected_context, # Usar el estado guardado
            key="editor_context_selector",
            help="Selecciona fuentes de contexto para ayudar a la IA a generar sugerencias relevantes."
            )
        # Actualizar estado si la selección cambia
        if selected_context_editor != st.session_state.selected_context:
            st.session_state.selected_context = selected_context_editor
            logging.info(f"SynapseMD: Editor context selection changed for user '{user_folder_id}'. Selected: {selected_context_editor}")
            st.rerun() # Recargar para que el botón use el nuevo contexto

        # Botón para generar sugerencias
        if st.button("Generar Sugerencias de Sección", use_container_width=True, key="gen_sidebar_sug", help="Obtener ideas para la siguiente sección basadas en el borrador y el contexto seleccionado."):
            if not st.session_state.draft_content.strip() and not st.session_state.selected_context:
                st.warning("Escribe algo o selecciona fuentes de contexto primero.")
            else:
                 logging.info(f"SynapseMD: Sidebar suggestions requested by user '{user_folder_id}'.")
                 with st.spinner("🧠 Generando sugerencias..."):
                    suggestions = editor_features.get_sidebar_suggestions(
                        user_folder_id,
                        st.session_state.draft_content,
                        st.session_state.selected_context
                        )
                    # Filtrar posibles errores devueltos como sugerencias
                    valid_suggestions = [s for s in suggestions if isinstance(s, dict) and "title" in s and "content" in s]
                    if len(valid_suggestions) != len(suggestions):
                         st.warning("Algunas sugerencias no pudieron ser generadas correctamente.")
                         logging.warning(f"SynapseMD: Received invalid suggestions for user '{user_folder_id}'. Raw: {suggestions}")

                    st.session_state.sidebar_suggestions = valid_suggestions
                    logging.info(f"SynapseMD: Sidebar suggestions generated/updated for user '{user_folder_id}'. Count: {len(valid_suggestions)}")
                    # No se necesita rerun aquí, se actualiza el estado y se muestra abajo

        st.markdown("---")
        # Mostrar sugerencias si existen
        if not st.session_state.sidebar_suggestions:
             st.caption("Haz clic en el botón de arriba para obtener ideas de secciones.")
        else:
            st.write("**Secciones Sugeridas:**")
            suggestions_container = st.container(height=500, border=False) # Contenedor con scroll
            with suggestions_container:
                for i, sug in enumerate(st.session_state.sidebar_suggestions):
                    with st.container(border=True):
                        st.markdown(f"**{sug.get('title', f'Sugerencia {i+1}')}**")
                        st.markdown(sug.get('content', ''))
                        if st.button("➕ Añadir al Borrador", key=f"append_suggestion_{i}_{sug.get('title', '')}", use_container_width=True, help="Añade esta sección sugerida al final del borrador."): # Clave más única
                            title = sug.get('title', f'Sección Sugerida {i+1}')
                            content = sug.get('content', '')
                            # Añadir con formato Markdown
                            st.session_state.draft_content += f"\n\n## {title}\n\n{content}\n"
                            st.session_state[prev_content_key] = st.session_state.draft_content # Actualizar previo
                            logging.info(f"SynapseMD: Sidebar suggestion '{title}' appended by user '{user_folder_id}'.")
                            st.rerun() # Rerun para actualizar editor

# --- Context Management Tab ---
with tab2:
    st.header("📚 Gestionar Fuentes de Contexto")
    st.caption("Sube PDFs o pega texto para usar como contexto por la IA.")
    col1, col2 = st.columns(2)

    # Columna Izquierda: Añadir Contexto
    with col1:
        st.subheader("⬆️ Añadir Nuevo Contexto")
        # Formulario de Carga de PDF
        with st.form("pdf_upload_form", clear_on_submit=True):
            uploaded_pdfs = st.file_uploader(
                "Subir Archivos PDF",
                type=["pdf"],
                accept_multiple_files=True,
                key="pdf_uploader_input",
                help="Selecciona uno o más archivos PDF para procesar y añadir como contexto."
                )
            submitted_pdf_upload = st.form_submit_button("Procesar PDFs Subidos")

            if submitted_pdf_upload and uploaded_pdfs:
                processed_count = 0
                error_files = []
                progress_bar = st.progress(0.0, text="Iniciando procesamiento...")
                status_message = st.empty()
                total_files = len(uploaded_pdfs)
                logging.info(f"SynapseMD: PDF upload initiated by user '{user_folder_id}'. Files: {total_files}")

                for i, pdf_file in enumerate(uploaded_pdfs):
                    # Crear un nombre de archivo seguro para mostrar
                    safe_display_name = pdf_file.name.encode('ascii', 'ignore').decode('ascii') # Evitar caracteres problemáticos en UI
                    progress_text = f"Procesando {safe_display_name} ({i+1}/{total_files})..."
                    status_message.info(progress_text)
                    progress_bar.progress((i + 0.5) / total_files, text=progress_text) # Progreso intermedio
                    try:
                        # Llamada a la función de procesamiento
                        context_id = context_processing.process_uploaded_pdf(user_folder_id, pdf_file, pdf_file.name)
                        if context_id:
                             processed_count += 1
                             logging.info(f"SynapseMD: Successfully processed PDF '{pdf_file.name}' -> '{context_id}' for user '{user_folder_id}'")
                        else:
                            error_files.append(safe_display_name)
                            logging.warning(f"SynapseMD: PDF processing returned no context ID for '{pdf_file.name}', user '{user_folder_id}'")
                    except Exception as e:
                        st.error(f"Error procesando {safe_display_name}: {e}")
                        error_files.append(safe_display_name)
                        logging.error(f"SynapseMD: Error processing PDF '{pdf_file.name}' for user '{user_folder_id}': {e}", exc_info=True)
                    progress_bar.progress((i + 1) / total_files, text=progress_text.replace("Procesando", "Procesado")) # Progreso final para este archivo

                status_message.empty() # Limpiar mensaje de estado
                progress_bar.empty() # Limpiar barra de progreso
                if processed_count > 0: st.success(f"Se procesaron {processed_count} PDF(s) exitosamente.")
                if error_files: st.warning(f"No se pudieron procesar los siguientes archivos: {', '.join(error_files)}")
                st.rerun() # Rerun para actualizar la lista de contextos en la otra columna
            elif submitted_pdf_upload:
                st.warning("No se seleccionaron archivos PDF.")

        st.markdown("---")
        # Formulario de Entrada de Texto
        with st.form("text_context_form", clear_on_submit=True):
            st.write("**Pegar Contexto de Texto:**")
            text_context_name = st.text_input("Nombre del Contexto:", placeholder="Ej: Notas Reunión, Fragmento Artículo", key="text_ctx_name", help="Dale un nombre descriptivo a este fragmento de texto.")
            text_context_content = st.text_area("Pega el contenido aquí:", height=150, key="text_ctx_content", help="Pega el texto que quieres usar como fuente de contexto.")
            submitted_text_context = st.form_submit_button("Guardar Contexto de Texto")

            if submitted_text_context and text_context_name.strip() and text_context_content.strip():
                logging.info(f"SynapseMD: Text context submission by user '{user_folder_id}'. Name: '{text_context_name}'")
                with st.spinner("Guardando contexto de texto..."):
                    context_id = context_processing.process_text_context(user_folder_id, text_context_content, text_context_name)
                    if context_id:
                        st.success(f"Contexto de texto '{context_id}' guardado.")
                        logging.info(f"SynapseMD: Text context '{context_id}' saved successfully for user '{user_folder_id}'.")
                        st.rerun() # Actualizar lista de contextos
                    else:
                        st.error("Fallo al guardar contexto de texto.")
                        logging.error(f"SynapseMD: Failed to save text context '{text_context_name}' for user '{user_folder_id}'.")
            elif submitted_text_context:
                st.warning("Por favor, introduce nombre y contenido para el contexto.")

    # Columna Derecha: Ver Contextos Disponibles
    with col2:
        st.subheader("📖 Fuentes de Contexto Disponibles")
        contexts = storage.list_context_sources(user_folder_id)
        if not contexts:
            st.info("No se encontraron fuentes de contexto. Sube PDFs o pega texto usando los formularios de la izquierda.")
        else:
            st.caption("Haz clic en el nombre para expandir, ver detalles o eliminar.")
            # Contenedor con scroll para la lista
            context_list_container = st.container(height=600) # Ajustar altura según necesidad
            with context_list_container:
                for context_name in contexts:
                    # Crear clave única y segura para elementos dentro del loop
                    safe_context_name = context_name.replace(' ', '_').replace('.', '_').encode('ascii', 'ignore').decode('ascii')
                    # expander_key = f"expander_{safe_context_name}_{user_folder_id}" # Mantener para claves internas

                    icon = "📄" if context_name.lower().endswith(".txt") else "📁"

                    # --- FIX START (Line ~540 original) ---
                    # Remove key argument from st.expander
                    with st.expander(f"{icon} {context_name}"):
                    # --- FIX END ---

                        # Use derived key for elements *inside* the expander
                        expander_key_suffix = f"{safe_context_name}_{user_folder_id}" # Base for internal keys

                        is_text_file = context_name.lower().endswith(".txt")
                        if is_text_file:
                            # Cargar y mostrar contenido de archivo de texto
                            content = storage.load_text_context(user_folder_id, context_name)
                            st.text_area("Contenido:", value=content or "Error al cargar contenido.", height=150, disabled=True, key=f"view_text_{expander_key_suffix}")
                        else: # Carpeta de Contexto PDF (procesado)
                            # Mostrar información del resumen
                            summary = storage.load_summary(user_folder_id, context_name)
                            if summary and "page_summaries" in summary and summary['page_summaries']:
                                num_summaries = len(summary['page_summaries'])
                                st.write(f"**Páginas Resumidas:** {num_summaries}")
                                if st.checkbox(f"Mostrar Vista Previa Resúmenes ({min(num_summaries, 3)} de {num_summaries})", key=f"show_sum_{expander_key_suffix}", value=False):
                                    # Mostrar hasta 3 resúmenes para vista previa
                                    st.json(summary['page_summaries'][:3])
                            else: st.caption("Resumen no encontrado o vacío.")

                            # Checkbox para mostrar vista previa del texto extraído
                            if st.checkbox("Mostrar Vista Previa Texto Extraído", key=f"view_full_text_{expander_key_suffix}", value=False):
                                with st.spinner("Cargando texto completo..."):
                                    full_text = storage.load_extracted_text(user_folder_id, context_name)
                                if full_text:
                                     # Mostrar solo una parte del texto para no sobrecargar
                                    preview_text = full_text[:2000] + ("..." if len(full_text) > 2000 else "")
                                    st.text_area("Vista Previa Texto (primeros 2000 caract.):", value=preview_text, height=200, disabled=True, key=f"view_full_area_{expander_key_suffix}")
                                else: st.caption("Archivo de texto extraído no encontrado.")

                        st.divider()
                        # Botón de borrado para esta fuente de contexto
                        delete_key = f"delete_ctx_{expander_key_suffix}"
                        st.warning("Esta acción eliminará permanentemente los datos de esta fuente.", icon="🗑️")
                        if st.button(f"Eliminar Fuente", key=delete_key, type="secondary", help=f"Eliminar todos los datos relacionados con '{context_name}'"):
                            try:
                                logging.warning(f"SynapseMD: Deletion requested for context '{context_name}' by user '{user_folder_id}'.")
                                storage.delete_context_source(user_folder_id, context_name)
                                st.success(f"Fuente de contexto eliminada: {context_name}")
                                logging.info(f"SynapseMD: Deleted context source '{context_name}' for user '{user_folder_id}'.")
                                # Eliminar de la selección activa si estaba seleccionado
                                if context_name in st.session_state.selected_context:
                                     st.session_state.selected_context = [ctx for ctx in st.session_state.selected_context if ctx != context_name]
                                st.rerun() # Refrescar la lista y cualquier estado dependiente
                            except Exception as e:
                                st.error(f"No se pudo eliminar '{context_name}': {e}")
                                logging.error(f"SynapseMD: Failed to delete context source '{context_name}' for user '{user_folder_id}': {e}")

# --- Footer ---
st.divider()
user_display_footer = user_info.get('email', 'Usuario Desconocido')
st.caption(f"{APP_NAME} - © {time.strftime('%Y')} | Usuario: {user_display_footer}")

# --- Fin del Script ---