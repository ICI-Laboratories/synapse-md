# D:/synapse-md/synapse_md_app/context_processing.py
import fitz # PyMuPDF
import easyocr
from PIL import Image
import io
import os
import re
import streamlit as st
import time
from config import OCR_LANGUAGES, OCR_GPU
from storage import save_uploaded_pdf, save_extracted_text, save_summary, save_text_context
from llm_interface import get_llm_client

# Initialize EasyOCR reader (cached)
@st.cache_resource
def get_ocr_reader():
    print("Initializing EasyOCR Reader...")
    try:
        reader = easyocr.Reader(OCR_LANGUAGES, gpu=OCR_GPU)
        print("EasyOCR Reader Initialized.")
        return reader
    except Exception as e:
        st.error(f"Failed to initialize EasyOCR: {e}. OCR on images will fail.")
        print(f"Failed to initialize EasyOCR: {e}")
        return None

# Extract text from a single PDF page (fitz + OCR fallback)
def extract_text_from_pdf_page(page: fitz.Page, reader: easyocr.Reader | None) -> str:
    text = page.get_text("text", sort=True).strip()
    if len(text) < 50 and reader: # Try OCR if fitz text is short
        try:
            pix = page.get_pixmap(dpi=200)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            ocr_results = reader.readtext(img_byte_arr.getvalue(), detail=0, paragraph=True)
            ocr_text = " ".join(ocr_results).strip()
            if len(ocr_text) > len(text) + 20 or (not text and ocr_text):
                print(f"Page {page.number + 1}: Used OCR.")
                return ocr_text
            # else: print(f"Page {page.number + 1}: Used fitz text (OCR not better).")
        except Exception as ocr_e: print(f"OCR failed for page {page.number + 1}: {ocr_e}")
    return text

# Extract text from all pages with markers
def extract_full_text_with_markers(pdf_bytes: bytes, filename: str) -> str:
    full_text = ""
    ocr_reader = get_ocr_reader()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_pages = len(doc)
        progress_bar = st.progress(0, text=f"Extracting text from '{filename}' (0/{num_pages})")
        for i, page in enumerate(doc):
            page_num = i + 1
            page_text = extract_text_from_pdf_page(page, ocr_reader)
            if page_text:
                full_text += f"\n--- Página {page_num} ---\n{page_text}\n"
            progress_bar.progress((i + 1) / num_pages, text=f"Extracting text from '{filename}' ({page_num}/{num_pages})")
        doc.close()
        progress_bar.empty() # Remove progress bar on completion
        # st.success(f"Text extraction complete for '{filename}'.") # Can be too verbose
        return full_text
    except Exception as e:
        st.error(f"Error extracting text from '{filename}': {e}")
        if 'progress_bar' in locals(): progress_bar.empty()
        return ""

# Split marked text into pages
def extract_pages_from_marked_text(text: str) -> list[tuple[int, str]]:
    pages = []
    pattern = re.compile(r'--- Página\s+(\d+)\s+---(.*?)(?=--- Página|\Z)', re.DOTALL)
    for match in pattern.finditer(text):
        try:
            page_num = int(match.group(1))
            page_text = match.group(2).strip()
            if page_text: pages.append((page_num, page_text))
        except (ValueError, IndexError): continue
    return pages

# Summarize a single page using small LLM
def summarize_page(page_num: int, page_text: str, context_name: str) -> dict:
    if not page_text: return {"page": page_num, "summary": ""}
    llm_small = get_llm_client(model_tier="small")
    if not llm_small: return {"page": page_num, "summary": "Error: LLM client not available."}

    input_text_for_summary = page_text[:4000] # Limit input size
    summary_text = llm_small.summarize_text(input_text_for_summary, max_length=50, temperature=0.3)

    if "error" in summary_text.lower() or len(summary_text) < 10:
        print(f"Page {page_num}: Failed to get meaningful summary. Raw: {summary_text}")
        summary_text = f"Content related to page {page_num} of {context_name}." # Fallback
    return {"page": page_num, "summary": summary_text.strip()}

# Create hierarchical summary (page-by-page)
def create_hierarchical_summary(pages: list[tuple[int, str]], context_name: str) -> dict:
    if not pages: return {"context_name": context_name, "page_summaries": []}

    summaries = []
    total_pages = len(pages)
    progress_bar = st.progress(0, text=f"Summarizing '{context_name}' (0/{total_pages})")

    for i, (page_num, page_text) in enumerate(pages):
        summary_data = summarize_page(page_num, page_text, context_name)
        summaries.append(summary_data)
        progress_bar.progress((i + 1) / total_pages, text=f"Summarizing '{context_name}' ({page_num}/{total_pages})")
        time.sleep(0.05) # Small delay

    progress_bar.empty()
    # st.success(f"Summarization complete for '{context_name}'.")
    return {"context_name": context_name, "page_summaries": summaries}

# Main processing pipeline for uploaded PDF
def process_uploaded_pdf(user_identifier: str, uploaded_file: io.BytesIO, filename: str):
    st.write(f"Processing: {filename}")
    pdf_bytes = uploaded_file.getvalue()
    context_name = os.path.splitext(filename)[0]
    context_name_safe = "".join(c for c in context_name if c.isalnum() or c in (' ', '_', '-')).rstrip() # Safe name for folders

    # 1. Save Original PDF
    try: save_uploaded_pdf(user_identifier, context_name_safe, pdf_bytes, filename)
    except Exception as e: st.warning(f"⚠️ Could not save original PDF '{filename}': {e}")

    # 2. Extract Full Text
    full_extracted_text = extract_full_text_with_markers(pdf_bytes, filename)
    if not full_extracted_text:
        st.error(f"❌ Text extraction failed for '{filename}'. Cannot proceed.")
        return None

    # 3. Save Extracted Text
    try: save_extracted_text(user_identifier, context_name_safe, full_extracted_text)
    except Exception as e: st.warning(f"⚠️ Could not save extracted text for '{filename}': {e}")

    # 4. Split into Pages
    pages = extract_pages_from_marked_text(full_extracted_text)
    if not pages:
        st.warning(f"⚠️ No pages found after extraction for '{filename}'. Skipping summarization.")
        return context_name_safe # Return ID even if unusable

    # 5. Create Hierarchical Summary
    summary_data = create_hierarchical_summary(pages, context_name_safe)

    # 6. Save Summary
    try: save_summary(user_identifier, context_name_safe, summary_data)
    except Exception as e: st.warning(f"⚠️ Could not save summaries for '{filename}': {e}")

    return context_name_safe # Return the safe identifier

# Processing for user-provided text context
def process_text_context(user_identifier: str, text_content: str, context_name: str) -> str | None:
    st.write(f"Processing Text Context: {context_name}")
    if not text_content.strip():
        st.error("❌ Text context is empty.")
        return None
    try:
        clean_name = "".join(c for c in context_name if c.isalnum() or c in (' ', '_', '-')).rstrip()
        clean_name = (clean_name or f"text_context_{int(time.time())}") + ".txt"

        save_text_context(user_identifier, clean_name, text_content)
        st.success(f"💾 Text context '{clean_name}' saved.")
        return clean_name
    except Exception as e:
        st.error(f"❌ Failed to save text context '{context_name}': {e}")
        return None