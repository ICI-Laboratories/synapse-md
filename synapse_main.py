import streamlit as st
import os
import time
import logging
import re

st.set_page_config(
    page_title="SynapseMD", layout="wide", initial_sidebar_state="expanded"
)

try:
    import auth_helpers as auth
except ImportError:
    st.error(
        "FATAL: No se pudo importar 'auth_helpers.py'. "
        "Asegúrate de que el archivo exista en la ubicación correcta."
    )
    st.stop()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

logging.info("SynapseMD: Verificando estado de autenticación...")
user_info = auth.ensure_authenticated(
    login_title="Bienvenido a SynapseMD",
    login_message="Tu asistente médico inteligente para documentos.",
    login_button_text="Iniciar sesión con SARA",
)
logging.info("SynapseMD: Sesión central validada.")

from config import (
    APP_NAME,
    BASE_STORAGE_PATH,
    ACTIVE_LLM_BACKEND,
    validate_google_api_key,
)
import storage
import context_processing
import editor_features
from llm_interface import get_llm_client

default_session_state_app = {
    "current_draft_name": None,
    "draft_content": "",
    "selected_context": [],
    "sidebar_suggestions": [],
    "inline_suggestion": "",
    "last_edit_time": time.time(),
    "llm_backend": ACTIVE_LLM_BACKEND,
    "_scheduled_load_draft": None,
}
for key, default_value in default_session_state_app.items():
    if key not in st.session_state:
        st.session_state[key] = default_value

try:
    user_folder_id = auth.get_user_namespace(user_info)
except auth.AuthError:
    logging.error("SynapseMD: Namespace configuration rejected.")
    st.error("No se pudo abrir el espacio de trabajo de esta cuenta.")
    st.stop()

user_folder = storage.get_user_dir_path(user_folder_id)
try:
    os.makedirs(user_folder, exist_ok=True)
    os.makedirs(os.path.join(user_folder, storage.DRAFTS_FOLDER), exist_ok=True)
    os.makedirs(os.path.join(user_folder, storage.CONTEXT_FOLDER), exist_ok=True)
    logging.info("SynapseMD: User workspace ready.")
except OSError:
    st.error("No se pudo crear la estructura de directorios del usuario.")
    logging.error("SynapseMD: User workspace initialization failed.")
    st.stop()

auth.display_auth_status_sidebar(app_name=APP_NAME, user_info=user_info)
is_google_key_valid = validate_google_api_key()


def load_draft_into_editor(draft_name):
    content = storage.load_draft(user_folder_id, draft_name)
    if content is not None:
        st.session_state.current_draft_name = draft_name
        st.session_state.draft_content = content
        st.session_state.sidebar_suggestions = []
        st.session_state.inline_suggestion = ""
        draft_name_for_key = st.session_state.get("current_draft_name") or "new_draft"
        editor_key_base = (
            draft_name_for_key.replace(" ", "_").lower().replace(".", "_")
        )
        editor_key = f"editor_area_{editor_key_base}"
        if f"_{editor_key}_prev" in st.session_state:
            del st.session_state[f"_{editor_key}_prev"]
        st.success(f"Borrador cargado: {draft_name}")
        logging.info("SynapseMD: Draft loaded.")
        st.rerun()
    else:
        st.error(f"Fallo al cargar borrador: {draft_name}")
        logging.error("SynapseMD: Draft loading failed.")


def save_current_draft():
    if st.session_state.current_draft_name:
        try:
            storage.save_draft(
                user_folder_id,
                st.session_state.current_draft_name,
                st.session_state.draft_content,
            )
            st.toast(
                f"Borrador '{st.session_state.current_draft_name}' guardado.", icon="💾"
            )
            logging.info("SynapseMD: Draft saved.")
        except Exception as e:
            st.error(f"Error guardando borrador: {e}")
            logging.error("SynapseMD: Draft saving failed.")
    else:
        st.warning("No se puede guardar, no hay nombre de borrador. Usa 'Guardar Como'.")
        logging.warning("SynapseMD: Draft save rejected because its name is missing.")


def clear_editor():
    st.session_state.current_draft_name = None
    st.session_state.draft_content = ""
    st.session_state.sidebar_suggestions = []
    st.session_state.inline_suggestion = ""
    editor_key_base = "editor_area_new_draft"
    if f"_{editor_key_base}_prev" in st.session_state:
        del st.session_state[f"_{editor_key_base}_prev"]
    logging.info("SynapseMD: Editor cleared.")
    st.rerun()


def delete_current_draft():
    if st.session_state.current_draft_name:
        draft_to_delete = st.session_state.current_draft_name
        try:
            storage.delete_draft(user_folder_id, draft_to_delete)
            st.success(f"Borrador eliminado: {draft_to_delete}")
            logging.info("SynapseMD: Draft deleted.")
            clear_editor()
        except Exception as e:
            st.error(f"No se pudo eliminar el borrador: {e}")
            logging.error("SynapseMD: Draft deletion failed.")
    else:
        st.warning("No hay borrador seleccionado para eliminar.")
        logging.warning("SynapseMD: Draft deletion rejected because no draft is selected.")


with st.sidebar:
    st.header("⚙️ Ajustes")
    backend_options = ["google", "lm_studio"]
    try:
        if (
            "llm_backend" not in st.session_state
            or st.session_state.llm_backend not in backend_options
        ):
            st.session_state.llm_backend = ACTIVE_LLM_BACKEND
            logging.warning("SynapseMD: Invalid LLM state reset to the configured default.")
        current_backend_index = backend_options.index(st.session_state.llm_backend)
    except ValueError:
        current_backend_index = 0
        st.session_state.llm_backend = backend_options[current_backend_index]
        logging.warning("SynapseMD: Unknown LLM backend reset to the configured default.")
    new_backend = st.selectbox(
        "Selecciona Backend LLM",
        options=backend_options,
        index=current_backend_index,
        key="llm_backend_selector",
        help="Elige el motor de lenguaje a usar (Google Gemini o un modelo local vía LM Studio).",
    )
    if new_backend != st.session_state.llm_backend:
        st.session_state.llm_backend = new_backend
        try:
            get_llm_client.clear()
            logging.info("SynapseMD: LLM client cache cleared.")
        except Exception:
            logging.error("SynapseMD: LLM client cache clear failed.")
        st.success(f"Backend LLM cambiado a: {new_backend.upper()}.")
        logging.info("SynapseMD: LLM backend changed.")
        st.rerun()
    st.caption(f"Usando: {st.session_state.llm_backend.upper()}")
    st.divider()
    st.header("📝 Gestión de Borradores")
    draft_files = storage.list_drafts(user_folder_id)
    draft_select_key = (
        f"draft_selector_{user_folder_id}_{'_'.join(sorted(draft_files))}"
    )
    selected_draft = st.selectbox(
        "Cargar Borrador",
        options=[""] + draft_files,
        index=0,
        format_func=lambda x: "Seleccionar..." if x == "" else x,
        key=draft_select_key,
        help="Selecciona un borrador existente para cargarlo en el editor.",
    )
    if selected_draft and selected_draft != st.session_state.get(
        "current_draft_name"
    ):
        st.session_state._scheduled_load_draft = selected_draft
        st.rerun()
    if st.session_state.get("_scheduled_load_draft"):
        draft_to_load = st.session_state._scheduled_load_draft
        st.session_state._scheduled_load_draft = None
        load_draft_into_editor(draft_to_load)
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "📄 Nuevo",
            use_container_width=True,
            key="new_draft_btn",
            help="Limpia el editor para empezar un nuevo borrador.",
        ):
            clear_editor()
    with col2:
        can_save = bool(st.session_state.get("current_draft_name"))
        if st.button(
            "💾 Guardar",
            use_container_width=True,
            type="primary",
            disabled=not can_save,
            key="save_btn",
            help="Guarda los cambios del borrador actual.",
        ):
            save_current_draft()
    with st.form("save_as_form"):
        st.write("**Guardar Como / Renombrar**")
        current_name = st.session_state.get("current_draft_name")
        current_name_str = current_name if current_name is not None else ""
        default_save_as_name = current_name_str.removesuffix(".md")
        new_draft_name_input = st.text_input(
            "Nuevo nombre:",
            value=default_save_as_name,
            placeholder="Introduce un nombre para el borrador",
            key="save_as_input",
            help="Escribe un nombre y presiona 'Guardar Como' para guardar el contenido actual con ese nombre (o renombrar el actual).",
        )
        submitted_save_as = st.form_submit_button(
            "Guardar Como", use_container_width=True
        )
        if submitted_save_as:
            new_name_stripped = new_draft_name_input.strip()
            if new_name_stripped:
                clean_name = "".join(
                    c
                    for c in new_name_stripped
                    if c.isalnum() or c in ("_", "-", "@", ".", " ")
                ).strip()
                clean_name = re.sub(r"\s+", "_", clean_name)
                clean_name = re.sub(r'[\\/*?:"<>|]+', "", clean_name)
                clean_name = re.sub(r"\.+", ".", clean_name).strip(".")
                if clean_name and clean_name.lower() != ".md":
                    if not clean_name.lower().endswith(".md"):
                        clean_name += ".md"
                    st.session_state.current_draft_name = clean_name
                    save_current_draft()
                    logging.info("SynapseMD: Draft saved or renamed.")
                    st.rerun()
                else:
                    st.warning(
                        "Nombre de borrador inválido. Evita solo puntos o caracteres especiales."
                    )
                    logging.warning("SynapseMD: Invalid draft name rejected.")
            else:
                st.warning("Por favor, introduce un nombre.")
    if st.session_state.get("current_draft_name"):
        st.divider()
        st.warning(
            f"¿Seguro que quieres eliminar '{st.session_state.current_draft_name}' permanentemente?",
            icon="⚠️",
        )
        if st.button(
            "❌ Eliminar Borrador Actual",
            use_container_width=True,
            type="secondary",
            key="delete_draft_btn",
            help="Elimina el borrador actual del disco. Esta acción no se puede deshacer.",
        ):
            delete_current_draft()

tab1, tab2 = st.tabs(["📝 Editor", "📚 Gestión de Contexto"])

with tab1:
    current_draft_display = st.session_state.get("current_draft_name", "Sin Título")
    st.header(f"📝 Editor ({current_draft_display})")
    editor_col, suggestion_col = st.columns([3, 1])
    with editor_col:
        with st.expander(
            "🚀 Generar Borrador Inicial con IA",
            expanded=not st.session_state.draft_content,
        ):
            with st.form("draft_generation_form"):
                draft_prompt = st.text_area(
                    "Instrucciones:",
                    height=100,
                    key="draft_gen_prompt",
                    placeholder="Ej: Escribe un resumen sobre...",
                )
                available_contexts_draft = storage.list_context_sources(user_folder_id)
                selected_context_draft = st.multiselect(
                    "Usar contexto (opcional):",
                    options=available_contexts_draft,
                    key="draft_gen_context_select",
                )
                submitted_generate = st.form_submit_button(
                    "Generar Borrador", type="primary"
                )
                if submitted_generate and draft_prompt.strip():
                    logging.info("SynapseMD: Draft generation requested.")
                    with st.spinner("🤖 Generando borrador inicial..."):
                        generated_content = editor_features.generate_initial_draft(
                            user_folder_id, draft_prompt, selected_context_draft
                        )
                        if (
                            generated_content
                            and "error" not in generated_content.lower()[:20]
                        ):
                            st.session_state.draft_content = generated_content
                            if not st.session_state.current_draft_name:
                                st.session_state.current_draft_name = (
                                    f"borrador_ia_{int(time.time())}.md"
                                )
                            draft_name_for_key = (
                                st.session_state.get("current_draft_name")
                                or "new_draft"
                            )
                            editor_key_base = (
                                draft_name_for_key.replace(" ", "_")
                                .lower()
                                .replace(".", "_")
                            )
                            editor_key = f"editor_area_{editor_key_base}"
                            if f"_{editor_key}_prev" in st.session_state:
                                del st.session_state[f"_{editor_key}_prev"]
                            logging.info("SynapseMD: Initial draft generated.")
                            st.rerun()
                        elif generated_content:
                            st.error(f"Fallo en generación de borrador: {generated_content}")
                            logging.error("SynapseMD: Draft generation failed.")
                        else:
                            st.error(
                                "Fallo en generación de borrador (respuesta vacía)."
                            )
                            logging.error("SynapseMD: Draft generation returned an empty response.")
                elif submitted_generate:
                    st.warning("Por favor, introduce instrucciones.")
        st.divider()
        st.subheader("Editar Documento")
        draft_name_for_key = st.session_state.get("current_draft_name") or "new_draft"
        editor_key_base = (
            draft_name_for_key.replace(" ", "_").lower().replace(".", "_")
        )
        editor_key = f"editor_area_{editor_key_base}"
        editor_content = st.text_area(
            "Contenido (Markdown):",
            value=st.session_state.draft_content,
            height=600,
            key=editor_key,
            help="Escribe o edita tu documento aquí usando sintaxis Markdown.",
        )
        prev_content_key = f"_{editor_key}_prev"
        if editor_content != st.session_state.get(prev_content_key, ""):
            st.session_state.draft_content = editor_content
            st.session_state.last_edit_time = time.time()
            st.session_state.inline_suggestion = ""
            st.session_state[prev_content_key] = editor_content
        ac_button_placeholder = st.empty()
        can_suggest_inline = (
            st.session_state.draft_content and not st.session_state.inline_suggestion
        )
        if can_suggest_inline:
            if ac_button_placeholder.button(
                "✨ Sugerir Completado",
                key="get_suggestion_btn",
                help="Obtener una sugerencia de la IA para continuar el texto.",
            ):
                logging.info("SynapseMD: Inline suggestion requested.")
                with st.spinner("🤔 Pensando..."):
                    suggestion = editor_features.get_inline_suggestion(
                        st.session_state.draft_content
                    )
                    if suggestion and "error" not in suggestion.lower():
                        st.session_state.inline_suggestion = suggestion
                        logging.info("SynapseMD: Inline suggestion generated.")
                    else:
                        st.warning(f"No se pudo obtener sugerencia: {suggestion}")
                        logging.warning("SynapseMD: Inline suggestion failed.")
                    st.rerun()
        if st.session_state.inline_suggestion:
            suggestion_text = st.session_state.inline_suggestion
            col_sug_text, col_sug_btn = ac_button_placeholder.columns([4, 1])
            col_sug_text.info(f"Sugerencia: `{suggestion_text}`")
            if col_sug_btn.button(
                "➕ Añadir",
                key="accept_suggestion_btn",
                help="Añadir la sugerencia al final del texto.",
            ):
                st.session_state.draft_content += suggestion_text
                st.session_state.inline_suggestion = ""
                st.session_state[prev_content_key] = st.session_state.draft_content
                logging.info("SynapseMD: Inline suggestion accepted.")
                st.rerun()
    with suggestion_col:
        st.subheader("💡 Sugerencias IA")
        available_contexts_edit = storage.list_context_sources(user_folder_id)
        selected_context_editor = st.multiselect(
            "Contexto para Sugerencias:",
            options=available_contexts_edit,
            default=st.session_state.selected_context,
            key="editor_context_selector",
            help="Selecciona fuentes de contexto para ayudar a la IA a generar sugerencias relevantes.",
        )
        if selected_context_editor != st.session_state.selected_context:
            st.session_state.selected_context = selected_context_editor
            logging.info("SynapseMD: Editor context selection changed.")
            st.rerun()
        if st.button(
            "Generar Sugerencias de Sección",
            use_container_width=True,
            key="gen_sidebar_sug",
            help="Obtener ideas para la siguiente sección basadas en el borrador y el contexto seleccionado.",
        ):
            if (
                not st.session_state.draft_content.strip()
                and not st.session_state.selected_context
            ):
                st.warning("Escribe algo o selecciona fuentes de contexto primero.")
            else:
                logging.info("SynapseMD: Sidebar suggestions requested.")
                with st.spinner("🧠 Generando sugerencias..."):
                    suggestions = editor_features.get_sidebar_suggestions(
                        user_folder_id,
                        st.session_state.draft_content,
                        st.session_state.selected_context,
                    )
                    valid_suggestions = [
                        s
                        for s in suggestions
                        if isinstance(s, dict) and "title" in s and "content" in s
                    ]
                    if len(valid_suggestions) != len(suggestions):
                        st.warning(
                            "Algunas sugerencias no pudieron ser generadas correctamente."
                        )
                        logging.warning("SynapseMD: Invalid suggestions were discarded.")
                    st.session_state.sidebar_suggestions = valid_suggestions
                    logging.info(
                        "SynapseMD: Sidebar suggestions updated; count=%d.",
                        len(valid_suggestions),
                    )
        st.markdown("---")
        if not st.session_state.sidebar_suggestions:
            st.caption("Haz clic en el botón de arriba para obtener ideas de secciones.")
        else:
            st.write("**Secciones Sugeridas:**")
            suggestions_container = st.container(height=500, border=False)
            with suggestions_container:
                for i, sug in enumerate(st.session_state.sidebar_suggestions):
                    with st.container(border=True):
                        st.markdown(f"**{sug.get('title', f'Sugerencia {i+1}')}**")
                        st.markdown(sug.get("content", ""))
                        if st.button(
                            "➕ Añadir al Borrador",
                            key=f"append_suggestion_{i}_{sug.get('title', '')}",
                            use_container_width=True,
                            help="Añade esta sección sugerida al final del borrador.",
                        ):
                            title = sug.get("title", f"Sección Sugerida {i+1}")
                            content = sug.get("content", "")
                            st.session_state.draft_content += (
                                f"\n\n## {title}\n\n{content}\n"
                            )
                            st.session_state[prev_content_key] = (
                                st.session_state.draft_content
                            )
                            logging.info("SynapseMD: Sidebar suggestion appended.")
                            st.rerun()

with tab2:
    st.header("📚 Gestionar Fuentes de Contexto")
    st.caption("Sube PDFs o pega texto para usar como contexto por la IA.")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("⬆️ Añadir Nuevo Contexto")
        with st.form("pdf_upload_form", clear_on_submit=True):
            uploaded_pdfs = st.file_uploader(
                "Subir Archivos PDF",
                type=["pdf"],
                accept_multiple_files=True,
                key="pdf_uploader_input",
                help="Selecciona uno o más archivos PDF para procesar y añadir como contexto.",
            )
            submitted_pdf_upload = st.form_submit_button("Procesar PDFs Subidos")
            if submitted_pdf_upload and uploaded_pdfs:
                processed_count = 0
                error_files = []
                progress_bar = st.progress(0.0, text="Iniciando procesamiento...")
                status_message = st.empty()
                total_files = len(uploaded_pdfs)
                logging.info(
                    "SynapseMD: PDF processing batch started; count=%d.",
                    total_files,
                )
                for i, pdf_file in enumerate(uploaded_pdfs):
                    safe_display_name = pdf_file.name.encode(
                        "ascii", "ignore"
                    ).decode("ascii")
                    progress_text = (
                        f"Procesando {safe_display_name} ({i+1}/{total_files})..."
                    )
                    status_message.info(progress_text)
                    progress_bar.progress(
                        (i + 0.5) / total_files, text=progress_text
                    )
                    try:
                        context_id = context_processing.process_uploaded_pdf(
                            user_folder_id, pdf_file, pdf_file.name
                        )
                        if context_id:
                            processed_count += 1
                            logging.info("SynapseMD: PDF processed.")
                        else:
                            error_files.append(safe_display_name)
                            logging.warning("SynapseMD: PDF processing returned no context.")
                    except Exception as e:
                        st.error(f"Error procesando {safe_display_name}: {e}")
                        error_files.append(safe_display_name)
                        logging.error("SynapseMD: PDF processing failed.")
                    progress_bar.progress(
                        (i + 1) / total_files,
                        text=progress_text.replace("Procesando", "Procesado"),
                    )
                status_message.empty()
                progress_bar.empty()
                if processed_count > 0:
                    st.success(f"Se procesaron {processed_count} PDF(s) exitosamente.")
                if error_files:
                    st.warning(
                        f"No se pudieron procesar los siguientes archivos: {', '.join(error_files)}"
                    )
                st.rerun()
            elif submitted_pdf_upload:
                st.warning("No se seleccionaron archivos PDF.")
        st.markdown("---")
        with st.form("text_context_form", clear_on_submit=True):
            st.write("**Pegar Contexto de Texto:**")
            text_context_name = st.text_input(
                "Nombre del Contexto:",
                placeholder="Ej: Notas Reunión, Fragmento Artículo",
                key="text_ctx_name",
                help="Dale un nombre descriptivo a este fragmento de texto.",
            )
            text_context_content = st.text_area(
                "Pega el contenido aquí:",
                height=150,
                key="text_ctx_content",
                help="Pega el texto que quieres usar como fuente de contexto.",
            )
            submitted_text_context = st.form_submit_button(
                "Guardar Contexto de Texto"
            )
            if (
                submitted_text_context
                and text_context_name.strip()
                and text_context_content.strip()
            ):
                logging.info("SynapseMD: Text context submission received.")
                with st.spinner("Guardando contexto de texto..."):
                    context_id = context_processing.process_text_context(
                        user_folder_id, text_context_content, text_context_name
                    )
                    if context_id:
                        st.success(f"Contexto de texto '{context_id}' guardado.")
                        logging.info("SynapseMD: Text context saved.")
                        st.rerun()
                    else:
                        st.error("Fallo al guardar contexto de texto.")
                        logging.error("SynapseMD: Text context save failed.")
            elif submitted_text_context:
                st.warning("Por favor, introduce nombre y contenido para el contexto.")
    with col2:
        st.subheader("📖 Fuentes de Contexto Disponibles")
        contexts = storage.list_context_sources(user_folder_id)
        if not contexts:
            st.info(
                "No se encontraron fuentes de contexto. "
                "Sube PDFs o pega texto usando los formularios de la izquierda."
            )
        else:
            st.caption("Haz clic en el nombre para expandir, ver detalles o eliminar.")
            context_list_container = st.container(height=600)
            with context_list_container:
                for context_name in contexts:
                    safe_context_name = (
                        context_name.replace(" ", "_")
                        .replace(".", "_")
                        .encode("ascii", "ignore")
                        .decode("ascii")
                    )
                    icon = "📄" if context_name.lower().endswith(".txt") else "📁"
                    with st.expander(f"{icon} {context_name}"):
                        expander_key_suffix = f"{safe_context_name}_{user_folder_id}"
                        is_text_file = context_name.lower().endswith(".txt")
                        if is_text_file:
                            content = storage.load_text_context(
                                user_folder_id, context_name
                            )
                            st.text_area(
                                "Contenido:",
                                value=content or "Error al cargar contenido.",
                                height=150,
                                disabled=True,
                                key=f"view_text_{expander_key_suffix}",
                            )
                        else:
                            summary = storage.load_summary(user_folder_id, context_name)
                            if (
                                summary
                                and "page_summaries" in summary
                                and summary["page_summaries"]
                            ):
                                num_summaries = len(summary["page_summaries"])
                                st.write(f"**Páginas Resumidas:** {num_summaries}")
                                if st.checkbox(
                                    f"Mostrar Vista Previa Resúmenes ({min(num_summaries, 3)} de {num_summaries})",
                                    key=f"show_sum_{expander_key_suffix}",
                                    value=False,
                                ):
                                    st.json(summary["page_summaries"][:3])
                            else:
                                st.caption("Resumen no encontrado o vacío.")
                            if st.checkbox(
                                "Mostrar Vista Previa Texto Extraído",
                                key=f"view_full_text_{expander_key_suffix}",
                                value=False,
                            ):
                                with st.spinner("Cargando texto completo..."):
                                    full_text = storage.load_extracted_text(
                                        user_folder_id, context_name
                                    )
                                if full_text:
                                    preview_text = full_text[:2000] + (
                                        "..." if len(full_text) > 2000 else ""
                                    )
                                    st.text_area(
                                        "Vista Previa Texto (primeros 2000 caract.):",
                                        value=preview_text,
                                        height=200,
                                        disabled=True,
                                        key=f"view_full_area_{expander_key_suffix}",
                                    )
                                else:
                                    st.caption(
                                        "Archivo de texto extraído no encontrado."
                                    )
                        st.divider()
                        delete_key = f"delete_ctx_{expander_key_suffix}"
                        st.warning(
                            "Esta acción eliminará permanentemente los datos de esta fuente.",
                            icon="🗑️",
                        )
                        if st.button(
                            "Eliminar Fuente",
                            key=delete_key,
                            type="secondary",
                            help=f"Eliminar todos los datos relacionados con '{context_name}'",
                        ):
                            try:
                                logging.warning("SynapseMD: Context deletion requested.")
                                storage.delete_context_source(
                                    user_folder_id, context_name
                                )
                                st.success(
                                    f"Fuente de contexto eliminada: {context_name}"
                                )
                                logging.info("SynapseMD: Context source deleted.")
                                if context_name in st.session_state.selected_context:
                                    st.session_state.selected_context = [
                                        ctx
                                        for ctx in st.session_state.selected_context
                                        if ctx != context_name
                                    ]
                                st.rerun()
                            except Exception as e:
                                st.error(f"No se pudo eliminar '{context_name}': {e}")
                                logging.error("SynapseMD: Context source deletion failed.")

st.divider()
user_display_footer = user_info.get("email", "Usuario Desconocido")
st.caption(f"{APP_NAME} - © {time.strftime('%Y')} | Usuario: {user_display_footer}")
