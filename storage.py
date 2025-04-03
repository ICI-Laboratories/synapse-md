# D:/synapse-md/synapse_md_app/storage.py
import os
import json
import shutil
from config import BASE_STORAGE_PATH
# from auth import ensure_user_folder # Not needed here, paths are constructed

# Subfolder Names
DRAFTS_FOLDER = "drafts"
CONTEXT_FOLDER = "context_sources"
ORIGINAL_PDF_SUBFOLDER = "_originals"
EXTRACTED_TEXT_SUBFOLDER = "_extracted_text"
SUMMARY_SUBFOLDER = "_summaries"

# --- Path Generation Helpers ---
def get_user_dir_path(user_identifier: str) -> str:
    """Gets the user's main directory path (doesn't create)."""
    safe_identifier = "".join(c for c in user_identifier if c.isalnum() or c in ('_', '-', '@', '.')).rstrip()
    safe_identifier = safe_identifier or "default_user"
    return os.path.join(BASE_STORAGE_PATH, safe_identifier)

def get_drafts_dir(user_identifier: str) -> str:
    user_dir = get_user_dir_path(user_identifier)
    drafts_path = os.path.join(user_dir, DRAFTS_FOLDER)
    os.makedirs(drafts_path, exist_ok=True)
    return drafts_path

def get_context_dir(user_identifier: str) -> str:
    user_dir = get_user_dir_path(user_identifier)
    context_path = os.path.join(user_dir, CONTEXT_FOLDER)
    os.makedirs(context_path, exist_ok=True)
    return context_path

def _get_context_subfolder(user_identifier: str, context_name: str, subfolder_type: str) -> str:
    context_dir = get_context_dir(user_identifier)
    # Ensure context_name is safe for path construction if needed
    safe_context_name = "".join(c for c in context_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
    source_path = os.path.join(context_dir, safe_context_name) # Use safe name
    subfolder_path = os.path.join(source_path, subfolder_type)
    os.makedirs(subfolder_path, exist_ok=True)
    return subfolder_path

# --- Saving Functions ---
def save_draft(user_identifier: str, draft_name: str, content: str) -> str:
    drafts_dir = get_drafts_dir(user_identifier)
    if not draft_name.lower().endswith(".md"): draft_name += ".md"
    file_path = os.path.join(drafts_dir, draft_name)
    with open(file_path, "w", encoding="utf-8") as f: f.write(content)
    return file_path

def save_uploaded_pdf(user_identifier: str, context_name: str, pdf_file_bytes: bytes, original_filename: str) -> str:
    pdf_dir = _get_context_subfolder(user_identifier, context_name, ORIGINAL_PDF_SUBFOLDER)
    file_path = os.path.join(pdf_dir, original_filename) # Use original filename here
    with open(file_path, "wb") as f: f.write(pdf_file_bytes)
    return file_path

def save_extracted_text(user_identifier: str, context_name: str, text_content: str) -> str:
    text_dir = _get_context_subfolder(user_identifier, context_name, EXTRACTED_TEXT_SUBFOLDER)
    file_path = os.path.join(text_dir, f"{context_name}_full.txt")
    with open(file_path, "w", encoding="utf-8") as f: f.write(text_content)
    return file_path

def save_summary(user_identifier: str, context_name: str, summary_data: dict) -> str:
    summary_dir = _get_context_subfolder(user_identifier, context_name, SUMMARY_SUBFOLDER)
    file_path = os.path.join(summary_dir, f"{context_name}_summary.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False) # Smaller indent
    return file_path

def save_text_context(user_identifier: str, context_filename: str, text_content: str) -> str:
    context_dir = get_context_dir(user_identifier)
    # Assume context_filename already includes .txt and is safe
    file_path = os.path.join(context_dir, context_filename)
    with open(file_path, "w", encoding="utf-8") as f: f.write(text_content)
    return file_path

# --- Loading Functions ---
def list_drafts(user_identifier: str) -> list[str]:
    drafts_dir = get_drafts_dir(user_identifier)
    try:
        if os.path.exists(drafts_dir):
            return sorted([f for f in os.listdir(drafts_dir) if f.lower().endswith(".md")])
    except OSError: pass # Handle potential permission errors etc.
    return []

def load_draft(user_identifier: str, draft_name: str) -> str | None:
    drafts_dir = get_drafts_dir(user_identifier)
    file_path = os.path.join(drafts_dir, draft_name)
    try:
        if os.path.isfile(file_path):
            with open(file_path, "r", encoding="utf-8") as f: return f.read()
    except OSError: pass
    return None

def list_context_sources(user_identifier: str) -> list[str]:
    context_dir = get_context_dir(user_identifier)
    sources = []
    try:
        if os.path.exists(context_dir):
            for item in os.listdir(context_dir):
                item_path = os.path.join(context_dir, item)
                 # Add directories (PDF sources) that aren't hidden/internal
                if os.path.isdir(item_path) and not item.startswith("_"):
                    sources.append(item)
                 # Add top-level text files
                elif os.path.isfile(item_path) and item.lower().endswith(".txt"):
                    sources.append(item)
            return sorted(sources)
    except OSError: pass
    return []

def load_summary(user_identifier: str, context_name: str) -> dict | None:
    summary_dir = _get_context_subfolder(user_identifier, context_name, SUMMARY_SUBFOLDER)
    file_path = os.path.join(summary_dir, f"{context_name}_summary.json")
    try:
        if os.path.isfile(file_path):
            with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
    except (OSError, json.JSONDecodeError): pass
    return None

def load_extracted_text(user_identifier: str, context_name: str) -> str | None:
    text_dir = _get_context_subfolder(user_identifier, context_name, EXTRACTED_TEXT_SUBFOLDER)
    file_path = os.path.join(text_dir, f"{context_name}_full.txt")
    try:
        if os.path.isfile(file_path):
            with open(file_path, "r", encoding="utf-8") as f: return f.read()
    except OSError: pass
    return None

def load_text_context(user_identifier: str, context_filename: str) -> str | None:
    context_dir = get_context_dir(user_identifier)
    file_path = os.path.join(context_dir, context_filename)
    try:
        if os.path.isfile(file_path) and context_filename.lower().endswith(".txt"):
            with open(file_path, "r", encoding="utf-8") as f: return f.read()
    except OSError: pass
    return None

# --- Deletion Functions ---
def delete_draft(user_identifier: str, draft_name: str):
    drafts_dir = get_drafts_dir(user_identifier)
    file_path = os.path.join(drafts_dir, draft_name)
    try:
        if os.path.isfile(file_path): os.remove(file_path)
    except OSError as e: print(f"Error deleting draft {file_path}: {e}")

def delete_context_source(user_identifier: str, context_name: str):
    context_dir = get_context_dir(user_identifier)
    path_to_delete = os.path.join(context_dir, context_name)
    try:
        if os.path.isdir(path_to_delete):
            shutil.rmtree(path_to_delete)
        elif os.path.isfile(path_to_delete):
            os.remove(path_to_delete)
    except OSError as e: print(f"Error deleting context {path_to_delete}: {e}")