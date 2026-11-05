"""Provider-agnostic LLM access via LiteLLM, configured by LLM_MODEL / LLM_API_KEY / LLM_API_BASE in config/.env."""

from __future__ import annotations

import json
import os
import re

REQUEST_TIMEOUT_S = 60


class LLMClient:
    def __init__(self, model: str | None = None, api_key: str | None = None, api_base: str | None = None):
        self.model = model or os.getenv("LLM_MODEL") or None
        self.api_key = api_key or os.getenv("LLM_API_KEY") or None
        self.api_base = api_base or os.getenv("LLM_API_BASE") or None  # None = provider's default URL
        # LLM_THINKING=false turns off reasoning on open models served via OpenAI-compatible APIs
        # (NVIDIA, vLLM, ...). Unset = model default; don't set it for Anthropic/OpenAI models.
        self.thinking = os.getenv("LLM_THINKING", "").strip().lower() or None

    @property
    def enabled(self) -> bool:
        return self.model is not None

    async def complete_json(self, system: str, user: str) -> dict:
        """Ask for a JSON object and parse it out of the reply."""
        import litellm  # slow import; only paid when an LLM is configured

        litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" banner on every error

        extra = {}
        if self.thinking in ("false", "off", "0", "no"):
            extra["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        response = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
            api_key=self.api_key,
            api_base=self.api_base,
            timeout=REQUEST_TIMEOUT_S,
            num_retries=0,  # a stalled model shouldn't hold up every job; callers fall back on failure
            **extra,
        )
        return parse_json_object(response.choices[0].message.content)


def parse_json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object in LLM reply: {text[:200]!r}")
    return json.loads(match.group(0))
