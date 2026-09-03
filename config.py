import os
import streamlit as st
import logging

APP_NAME = "SynapseMD"
BASE_STORAGE_PATH = r"D:\synapse-md"
ACTIVE_LLM_BACKEND = "google"
_google_api_key_default = "YOUR_GOOGLE_API_KEY_PLEASE_SET"
api_key_source = "not set"

if "GEMINI_API_KEY" in st.secrets:
    GOOGLE_API_KEY = st.secrets["GEMINI_API_KEY"]
    api_key_source = "st.secrets"
else:
    GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", _google_api_key_default)
    if GOOGLE_API_KEY != _google_api_key_default:
        api_key_source = "environment variable"
    else:
        api_key_source = "placeholder (not set)"

logging.info(f"🔑 Google API Key Source: {api_key_source}")

GOOGLE_MODELS = {
    "small": "gemma-3-27b-it",
    "medium": "gemini-2.0-flash",
    "large": "gemini-2.5-pro-exp-03-25",
}

LM_STUDIO_URL = "http://localhost:8080/lmstudio"
LM_STUDIO_HEADERS = {"Content-Type": "application/json"}
LM_STUDIO_MODELS = {
    "small": "meta-llama-3.1-8b-instruct",
    "medium": "meta-llama-3.1-8b-instruct",
    "large": "meta-llama-3.1-8b-instruct",
}

OCR_LANGUAGES = ["en", "es"]
OCR_GPU = True

MAX_CONTEXT_TOKENS_SMALL = 4000
MAX_CONTEXT_TOKENS_MEDIUM = 8000
MAX_CONTEXT_TOKENS_LARGE = 16000

def validate_google_api_key():
    if ACTIVE_LLM_BACKEND == "google" and GOOGLE_API_KEY == _google_api_key_default:
        st.warning(
            "⚠️ **Google API Key Not Configured!** Set `GEMINI_API_KEY` in "
            "`.streamlit/secrets.toml`.",
            icon="🔑",
        )
        return False
    return True

if not os.path.exists(BASE_STORAGE_PATH):
    logging.warning("Configured base storage path does not exist.")
