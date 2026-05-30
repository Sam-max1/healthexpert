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

    def __init__(self, **kwargs):
        kwargs.setdefault("model", config.LLM_MODEL_ID)
        kwargs.setdefault("base_url", config.LLM_BASE_URL)
        super().__init__(**kwargs)
        self.max_tokens = kwargs.get("max_tokens", config.LLM_MAX_TOKENS)
        self.temperature = kwargs.get("temperature", config.LLM_TEMPERATURE)
        self.top_p = kwargs.get("top_p", config.LLM_TOP_P)
        self.timeout = kwargs.get("timeout", config.LLM_TIMEOUT)
        self.use_kv_cache = kwargs.get("use_kv_cache", False)

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
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
            usage = data.get("usage")
            if usage:
                self._track_token_usage_internal(usage)
            return data["choices"][0]["text"].strip()
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
