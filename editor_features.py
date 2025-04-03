# D:/synapse-md/synapse_md_app/editor_features.py
import streamlit as st
import time
import json
from llm_interface import get_llm_client
from config import MAX_CONTEXT_TOKENS_SMALL, MAX_CONTEXT_TOKENS_MEDIUM, MAX_CONTEXT_TOKENS_LARGE
from storage import load_summary, load_extracted_text, load_text_context

# Helper to get relevant context snippets (simplified relevance)
def get_relevant_context_snippets(user_identifier: str, selected_context_names: list[str], query_or_draft_end: str, max_tokens: int) -> str:
    context_str = ""
    approx_tokens = 0

    for name in selected_context_names:
        if approx_tokens >= max_tokens: break # Stop if limit reached

        full_text = None
        summary_data = None
        is_text_file = name.lower().endswith(".txt")

        if is_text_file:
            full_text = load_text_context(user_identifier, name)
        else: # Processed PDF context (directory name)
            summary_data = load_summary(user_identifier, name)
            if not summary_data: full_text = load_extracted_text(user_identifier, name)

        source_contribution = ""
        if summary_data and "page_summaries" in summary_data:
            # Add summaries first
            header = f"\n--- Context Source: {name} (Summaries) ---\n"
            body = ""
            for page_sum in summary_data["page_summaries"]:
                 snippet = f"Page {page_sum.get('page', '?')}: {page_sum.get('summary', '')}\n"
                 if approx_tokens + len(header.split()) + len(body.split()) + len(snippet.split()) < max_tokens:
                     body += snippet
                 else: break
            if body: source_contribution = header + body

        elif full_text:
             # Add from full text if no summary or need more
             header = f"\n--- Context Source: {name} {'(Text File)' if is_text_file else '(Full Text Snippet)'} ---\n"
             # Estimate available tokens for this source's snippet
             remaining_tokens = max_tokens - approx_tokens - len(header.split())
             if remaining_tokens > 50: # Only add if there's reasonable space
                 # Take roughly remaining_tokens * 5 characters as an estimate
                 text_snippet = full_text[:remaining_tokens * 5]
                 source_contribution = header + text_snippet + "\n...\n"

        if source_contribution:
            context_str += source_contribution
            approx_tokens += len(source_contribution.split()) # Update token count

    return context_str[:max_tokens * 7] # Final safety trim

# Inline Autocomplete Suggestion
def get_inline_suggestion(current_text: str) -> str:
    if not current_text.strip(): return ""
    llm_small = get_llm_client(model_tier="small")
    if not llm_small: return "Error: LLM client unavailable."

    context_chunk = "\n".join(current_text.split("\n")[-5:]) # Last 5 lines
    context_chunk = context_chunk[-MAX_CONTEXT_TOKENS_SMALL:] # Limit length

    prompt = f"Continue writing the following text naturally:\n\n```\n{context_chunk}\n```\n\nContinuation:"
    suggestion = llm_small.autocomplete(context_chunk, max_tokens=40, temperature=0.6)

    if suggestion.startswith(context_chunk): suggestion = suggestion[len(context_chunk):].strip()
    # Return only the first line of the suggestion for inline use
    return suggestion.split('\n')[0]

# Sidebar Suggestions (Larger Blocks)
def get_sidebar_suggestions(user_identifier: str, current_draft: str, selected_context_names: list[str]) -> list[dict]:
    suggestions = []
    if not current_draft.strip() and not selected_context_names:
        st.info("Provide draft text or select context for suggestions.")
        return []

    llm_medium = get_llm_client(model_tier="medium")
    if not llm_medium:
        st.error("Medium LLM client unavailable.")
        return []

    context_material = get_relevant_context_snippets(
        user_identifier, selected_context_names, current_draft[-1500:],
        MAX_CONTEXT_TOKENS_MEDIUM // 2
    )
    draft_snippet = current_draft[-MAX_CONTEXT_TOKENS_MEDIUM // 2:]

    prompt = (
        f"Based on the context and current draft, suggest the **next logical section or paragraph**. "
        f"Provide 2-3 distinct suggestions as a JSON list of objects, each with 'title' and 'content' keys.\n\n"
        f"**Context:**\n{context_material}\n\n"
        f"**Draft (End Snippet):**\n```\n{draft_snippet}\n```\n\n"
        f"**JSON Output:**"
    )
    json_schema = {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "content": {"type": "string"}}, "required": ["title", "content"]}}

    response_data = llm_medium.generate_json(prompt, json_schema, max_tokens=1000, temperature=0.7)

    if isinstance(response_data, list):
        suggestions = [item for item in response_data if isinstance(item, dict) and "title" in item and "content" in item]
    elif isinstance(response_data, dict) and "error" in response_data:
         st.error(f"Suggestion Generation Error: {response_data['error']}")
         if "raw_content" in response_data: st.code(response_data['raw_content'], language='json')

    return suggestions

# Initial Draft Generation
def generate_initial_draft(user_identifier: str, draft_prompt: str, selected_context_names: list[str]) -> str:
    if not draft_prompt.strip():
        st.error("Please provide instructions for the draft.")
        return ""

    llm_large = get_llm_client(model_tier="large")
    if not llm_large: return "Error: Large LLM client unavailable."

    context_limit = MAX_CONTEXT_TOKENS_LARGE - len(draft_prompt.split()) - 500
    context_material = get_relevant_context_snippets(
        user_identifier, selected_context_names, draft_prompt, context_limit if context_limit > 0 else 500
    )

    full_prompt = (
        f"Generate a document draft based on the instructions and context.\n\n"
        f"**Instructions:**\n{draft_prompt}\n\n"
        f"**Context:**\n{context_material}\n\n"
        f"**Generated Draft:**\n"
    )

    # Allow significant length for draft
    draft_content = llm_large.generate_text(full_prompt, max_tokens=3000, temperature=0.7)

    if "error" in draft_content.lower() and len(draft_content) < 100:
        st.error(f"Draft Generation Failed: {draft_content}")
        return ""
    else:
        # st.success("Initial draft generated!") # Can be annoying, remove toast
        return draft_content