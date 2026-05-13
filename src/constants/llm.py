"""Constants for LLM provider configuration, pricing, and model limits.

Key concepts (do not confuse):
    - ``max_input_tokens`` in pricing tiers: the threshold of **input tokens
      in a single request** that determines which price tier applies. This is
      a billing concept, not a model capability.
    - ``MODEL_MAX_CONTEXT_WINDOWS``: the technical upper limit on the total
      tokens (input + output) a model can process in one request.

All prices are in CNY (人民币) per 1K tokens, sourced from official
pricing pages as of May 2026. DeepSeek V4-Pro prices reflect the 2.5折
discount valid until 2026-05-31. OpenAI prices converted from USD at
1 USD = 7.2 CNY.

Qwen models use tiered pricing: the per-token rate depends on the total
input tokens in a single request. MODEL_TOKEN_PRICES stores price tiers
as a list ordered by ascending ``max_input_tokens``. The applicable tier
is the one whose ``max_input_tokens`` is the smallest value >= the actual
input token count.

References:
    DeepSeek: https://api-docs.deepseek.com/zh-cn/quick_start/pricing
    Qwen: https://help.aliyun.com/zh/model-studio/model-pricing
    OpenAI: https://openai.com/api/pricing/
"""

from typing import Any

PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_QWEN = "qwen"
PROVIDER_OPENAI = "openai"

PROVIDERS = [PROVIDER_DEEPSEEK, PROVIDER_QWEN, PROVIDER_OPENAI]

PROVIDER_BASE_URLS = {
    PROVIDER_DEEPSEEK: "https://api.deepseek.com/v1",
    PROVIDER_QWEN: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    PROVIDER_OPENAI: "https://api.openai.com/v1",
}

PROVIDER_MODELS = {
    PROVIDER_DEEPSEEK: "deepseek-v4-flash",
    PROVIDER_QWEN: "qwen3.6-plus",
    PROVIDER_OPENAI: "gpt-4o-mini",
}

PROVIDER_ENV_VARS = {
    PROVIDER_DEEPSEEK: "DEEPSEEK_API_KEY",
    PROVIDER_QWEN: "QWEN_API_KEY",
    PROVIDER_OPENAI: "OPENAI_API_KEY",
}

MODEL_TOKEN_PRICES: dict[str, list[dict[str, Any]]] = {
    "deepseek-v4-flash": [
        {"max_input_tokens": 1000000, "input": 0.001, "output": 0.002},
    ],
    "deepseek-v4-pro": [
        {"max_input_tokens": 1000000, "input": 0.003, "output": 0.006},
    ],
    "qwen3.6-flash": [
        {"max_input_tokens": 256000, "input": 0.0012, "output": 0.0072},
        {"max_input_tokens": 1000000, "input": 0.0048, "output": 0.0288},
    ],
    "qwen3.6-plus": [
        {"max_input_tokens": 256000, "input": 0.002, "output": 0.012},
        {"max_input_tokens": 1000000, "input": 0.008, "output": 0.048},
    ],
    "qwen3.6-max-preview": [
        {"max_input_tokens": 128000, "input": 0.009, "output": 0.054},
        {"max_input_tokens": 256000, "input": 0.015, "output": 0.090},
    ],
    "gpt-4o-mini": [
        {"max_input_tokens": 128000, "input": 0.00108, "output": 0.00432},
    ],
    "gpt-4o": [
        {"max_input_tokens": 128000, "input": 0.018, "output": 0.072},
    ],
    "gpt-4.1": [
        {"max_input_tokens": 1048576, "input": 0.01440, "output": 0.05760},
    ],
    "gpt-4.1-mini": [
        {"max_input_tokens": 1048576, "input": 0.00288, "output": 0.01152},
    ],
}

MODEL_MAX_CONTEXT_WINDOWS = {
    "deepseek-v4-flash": 1000000,
    "deepseek-v4-pro": 1000000,
    "qwen3.6-flash": 1000000,
    "qwen3.6-plus": 1000000,
    "qwen3.6-max-preview": 256000,
    "gpt-4o-mini": 128000,
    "gpt-4o": 128000,
    "gpt-4.1": 1048576,
    "gpt-4.1-mini": 1048576,
}

DEFAULT_MAX_OUTPUT_TOKENS = 4096
