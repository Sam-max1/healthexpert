"""Local LLM wrapper — wraps the local completions endpoint as a LangChain LLM."""
from __future__ import annotations
from typing import Any, Optional
import requests, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

from crewai.llms.base_llm import BaseLLM
from pydantic import Field


class LocalLLM(BaseLLM):
    """CrewAI-compatible wrapper for the local OpenAI-compatible completions endpoint."""

    model:       str   = Field(default_factory=lambda: config.LLM_MODEL_ID)
    base_url:    str   = Field(default_factory=lambda: config.LLM_BASE_URL)
    max_tokens:  int   = Field(default_factory=lambda: config.LLM_MAX_TOKENS)
    temperature: float = Field(default_factory=lambda: config.LLM_TEMPERATURE)
    top_p:       float = Field(default_factory=lambda: config.LLM_TOP_P)
    timeout:     int   = Field(default_factory=lambda: config.LLM_TIMEOUT)

    use_kv_cache: bool = Field(default=False)

    def call(self, messages: list[dict], callbacks: list[Any] | None = None, **kwargs: Any) -> str:
        # Flatten conversation history into a single string since gen_llm.py wraps it in a user role
        prompt = "\n".join([f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages])
        
        payload = {
            "model_id":    self.model,
            "prompt":      prompt,
            "max_tokens":  self.max_tokens,
            "temperature": self.temperature,
            "top_p":       self.top_p,
            "use_kv_cache": self.use_kv_cache,
            "attachments": [],
        }
        try:
            resp = requests.post(
                f"{self.base_url}/v1/completions",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["text"].strip()
        except requests.exceptions.ConnectionError:
            return "[LLM ERROR] Cannot connect to local endpoint. Is the AMD/Nvidia server running?"
        except Exception as e:
            return f"[LLM ERROR] {e}"

    def supports_function_calling(self) -> bool:
        return False

    def supports_stop_words(self) -> bool:
        return False


# Singleton instance
_llm_instance: LocalLLM | None = None

def get_llm() -> LocalLLM:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LocalLLM(model=config.LLM_MODEL_ID)
    return _llm_instance
