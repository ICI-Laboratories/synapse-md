import requests
import json
import google.generativeai as genai
import streamlit as st
import logging
from config import (
    ACTIVE_LLM_BACKEND,
    GOOGLE_API_KEY,
    GOOGLE_MODELS,
    _google_api_key_default,
    LM_STUDIO_URL,
    LM_STUDIO_HEADERS,
    LM_STUDIO_MODELS,
)


class LLMInterface:
    def __init__(self, model_tier: str = "small"):
        self.model_tier = model_tier
        self.model_name = self._get_model_name(model_tier)

    def _get_model_name(self, tier: str) -> str:
        raise NotImplementedError

    def generate_text(
        self, prompt: str, max_tokens: int = 500, temperature: float = 0.7
    ) -> str:
        raise NotImplementedError

    def generate_json(
        self,
        prompt: str,
        json_schema: dict,
        max_tokens: int = 300,
        temperature: float = 0.5,
    ) -> dict:
        raise NotImplementedError

    def summarize_text(
        self, text: str, max_length: int = 150, temperature: float = 0.3
    ) -> str:
        prompt = f"Summarize the following text concisely (around {max_length} words):\n\n{text}\n\nSummary:"
        estimated_tokens = int(max_length * 1.5)
        return self.generate_text(
            prompt, max_tokens=estimated_tokens, temperature=temperature
        )

    def autocomplete(
        self, text_before_cursor: str, max_tokens: int = 50, temperature: float = 0.5
    ) -> str:
        prompt = f"Complete the following text:\n\n{text_before_cursor}"
        return self.generate_text(
            prompt, max_tokens=max_tokens, temperature=temperature
        ).strip()


class GoogleAIClient(LLMInterface):
    def __init__(self, model_tier: str = "small"):
        super().__init__(model_tier)
        self.client = None
        if GOOGLE_API_KEY == _google_api_key_default:
            logging.warning(
                "GoogleAIClient: Cannot initialize, API key is missing or placeholder."
            )
        else:
            try:
                genai.configure(api_key=GOOGLE_API_KEY)
                self.client = genai.GenerativeModel(self.model_name)
                logging.info(
                    f"Google AI client initialized with model: {self.model_name}"
                )
            except Exception as e:
                st.error(
                    f"Failed to initialize Google AI client with provided key: {e}"
                )
                logging.error("Failed to initialize Google AI client.")

    def _get_model_name(self, tier: str) -> str:
        return GOOGLE_MODELS.get(tier, GOOGLE_MODELS["small"])

    def generate_text(
        self, prompt: str, max_tokens: int = 500, temperature: float = 0.7
    ) -> str:
        if not self.client:
            return "Error: Google AI client not initialized (API key missing or invalid)."
        try:
            generation_config = genai.types.GenerationConfig(
                max_output_tokens=max_tokens, temperature=temperature
            )
            response = self.client.generate_content(
                prompt, generation_config=generation_config
            )
            if response.parts:
                return "".join(part.text for part in response.parts)
            if (
                hasattr(response, "prompt_feedback")
                and response.prompt_feedback.block_reason
            ):
                return f"Error: Content generation blocked due to {response.prompt_feedback.block_reason.name}."
            return "Error: Received an empty or unexpected response from Google AI."
        except Exception as e:
            st.error(f"Google AI API Error: {e}")
            logging.error("Google AI API request failed.")
            return f"Error: Google AI API call failed. {e}"

    def generate_json(
        self,
        prompt: str,
        json_schema: dict,
        max_tokens: int = 300,
        temperature: float = 0.5,
    ) -> dict:
        if not self.client:
            return {
                "error": "Google AI client not initialized (API key missing or invalid)."
            }
        try:
            generation_config = genai.types.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=max_tokens,
                temperature=temperature,
            )
            schema_string = json.dumps(json_schema)
            full_prompt = f"{prompt}\n\nGenerate a JSON object matching this schema:\n{schema_string}\n\nJSON output:"
            response = self.client.generate_content(
                full_prompt, generation_config=generation_config
            )
            if response.parts:
                response_text = "".join(part.text for part in response.parts)
                try:
                    if response_text.strip().startswith("```json"):
                        response_text = response_text.strip()[7:-3].strip()
                    elif response_text.strip().startswith("```"):
                        response_text = response_text.strip()[3:-3].strip()
                    return json.loads(response_text)
                except json.JSONDecodeError as json_e:
                    return {
                        "error": "Invalid JSON response",
                        "raw_content": response_text,
                        "decode_error": str(json_e),
                    }
            if (
                hasattr(response, "prompt_feedback")
                and response.prompt_feedback.block_reason
            ):
                return {
                    "error": f"Content generation blocked due to {response.prompt_feedback.block_reason.name}."
                }
            return {
                "error": "Received an empty or unexpected JSON response from Google AI."
            }
        except Exception as e:
            st.error(f"Google AI API JSON Error: {e}")
            logging.error("Google AI API JSON request failed.")
            return {"error": f"Google AI API call failed: {str(e)}"}


class LMStudioClient(LLMInterface):
    def __init__(self, model_tier: str = "small"):
        super().__init__(model_tier)
        self.api_url = f"{LM_STUDIO_URL}"
        logging.info("LM Studio client configured.")

    def _get_model_name(self, tier: str) -> str:
        return LM_STUDIO_MODELS.get(tier, LM_STUDIO_MODELS["small"])

    def _call_lm_studio(self, payload: dict) -> dict:
        try:
            response = requests.post(
                self.api_url, headers=LM_STUDIO_HEADERS, json=payload, timeout=180
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            st.error(
                f"LM Studio connection error: {e}. Is it running at {self.api_url}?"
            )
            logging.error("LM Studio connection failed.")
            return {"error": f"LM Studio connection failed: {e}"}
        except Exception as e:
            st.error(f"Error during LM Studio call: {e}")
            logging.error("LM Studio request failed.")
            return {"error": f"An unexpected error occurred: {e}"}

    def generate_text(
        self, prompt: str, max_tokens: int = 500, temperature: float = 0.7
    ) -> str:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        result = self._call_lm_studio(payload)
        if "error" in result:
            return f"Error: {result['error']}"
        try:
            return result["choices"][0]["message"]["content"].strip()
        except (IndexError, KeyError, TypeError):
            logging.error("Unexpected LM Studio response format.")
            return "Error: Unexpected response format from LM Studio."

    def generate_json(
        self,
        prompt: str,
        json_schema: dict,
        max_tokens: int = 300,
        temperature: float = 0.5,
    ) -> dict:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        schema_string = json.dumps(json_schema)
        payload["messages"][0][
            "content"
        ] = f"{prompt}\n\nGenerate JSON matching:\n{schema_string}\n\nJSON output:"
        result = self._call_lm_studio(payload)
        if "error" in result:
            return {"error": result["error"]}
        try:
            content = result["choices"][0]["message"]["content"]
            if content.strip().startswith("```json"):
                content = content.strip()[7:-3].strip()
            elif content.strip().startswith("```"):
                content = content.strip()[3:-3].strip()
            return json.loads(content)
        except (IndexError, KeyError, TypeError):
            logging.error("Unexpected LM Studio JSON response format.")
            return {"error": "Unexpected JSON response format from LM Studio."}
        except json.JSONDecodeError as json_e:
            return {
                "error": "Invalid JSON response",
                "raw_content": content,
                "decode_error": str(json_e),
            }


@st.cache_resource
def get_llm_client(model_tier: str = "small") -> LLMInterface | None:
    backend = st.session_state.get("llm_backend", ACTIVE_LLM_BACKEND)
    logging.info(f"Getting LLM client for backend: {backend}, tier: {model_tier}")
    if backend == "google":
        client = GoogleAIClient(model_tier=model_tier)
        return client if client.client else None
    elif backend == "lm_studio":
        return LMStudioClient(model_tier=model_tier)
    else:
        st.error(f"Unsupported LLM backend configured: {backend}")
        return None
