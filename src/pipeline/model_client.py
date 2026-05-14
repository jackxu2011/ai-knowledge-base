"""Unified async LLM client with support for DeepSeek, Qwen, and OpenAI providers.

Environment variables:
    LLM_PROVIDER: Model provider ("deepseek", "qwen", "openai"). Defaults to "deepseek".
    DEEPSEEK_API_KEY: API key for DeepSeek.
    QWEN_API_KEY: API key for Qwen.
    OPENAI_API_KEY: API key for OpenAI.

Examples:
    >>> client = create_llm_client()
    >>> response = await client.chat([{"role": "user", "content": "Hello"}])
    >>> print(response.content)
"""

import asyncio
import json
import logging
import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openai

from constants.llm import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    MODEL_TOKEN_PRICES,
    PROVIDER_BASE_URLS,
    PROVIDER_DEEPSEEK,
    PROVIDER_ENV_VARS,
    PROVIDER_MODEL_ENVVARS,
    PROVIDER_BASE_URL_ENVVARS,
    PROVIDER_MODELS,
    PROVIDER_OPENAI,
    PROVIDERS,
)

logger = logging.getLogger(__name__)

# ── LLM config ───────────────────────────────────────────────────────────────
LLM_CONFIG_PATH = Path("config/llm.json")


def load_llm_config() -> dict[str, Any]:
    """Load per-provider LLM configuration from ``config/llm.json``.

    Returns:
        Dict keyed by provider name (e.g. ``"qwen"``, ``"deepseek"``),
        each containing ``extra_body`` and other provider-specific settings.
        Returns an empty dict if the config file is missing or unreadable.
    """
    try:
        if not LLM_CONFIG_PATH.exists():
            logger.info(
                "LLM config not found at %s, using defaults (no extra_body)",
                LLM_CONFIG_PATH,
            )
            return {}

        with open(LLM_CONFIG_PATH) as f:
            config = json.load(f)
            if not isinstance(config, dict):
                logger.warning("LLM config must be a dict, ignoring")
                return {}
            return config
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Failed to load LLM config from %s: %s. Using defaults.",
            LLM_CONFIG_PATH,
            exc,
        )
        return {}


_RETRIABLE_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)

_client_cache: dict[str, Any] = {}


def get_economical_input_limit(model: str) -> int | None:
    """Get the economical (standard-pricing) input token limit for a model.

    Returns the ``max_input_tokens`` of the first (cheapest) pricing tier.
    Input tokens beyond this limit trigger higher per-token rates.

    Args:
        model: LLM model name.

    Returns:
        Economical input token limit, or None if model is unknown.
    """
    tiers = MODEL_TOKEN_PRICES.get(model)
    if not tiers:
        return None
    return tiers[0]["max_input_tokens"]


@dataclass
class Usage:
    """LLM token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def total_cost_cny(self, model: str) -> float:
        """Calculate cost in CNY (人民币) based on model pricing.

        Automatically selects the correct price tier based on
        ``prompt_tokens``. Qwen models charge 4x more when input
        exceeds 256K tokens.

        Args:
            model: LLM model name.

        Returns:
            Total cost in CNY.

        Raises:
            ValueError: If model is unknown.
        """
        if model not in MODEL_TOKEN_PRICES:
            raise ValueError(f"Unknown model '{model}', cannot calculate cost")

        tiers = MODEL_TOKEN_PRICES[model]
        applicable_tier = tiers[0]
        for tier in tiers:
            if self.prompt_tokens <= tier["max_input_tokens"]:
                applicable_tier = tier
                break
            applicable_tier = tier

        return (
            self.prompt_tokens * applicable_tier["input"] / 1000
            + self.completion_tokens * applicable_tier["output"] / 1000
        )


@dataclass
class LLMResponse:
    """Standardized LLM response container."""

    content: str
    usage: Usage = field(default_factory=Usage)
    provider: str = PROVIDER_DEEPSEEK
    model: str = ""
    raw_response: Any = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        """Send chat request to LLM.

        Args:
            messages: List of message dicts with "role" and "content".
            **kwargs: Additional provider-specific arguments.

        Returns:
            LLMResponse with content and usage.
        """

    async def chat_with_retry(
        self, messages: list[dict[str, str]], max_retries: int = 3, **kwargs: Any
    ) -> LLMResponse:
        """Send chat request with exponential backoff retry and jitter.

        Only retries on retriable errors (rate limits, timeouts, connection
        errors, internal server errors). Other errors propagate immediately.

        Args:
            messages: List of message dicts with "role" and "content".
            max_retries: Maximum number of retry attempts.
            **kwargs: Additional arguments (temperature, max_tokens, etc.).

        Returns:
            LLMResponse with content and usage.

        Raises:
            Exception: The last retriable error if all retries exhausted,
                or the original non-retriable error.
        """
        last_error: Exception | None = None

        for attempt in range(max_retries):
            try:
                return await self.chat(messages, **kwargs)
            except _RETRIABLE_EXCEPTIONS as e:
                last_error = e
                wait_time = 2**attempt + random.uniform(0, 1)
                logger.warning(
                    "LLM request failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    wait_time,
                    str(e),
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)
            except Exception:
                raise

        logger.error(
            "LLM request failed after %d attempts: %s", max_retries, last_error
        )
        raise last_error or RuntimeError(
            f"LLM request failed after {max_retries} retries"
        )


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible LLM provider using the async OpenAI SDK."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider_name: str = PROVIDER_OPENAI,
        timeout: float = 60.0,
        extra_body: dict[str, Any] | None = None,
    ):
        """Initialize OpenAI-compatible provider.

        Args:
            api_key: API key for authentication.
            base_url: Base URL of the API endpoint.
            model: Model name to use.
            provider_name: Provider identifier for pricing.
            timeout: Request timeout in seconds.
            extra_body: Additional provider-specific parameters merged into
                each API call (e.g. ``{"thinking": {"type": "off"}}`` for
                Qwen to disable thinking mode).
        """
        self._client = openai.AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout
        )
        self._model = model
        self._provider = provider_name
        self._extra_body = extra_body or {}

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> LLMResponse:
        """Send chat request to LLM.

        Args:
            messages: List of message dicts with "role" and "content".
            **kwargs: Additional arguments (temperature, max_tokens, etc.).

        Returns:
            LLMResponse with content and usage.
        """
        max_tokens = kwargs.pop("max_tokens", DEFAULT_MAX_OUTPUT_TOKENS)

        # Merge per-call kwargs with provider-level extra_body.
        call_extra_body = kwargs.pop("extra_body", None) or {}
        merged_extra_body = {**self._extra_body, **call_extra_body}

        create_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            **kwargs,
        }
        if merged_extra_body:
            create_kwargs["extra_body"] = merged_extra_body

        response = await self._client.chat.completions.create(**create_kwargs)

        usage = Usage()
        if response.usage is not None:
            usage = Usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )

        content = ""
        if response.choices:
            content = response.choices[0].message.content or ""

        economical_limit = get_economical_input_limit(self._model)
        if economical_limit and usage.prompt_tokens > economical_limit:
            logger.warning(
                "Input tokens (%d) exceed economical limit (%d) for %s, "
                "higher pricing tier applied — cost may be significantly higher",
                usage.prompt_tokens,
                economical_limit,
                self._model,
            )

        return LLMResponse(
            content=content,
            usage=usage,
            provider=self._provider,
            model=self._model,
            raw_response=response,
        )


def _get_api_key(provider: str) -> str:
    """Retrieve API key from environment variable.

    Args:
        provider: Provider name.

    Returns:
        API key string.

    Raises:
        ValueError: If provider is unknown or API key is not set.
    """
    if provider not in PROVIDER_ENV_VARS:
        raise ValueError(f"Unknown provider '{provider}', expected one of: {PROVIDERS}")
    env_var = PROVIDER_ENV_VARS[provider]
    api_key = os.environ.get(env_var, "")
    if not api_key:
        raise ValueError(f"{env_var} environment variable is not set")
    return api_key


def create_llm_client(provider: str | None = None) -> LLMProvider:
    """Create an LLM client for the specified provider.

    Uses a per-provider cache to reuse client instances across calls.

    Args:
        provider: Provider name ("deepseek", "qwen", "openai").
            Defaults to LLM_PROVIDER env var or "deepseek".

    Returns:
        Configured LLMProvider instance.

    Raises:
        ValueError: If provider is unknown or API key is missing.
    """
    provider = provider or os.environ.get("LLM_PROVIDER", PROVIDER_DEEPSEEK).lower()

    if provider not in PROVIDER_BASE_URLS:
        raise ValueError(f"Unknown provider '{provider}', expected one of: {PROVIDERS}")

    if provider in _client_cache:
        return _client_cache[provider]

    api_key = _get_api_key(provider)

    llm_config = load_llm_config()
    provider_config = llm_config.get(provider, {})
    extra_body = provider_config.get("extra_body")

    # Environment variable overrides — model and base_url take precedence
    # over both the constants table and config/llm.json.
    model = (
        os.environ.get(PROVIDER_MODEL_ENVVARS[provider]) or PROVIDER_MODELS[provider]
    )
    base_url = (
        os.environ.get(PROVIDER_BASE_URL_ENVVARS[provider])
        or PROVIDER_BASE_URLS[provider]
    )

    # Environment variable override for thinking mode.
    # ENABLE_THINKING=true  → enable_thinking=true (模型输出思考过程)
    # ENABLE_THINKING=false → enable_thinking=false (默认，节省 token)
    # 不设置 → 使用 config/llm.json 中的配置
    enable_thinking_env = os.environ.get("ENABLE_THINKING")
    if enable_thinking_env is not None:
        if extra_body is None:
            extra_body = {}
        extra_body["enable_thinking"] = enable_thinking_env.lower() == "true"

    client = OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider_name=provider,
        timeout=60.0,
        extra_body=extra_body,
    )
    _client_cache[provider] = client
    return client


def estimate_tokens(text: str, provider: str = PROVIDER_DEEPSEEK) -> int:
    """Estimate token count for text using simple heuristic.

    Args:
        text: Input text.
        provider: Provider name (affects calculation).

    Returns:
        Estimated token count.
    """
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if ord(c) > 127)
    ascii_words = len(text.split())
    return int(chinese_chars * 2 + ascii_words * 0.75)


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    model: str = PROVIDER_MODELS[PROVIDER_DEEPSEEK],
) -> float:
    """Calculate LLM cost in CNY (人民币).

    Args:
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        model: Model name for pricing lookup.

    Returns:
        Total cost in CNY.

    Raises:
        ValueError: If model is unknown.
    """
    usage = Usage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
    return usage.total_cost_cny(model)


async def quick_chat(
    prompt: str,
    system_prompt: str | None = None,
    provider: str | None = None,
    **kwargs: Any,
) -> LLMResponse:
    """Convenience function for a single LLM call.

    Args:
        prompt: User prompt.
        system_prompt: Optional system prompt.
        provider: LLM provider override.
        **kwargs: Additional arguments passed to chat.

    Returns:
        LLMResponse with content and usage.
    """
    client = create_llm_client(provider)

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    return await client.chat_with_retry(messages, max_retries=3, **kwargs)


if __name__ == "__main__":
    import asyncio as _asyncio
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("Testing LLM client creation")
    try:
        client = create_llm_client()
        logger.info("Client created successfully for provider: %s", client._provider)
    except ValueError as e:
        logger.error("Failed to create client: %s", e)
        exit(1)

    logger.info("Testing quick_chat with a simple prompt")
    try:
        response = _asyncio.run(
            quick_chat("Say 'Hello, World!' in exactly those words.")
        )
        logger.info(
            "Response received - tokens: %d, cost: ¥%.6f",
            response.usage.total_tokens,
            response.usage.total_cost_cny(response.model),
        )
        logger.info(
            "Result: %s",
            json.dumps(
                {
                    "content": response.content,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                    "cost_cny": response.usage.total_cost_cny(response.model),
                    "provider": response.provider,
                    "model": response.model,
                },
                ensure_ascii=False,
            ),
        )
    except Exception as e:
        logger.error("LLM request failed: %s", e)
        exit(1)
