"""Pipeline orchestrator — 4-step knowledge base automation.

Collect → Analyze → Organize → Save

Steps:
  1. **Collect** — fetch AI/LLM/Agent repos from GitHub Search API
     and AI-related items from RSS/Atom feeds.
  2. **Analyze** — invoke LLM to summarise, score, tag each item.
  3. **Organize** — deduplicate by URL, standardise format, validate schema.
  4. **Save** — persist individual JSON articles to ``knowledge/articles/``
     and batch raw data to ``knowledge/raw/``.

Configuration:
    ``config/rss_sources.json``  — RSS/Atom feed list, AI keywords, timeout
                               (fallback to sensible defaults if absent).

Environment variables:
    GITHUB_TOKEN       — GitHub personal access token (optional, ups rate limit).
    LLM_PROVIDER       — LLM provider ("deepseek", "qwen", "openai").
    DEEPSEEK_API_KEY   — DeepSeek API key.
    QWEN_API_KEY       — Qwen API key.
    OPENAI_API_KEY     — OpenAI API key.

CLI examples:
    ai-knowledge-base --sources github,rss --limit 20
    ai-knowledge-base --sources github --limit 5
    ai-knowledge-base --sources rss --limit 10 --dry-run
    ai-knowledge-base --verbose

    # Or run directly:
    uv run src/pipeline/pipeline.py --sources github,rss --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from constants.analyzers import DEFAULT_MIN_SCORE

try:
    # Absolute import — works when the package is installed or
    # when run via ``python -m pipeline.pipeline``.
    from pipeline.model_client import (
        DEFAULT_MAX_OUTPUT_TOKENS,
        LLMResponse,
        Usage,
        create_llm_client,
    )
except (ImportError, ModuleNotFoundError):
    # Relative fallback — works when the file is run directly
    # as ``__main__`` (e.g. ``python src/pipeline/pipeline.py``).
    from model_client import (  # type: ignore[no-redef]
        DEFAULT_MAX_OUTPUT_TOKENS,
        LLMResponse,
        Usage,
        create_llm_client,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

RAW_DIR = Path("knowledge/raw")
ARTICLES_DIR = Path("knowledge/articles")
DEFAULT_LIMIT = 20

# ── GitHub Search ──────────────────────────────────────────────────────────
GITHUB_API_BASE = "https://api.github.com"
GITHUB_SEARCH_REPOS = f"{GITHUB_API_BASE}/search/repositories"
GITHUB_TIMEOUT = 30.0

# Topic keywords used with the ``topic:`` qualifier (OR logic).
GITHUB_TOPIC_KEYWORDS = [
    "ai",
    "llm",
    "agent",
    "machine-learning",
    "artificial-intelligence",
]

# ── RSS / Atom feeds ──────────────────────────────────────────────────────
SOURCES_CONFIG_PATH = Path("config/rss_sources.json")


def _default_rss_config() -> dict[str, Any]:
    """Return a sensible default RSS configuration.

    Used as a fallback when ``config/rss_sources.json`` does not exist.
    """
    return {
        "timeout": 30.0,
        "scan_limit": 50,
        "feeds": [
            {
                "name": "Hacker News Best",
                "url": "https://news.ycombinator.com/rss",
                "category": "通用技术",
                "enabled": True,
                "ai_keyword_filter": True,
            },
            {
                "name": "Lobsters AI/ML",
                "url": "https://lobste.rs/tag/ai.rss",
                "category": "AI 行业",
                "enabled": True,
                "ai_keyword_filter": False,
            },
            {
                "name": "arXiv cs.AI",
                "url": "https://rss.arxiv.org/rss/cs.AI",
                "category": "学术研究",
                "enabled": False,
                "ai_keyword_filter": False,
            },
            {
                "name": "OpenAI Blog",
                "url": "https://openai.com/feed.xml",
                "category": "AI 行业",
                "enabled": True,
                "ai_keyword_filter": False,
            },
            {
                "name": "Anthropic Research",
                "url": "https://www.anthropic.com/feed.xml",
                "category": "AI 行业",
                "enabled": True,
                "ai_keyword_filter": False,
            },
            {
                "name": "Hugging Face Blog",
                "url": "https://huggingface.co/blog/feed.xml",
                "category": "AI 行业",
                "enabled": True,
                "ai_keyword_filter": False,
            },
            {
                "name": "机器之心",
                "url": "https://jiqizhixin.com/rss",
                "category": "AI 行业",
                "enabled": False,
                "ai_keyword_filter": False,
            },
            {
                "name": "量子位",
                "url": "https://www.qbitai.com/feed/",
                "category": "AI 行业",
                "enabled": False,
                "ai_keyword_filter": False,
            },
        ],
        "ai_keywords": [
            "ai",
            "llm",
            "agent",
            "gpt",
            "machine learning",
            "deep learning",
            "neural",
            "transformer",
            "rag",
            "langchain",
            "generative",
            "artificial intelligence",
            "openai",
            "claude",
            "gemini",
            "llama",
            "mistral",
            "qwen",
            "deepseek",
        ],
    }


def load_rss_config() -> dict[str, Any]:
    """Load RSS source configuration from ``config/rss_sources.json``.

    Returns:
        RSS configuration dict with keys: ``timeout``, ``scan_limit``,
        ``feeds`` (list of ``{"name", "url", "category", "enabled"}``), and
        ``ai_keywords``.

    Falls back to :func:`_default_rss_config` if the config file is
    missing, unreadable, or lacks the ``"rss"`` section.
    """
    try:
        if not SOURCES_CONFIG_PATH.exists():
            logger.info(
                "Sources config not found at %s, using defaults",
                SOURCES_CONFIG_PATH,
            )
            return _default_rss_config()

        with open(SOURCES_CONFIG_PATH) as f:
            config = json.load(f)

        rss_cfg = config.get("rss")
        if rss_cfg is None:
            logger.warning(
                "No 'rss' section in %s, using defaults",
                SOURCES_CONFIG_PATH,
            )
            return _default_rss_config()

        return rss_cfg
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "Failed to load sources config from %s: %s. Using defaults.",
            SOURCES_CONFIG_PATH,
            exc,
        )
        return _default_rss_config()

# ── LLM analysis prompt ───────────────────────────────────────────────────
ANALYSIS_SYSTEM_PROMPT = (
    "你是一个 AI 技术动态分析师。请对以下技术内容进行分析，"
    "返回**纯 JSON**（不要 markdown 代码块，不要其他文字）。\n\n"
    "输出格式：\n"
    "{\n"
    '  "summary": "中文摘要（50-200字）",\n'
    '  "score": <1-10>,\n'
    '  "scoring_reasons": "评分原因说明",\n'
    '  "scoring_dimensions": {\n'
    '    "tech_depth": <0-10>,\n'
    '    "innovation": <0-10>,\n'
    '    "usability": <0-10>\n'
    "  },\n"
    '  "technical_highlights": ["亮点1", "亮点2"],\n'
    '  "tags": ["标签1", "标签2", "标签3"]\n'
    "}\n\n"
    "评分标准：\n"
    "- 9-10 分：改变格局 — 突破性技术、范式转变、引领行业趋势\n"
    "- 7-8 分：直接有帮助 — 实用性强、直接提升工作效率或生活质量\n"
    "- 5-6 分：值得了解 — 有趣但非必需，了解有益\n"
    "- 1-4 分：可略过 — 重复性高、适用面窄或价值不明显"
)

# Fields that must be present in a valid article.
_ARTICLE_REQUIRED_FIELDS = [
    "id",
    "title",
    "source_url",
    "source_type",
    "summary",
    "status",
    "metadata",
    "analysis",
    "tags",
]

_ANALYSIS_REQUIRED_FIELDS = [
    "score",
    "scoring_reasons",
    "scoring_dimensions",
    "technical_highlights",
]

_SCORING_DIMENSIONS_FIELDS = ["tech_depth", "innovation", "usability"]

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    """Return today's date string in YYYY-MM-DD format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _today_compact() -> str:
    """Return today's date in compact YYYYMMDD format (for IDs)."""
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _generate_id(source: str, seq: int) -> str:
    """Generate a unique item ID.

    Args:
        source: Source name (e.g. ``"github"``, ``"rss"``).
        seq: 1-based sequence number.

    Returns:
        ID in the format ``{source}-{YYYYMMDD}-{seq:03d}``.
    """
    return f"{source}-{_today_compact()}-{seq:03d}"


# ── XML / RSS helpers (regex-based) ─────────────────────────────────────────


def _extract_xml_tag(xml: str, tag: str) -> str | None:
    """Extract text content of a simple XML tag using regex.

    Strips any nested HTML tags from the result.

    Args:
        xml: XML/HTML snippet to search.
        tag: Tag name (without angle brackets).

    Returns:
        Cleaned text content, or ``None`` if not found.
    """
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, re.DOTALL)
    if m:
        text = m.group(1).strip()
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
    return None


def _extract_link_href(xml: str) -> str | None:
    """Extract ``href`` attribute from a ``<link>`` element (Atom format).

    Args:
        xml: XML/HTML snippet to search.

    Returns:
        URL string, or ``None``.
    """
    m = re.search(r'<link[^>]+href\s*=\s*["\']([^"\']+)["\']', xml)
    if m:
        return m.group(1).strip()
    return None


def _extract_link_text(xml: str) -> str | None:
    """Extract text content of a ``<link>`` element (RSS format).

    Args:
        xml: XML/HTML snippet to search.

    Returns:
        URL string, or ``None``.
    """
    m = re.search(r"<link[^>]*>(.*?)</link>", xml, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def _is_ai_relevant(text: str, keywords: list[str]) -> bool:
    """Check whether *text* contains any AI/LLM-related keyword.

    Args:
        text: Text to test.
        keywords: List of substrings to search for (case-insensitive).

    Returns:
        ``True`` if at least one keyword matches (case-insensitive).
    """
    lower = text.lower()
    return any(kw in lower for kw in keywords)


# ── JSON extraction (from LLM responses) ────────────────────────────────────


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract a JSON object from raw LLM text.

    Handles responses wrapped in `` ```json ... ``` `` fences as well as
    bare JSON.

    Args:
        text: Raw LLM response.

    Returns:
        Parsed dict, or ``None`` if no valid JSON was found.
    """
    text = text.strip()

    # Try extracting from markdown code fence first.
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: find the first { … } block.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


# ---------------------------------------------------------------------------
# Step 1 — Collect
# ---------------------------------------------------------------------------


async def collect_github(limit: int, dry_run: bool = False) -> list[dict[str, Any]]:
    """Collect AI/LLM/Agent repos from the GitHub Search API.

    Args:
        limit: Maximum number of results to return.
        dry_run: If ``True``, log intent without making HTTP calls.

    Returns:
        List of raw item dicts (internal pipeline format).
    """
    if limit <= 0:
        return []

    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token and not token.startswith("your-"):
        headers["Authorization"] = f"Bearer {token}"
    elif token:
        logger.warning(
            "GITHUB_TOKEN looks like a placeholder ('%s…'); "
            "making unauthenticated request (rate-limited to 10 req/min)",
            token[:10],
        )

    topics_query = "+".join(f"topic:{kw}" for kw in GITHUB_TOPIC_KEYWORDS)
    url = f"{GITHUB_SEARCH_REPOS}?q={topics_query}&sort=stars&order=desc&per_page={limit}"

    logger.info("GitHub Search API: q=%s sort=stars per_page=%d", topics_query, limit)

    if dry_run:
        logger.info("[DRY-RUN] Would fetch %s", url)
        # Generate placeholder items so downstream steps can be verified.
        date_str = _today_str()
        return [
            {
                "id": _generate_id("github", i + 1),
                "title": f"owner/repo-{i + 1}",
                "source_url": f"https://github.com/owner/repo-{i + 1}",
                "source": "github",
                "collected_date": date_str,
                "description": f"Placeholder description for repo-{i + 1}",
                "summary": "",
                "metadata": {
                    "stars": 1000,
                    "author": "owner",
                    "topics": ["ai", "llm"],
                    "language": "Python",
                    "fork": False,
                },
            }
            for i in range(min(limit, 3))  # cap placeholders at 3
        ]

    async with httpx.AsyncClient(timeout=GITHUB_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("GitHub API HTTP error: %s", e)
            return []
        except httpx.RequestError as e:
            logger.error("GitHub API request failed: %s", e)
            return []

    date_str = _today_str()
    items: list[dict[str, Any]] = []
    repos = data.get("items", [])[:limit]

    for i, repo in enumerate(repos):
        items.append(
            {
                "id": _generate_id("github", i + 1),
                "title": repo.get("full_name", ""),
                "source_url": repo.get("html_url", ""),
                "source": "github",
                "collected_date": date_str,
                "description": repo.get("description") or "",
                "summary": "",
                "metadata": {
                    "stars": repo.get("stargazers_count", 0),
                    "author": (
                        repo.get("owner", {}).get("login", "")
                        if repo.get("owner")
                        else ""
                    ),
                    "topics": repo.get("topics", []),
                    "language": repo.get("language"),
                    "fork": repo.get("fork", False),
                },
            }
        )

    logger.info("Collected %d GitHub items", len(items))
    return items


async def collect_rss(limit: int, dry_run: bool = False) -> list[dict[str, Any]]:
    """Collect AI-related items from configured RSS/Atom feeds.

    Each feed is treated as an independent source.  The *limit* parameter
    is the **default per-feed cap**; individual feeds can override it with
    a ``"limit"`` field in ``config/rss_sources.json``.

    Feed list, AI keywords, timeout, and scan limit are loaded from
    ``config/rss_sources.json`` (falls back to sensible defaults).

    Uses regex-based parsing (no external feedparser library).

    Args:
        limit: Default per-feed item cap (overridable per feed in config).
        dry_run: If ``True``, log intent without making HTTP calls.

    Returns:
        List of raw item dicts (internal pipeline format).
    """
    if limit <= 0:
        return []

    rss_config = load_rss_config()
    feeds = [f for f in rss_config.get("feeds", []) if f.get("enabled", True)]
    keywords = rss_config.get("ai_keywords", [])
    timeout = rss_config.get("timeout", 30.0)
    scan_limit = rss_config.get("scan_limit", 50)

    if not feeds:
        logger.warning("No enabled RSS feeds configured")
        return []

    date_str = _today_str()
    items: list[dict[str, Any]] = []

    if dry_run:
        summaries = [
            f"{f['name']} (limit={f.get('limit', limit)})" for f in feeds
        ]
        logger.info("[DRY-RUN] Would fetch RSS feeds: %s", summaries)
        return [
            {
                "id": _generate_id("rss", i + 1),
                "title": f"Placeholder RSS Article {i + 1}",
                "source_url": f"https://example.com/article-{i + 1}",
                "source": "rss",
                "collected_date": date_str,
                "description": f"Placeholder description for article {i + 1} about AI and LLMs.",
                "summary": "",
                "metadata": {
                    "feed_name": "Placeholder Feed",
                    "feed_url": "https://example.com/rss",
                },
            }
            for i in range(min(len(feeds) * limit, 6))
        ]

    async with httpx.AsyncClient(timeout=timeout) as client:
        for feed in feeds:
            feed_name = feed["name"]
            feed_url = feed["url"]
            feed_limit = feed.get("limit", limit)  # per-feed override
            use_keyword_filter = feed.get(
                "ai_keyword_filter", True
            )  # per-feed toggle

            if feed_limit <= 0:
                continue

            try:
                logger.info(
                    "Fetching RSS: %s (%s, limit=%d)",
                    feed_name, feed_url, feed_limit,
                )
                resp = await client.get(feed_url)
                resp.raise_for_status()
                raw_xml = resp.text
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", feed_name, exc)
                continue

            # Support both RSS <item> and Atom <entry>.
            entries = re.findall(
                r"<(?:item|entry)[^>]*>(.*?)</(?:item|entry)>", raw_xml, re.DOTALL
            )

            # Scan up to ``scan_limit`` entries per feed and collect at
            # most ``feed_limit`` matching items.
            feed_count = 0
            for entry_xml in entries[:scan_limit]:
                if feed_count >= feed_limit:
                    break

                title = _extract_xml_tag(entry_xml, "title") or ""
                link = (
                    _extract_link_text(entry_xml)
                    or _extract_link_href(entry_xml)
                    or ""
                )
                description = (
                    _extract_xml_tag(entry_xml, "description")
                    or _extract_xml_tag(entry_xml, "summary")
                    or ""
                )

                if not title or not link:
                    continue

                combined = f"{title} {description}"
                if use_keyword_filter and not _is_ai_relevant(combined, keywords):
                    continue

                items.append(
                    {
                        "id": _generate_id("rss", len(items) + 1),
                        "title": title,
                        "source_url": link,
                        "source": "rss",
                        "collected_date": date_str,
                        "description": description,
                        "summary": "",
                        "metadata": {
                            "feed_name": feed_name,
                            "feed_url": feed_url,
                        },
                    }
                )
                feed_count += 1

    logger.info(
        "Collected %d RSS items from %d feed(s)",
        len(items), len(feeds),
    )
    return items


# ---------------------------------------------------------------------------
# Step 2 — Analyze
# ---------------------------------------------------------------------------


async def analyze_item(
    item: dict[str, Any],
    client: Any,
    total_usage: Usage,
    *,
    max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any] | None:
    """Analyse a single raw item via LLM.

    Generates a Chinese summary, numeric score, scoring dimensions,
    technical highlights, and tags.

    Args:
        item: Raw item dict with at least ``title`` and ``description``.
        client: An ``LLMProvider`` instance (from ``create_llm_client()``).
        total_usage: Mutable ``Usage`` accumulator updated after each call.
        max_tokens: Maximum output tokens for the LLM response.

    Returns:
        Item dict enriched with an ``analysis`` sub-dict and
        ``summary`` / ``tags`` at the top level, or ``None`` on failure.
    """
    content = f"标题：{item['title']}\n描述：{item['description']}"
    if not content.strip():
        logger.warning("Skipping item %s: empty content", item.get("id"))
        return None

    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    try:
        response: LLMResponse = await client.chat_with_retry(
            messages, max_retries=3, max_tokens=max_tokens
        )
    except Exception as exc:
        logger.error("LLM analysis failed for %s: %s", item["id"], exc)
        return None

    # Accumulate token usage for batch-level cost reporting.
    usage: Usage = response.usage
    total_usage.prompt_tokens += usage.prompt_tokens
    total_usage.completion_tokens += usage.completion_tokens
    total_usage.total_tokens += usage.total_tokens

    # Per-item cost log for observability.
    model_name = response.model or "?"
    cost = usage.total_cost_cny(response.model) if response.model else 0.0
    logger.info(
        "%s — tokens: %d (in=%d / out=%d), model=%s, cost=¥%.6f",
        item["id"],
        usage.total_tokens,
        usage.prompt_tokens,
        usage.completion_tokens,
        model_name,
        cost,
    )

    parsed = _extract_json(response.content)
    if not parsed:
        logger.warning(
            "Could not parse LLM response for %s, falling back to defaults",
            item["id"],
        )
        parsed = {}

    summary = parsed.get("summary", item.get("summary", ""))
    analysis = {
        "score": parsed.get("score", 5),
        "scoring_reasons": parsed.get("scoring_reasons", ""),
        "scoring_dimensions": parsed.get(
            "scoring_dimensions",
            {"tech_depth": 0, "innovation": 0, "usability": 0},
        ),
        "technical_highlights": parsed.get("technical_highlights", []),
    }

    return {
        **item,
        "summary": summary,
        "analysis": analysis,
        "tags": parsed.get("tags", []),
    }


async def analyze_items(
    items: list[dict[str, Any]], dry_run: bool = False
) -> list[dict[str, Any]]:
    """Analyse a batch of raw items via LLM.

    Creates a single LLM client (reused for all calls), tracks cumulative
    token usage, and logs a cost summary at the end.  Analyses are
    performed sequentially with a small delay between calls to help stay
    within rate limits.

    Args:
        items: List of raw item dicts.
        dry_run: If ``True``, assign default analysis values without
            calling the LLM.

    Returns:
        List of analysed item dicts.
    """
    if not items:
        return []

    if dry_run:
        logger.info("[DRY-RUN] Would analyse %d items via LLM", len(items))
        return [
            {
                **item,
                "analysis": {
                    "score": 5,
                    "scoring_reasons": "Dry run — no analysis performed",
                    "scoring_dimensions": {
                        "tech_depth": 0,
                        "innovation": 0,
                        "usability": 0,
                    },
                    "technical_highlights": [],
                },
                "tags": ["dry-run"],
            }
            for item in items
        ]

    logger.info("Analysing %d items via LLM (sequential)", len(items))

    # Single client reused across all calls (cached internally).
    client = create_llm_client()
    total_usage = Usage()
    analysed: list[dict[str, Any]] = []

    for i, item in enumerate(items):
        logger.info("Analysis progress: %d/%d — %s", i + 1, len(items), item["id"])
        result = await analyze_item(item, client, total_usage)
        if result:
            analysed.append(result)
        # Brief delay to reduce chance of rate limiting.
        await asyncio.sleep(0.5)

    # Cumulative cost summary.
    if total_usage.total_tokens > 0:
        logger.info(
            "Cumulative — tokens: %d (in=%d / out=%d), items: %d/%d",
            total_usage.total_tokens,
            total_usage.prompt_tokens,
            total_usage.completion_tokens,
            len(analysed),
            len(items),
        )

    logger.info("Successfully analysed %d/%d items", len(analysed), len(items))
    return analysed


# ---------------------------------------------------------------------------
# Step 3 — Organize
# ---------------------------------------------------------------------------


def _load_existing_urls() -> set[str]:
    """Load ``source_url`` values from all existing articles on disk.

    Returns:
        Set of known URLs (used for cross-run deduplication).
    """
    urls: set[str] = set()
    if not ARTICLES_DIR.exists():
        return urls

    for path in ARTICLES_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                article: dict[str, Any] = json.load(f)
            url = article.get("source_url", "")
            if url:
                urls.add(url)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping unreadable article %s: %s", path.name, exc)

    return urls


def _dedup_items(
    items: list[dict[str, Any]], known_urls: set[str]
) -> list[dict[str, Any]]:
    """Remove duplicate items by ``source_url``.

    Deduplicates against both on-disk articles and other items seen
    earlier in the same run.

    Args:
        items: List of (analysed) item dicts.
        known_urls: URLs already persisted on disk.

    Returns:
        Deduplicated list preserving insertion order.
    """
    seen: set[str] = set(known_urls)
    deduped: list[dict[str, Any]] = []

    for item in items:
        url = item.get("source_url", "")
        if url in seen:
            logger.debug("Skipping duplicate: %s (%s)", item.get("title"), url)
            continue
        if url:
            seen.add(url)
        deduped.append(item)

    return deduped


def _validate_article(article: dict[str, Any]) -> list[str]:
    """Validate an article dict against the required schema.

    Checks for required top-level fields, analysis sub-fields, and
    score range constraints.

    Args:
        article: Article dict to validate.

    Returns:
        List of error messages (empty = valid).
    """
    errors: list[str] = []

    for field in _ARTICLE_REQUIRED_FIELDS:
        if field not in article:
            errors.append(f"Missing required field: {field}")

    analysis = article.get("analysis", {})
    if isinstance(analysis, dict):
        for field in _ANALYSIS_REQUIRED_FIELDS:
            if field not in analysis:
                errors.append(f"Missing analysis field: {field}")

        dims = analysis.get("scoring_dimensions", {})
        if isinstance(dims, dict):
            for field in _SCORING_DIMENSIONS_FIELDS:
                val = dims.get(field, -1)
                if not isinstance(val, (int, float)) or val < 0 or val > 10:
                    errors.append(f"Invalid scoring_dimension.{field}: {val}")
        else:
            errors.append("scoring_dimensions must be a dict")

        score = analysis.get("score", -1)
        if not isinstance(score, int) or score < 1 or score > 10:
            errors.append(f"Invalid analysis.score: {score} (must be int 1-10)")

    tags = article.get("tags", [])
    if not isinstance(tags, list) or len(tags) == 0:
        errors.append("tags must be a non-empty list")

    return errors


def _format_article(item: dict[str, Any]) -> dict[str, Any]:
    """Convert an analysed item into the canonical article format.

    Args:
        item: Analysed item dict with ``analysis`` and ``tags``.

    Returns:
        Article dict ready for persistence.
    """
    now_iso = _now_iso()
    analysis: dict[str, Any] = item.get("analysis", {})
    tags: list[str] = item.get("tags", [])

    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "source_url": item.get("source_url", ""),
        "source_type": item.get("source", item.get("source_type", "")),
        "summary": item.get("summary", ""),
        "published_at": item.get("collected_date", _today_str()),
        "collected_at": now_iso,
        "status": "pending",
        "metadata": item.get("metadata", {}),
        "analysis": {
            "score": analysis.get("score", 5),
            "scoring_reasons": analysis.get("scoring_reasons", ""),
            "scoring_dimensions": analysis.get(
                "scoring_dimensions",
                {"tech_depth": 0, "innovation": 0, "usability": 0},
            ),
            "technical_highlights": analysis.get("technical_highlights", []),
        },
        "tags": tags,
    }


def organize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Organise analysed items: deduplication, standardisation, validation.

    Args:
        items: List of analysed item dicts.

    Returns:
        List of valid, standardised article dicts.
    """
    if not items:
        return []

    # 1. Deduplicate against existing articles.
    known = _load_existing_urls()
    logger.info("Loaded %d existing article URLs for dedup", len(known))
    deduped = _dedup_items(items, known)
    logger.info("%d items remain after dedup", len(deduped))

    # 2. Format every item.
    formatted = [_format_article(item) for item in deduped]

    # 3. Validate and filter by score.
    valid: list[dict[str, Any]] = []
    for article in formatted:
        score = article.get("analysis", {}).get("score", 0)
        if score < DEFAULT_MIN_SCORE:
            logger.info(
                "Skipping %s (score=%s < %s)", article["id"], score, DEFAULT_MIN_SCORE
            )
            continue
        errors = _validate_article(article)
        if errors:
            logger.warning(
                "Validation warnings for %s: %s", article["id"], "; ".join(errors)
            )
        valid.append(article)

    logger.info("Organised %d valid articles", len(valid))
    return valid


# ---------------------------------------------------------------------------
# Step 4 — Save
# ---------------------------------------------------------------------------


def save_raw(items: list[dict[str, Any]]) -> None:
    """Save raw collected items to ``knowledge/raw/``.

    Writes one JSON file per source, matching the project's existing
    raw data format.

    Args:
        items: List of raw item dicts (internal pipeline format).
    """
    if not items:
        return

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    date_str = _today_str()
    now_iso = _now_iso()

    # Group by source.
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        src = item.get("source", "unknown")
        by_source.setdefault(src, []).append(item)

    for source, src_items in sorted(by_source.items()):
        filename = f"{date_str}-{source}.json"
        filepath = RAW_DIR / filename
        data = {
            "source": source,
            "collected_at": now_iso,
            "items": [
                {
                    "title": it["title"],
                    "url": it["source_url"],
                    "summary": it.get("summary", ""),
                    **it.get("metadata", {}),
                }
                for it in src_items
            ],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Saved %d raw items to %s", len(src_items), filepath)


def save_article(article: dict[str, Any]) -> None:
    """Save a single article to ``knowledge/articles/``.

    File is named ``{date}-{source}-{seq}.json`` so that a directory listing
    sorts articles chronologically — making it easy for humans to browse.

    Args:
        article: Standardised article dict.
    """
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    article_id = article.get("id", "unknown")
    # Parse date from published_at (YYYY-MM-DD) and seq from id.
    date_str = article.get("published_at", "unknown")[:10]
    source = article.get("source_type", "unknown")
    seq = article_id.split("-")[-1]  # last component of "github-20260513-001"
    filename = f"{date_str}-{source}-{seq}.json"
    filepath = ARTICLES_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(article, f, ensure_ascii=False, indent=2)
    logger.debug("Saved article to %s", filepath)


def save_articles(articles: list[dict[str, Any]]) -> int:
    """Save a batch of articles to ``knowledge/articles/``.

    Args:
        articles: List of standardised article dicts.

    Returns:
        Number of successfully saved files.
    """
    if not articles:
        logger.info("No articles to save")
        return 0

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for article in articles:
        try:
            save_article(article)
            saved += 1
        except OSError as exc:
            logger.error("Failed to save article %s: %s", article.get("id"), exc)

    logger.info("Saved %d articles to %s", saved, ARTICLES_DIR)
    return saved


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------


class Pipeline:
    """Four-step knowledge base automation pipeline.

    Typical usage::

        pipeline = Pipeline(sources=["github", "rss"], limit=20)
        saved = await pipeline.run()

    Attributes:
        sources: Active data source names.
        limit: Maximum items per source.
        dry_run: If ``True``, skip mutations (no API calls, no file writes).
    """

    def __init__(
        self,
        sources: list[str],
        limit: int,
        dry_run: bool = False,
    ) -> None:
        self.sources = sources
        self.limit = limit
        self.dry_run = dry_run

    async def run(self) -> int:
        """Execute the full pipeline.

        Returns:
            Number of articles persisted to disk (0 on failure or dry-run).
        """
        # ── Step 1: Collect ────────────────────────────────────────────
        logger.info("=" * 56)
        logger.info("Step 1/4 — Collecting data from %s", ", ".join(self.sources))
        logger.info("=" * 56)

        raw_items: list[dict[str, Any]] = []

        if "github" in self.sources:
            items = await collect_github(self.limit, dry_run=self.dry_run)
            raw_items.extend(items)

        if "rss" in self.sources:
            items = await collect_rss(self.limit, dry_run=self.dry_run)
            raw_items.extend(items)

        if not raw_items:
            logger.warning("No items collected — aborting pipeline")
            return 0

        # Persist raw data before analysis.
        if not self.dry_run:
            save_raw(raw_items)

        # ── Step 2: Analyze ────────────────────────────────────────────
        logger.info("=" * 56)
        logger.info("Step 2/4 — Analysing %d items via LLM", len(raw_items))
        logger.info("=" * 56)

        analysed = await analyze_items(raw_items, dry_run=self.dry_run)
        if not analysed:
            logger.warning("No items analysed — aborting pipeline")
            return 0

        # ── Step 3: Organize ───────────────────────────────────────────
        logger.info("=" * 56)
        logger.info("Step 3/4 — Organising %d items", len(analysed))
        logger.info("=" * 56)

        organised = organize_items(analysed)
        if not organised:
            logger.warning("No items after organisation — aborting pipeline")
            return 0

        # ── Step 4: Save ───────────────────────────────────────────────
        logger.info("=" * 56)
        logger.info("Step 4/4 — Saving %d articles", len(organised))
        logger.info("=" * 56)

        if self.dry_run:
            logger.info(
                "[DRY-RUN] Would save %d articles to %s",
                len(organised),
                ARTICLES_DIR,
            )
            if organised:
                preview = {
                    "id": organised[0]["id"],
                    "title": organised[0]["title"],
                    "source_type": organised[0]["source_type"],
                    "score": organised[0].get("analysis", {}).get("score"),
                    "tags": organised[0].get("tags", []),
                }
                logger.info(
                    "[DRY-RUN] First article preview: %s",
                    json.dumps(preview, ensure_ascii=False),
                )
            return len(organised)

        saved = save_articles(organised)
        return saved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed namespace.
    """
    parser = argparse.ArgumentParser(
        prog="ai-knowledge-base",
        description="AI Knowledge Base — automated collect → analyse → organise → save pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ai-knowledge-base --sources github,rss --limit 20\n"
            "  ai-knowledge-base --sources github --limit 5\n"
            "  ai-knowledge-base --sources rss --limit 10 --dry-run\n"
            "  ai-knowledge-base --verbose\n"
        ),
    )
    parser.add_argument(
        "--sources",
        default="github,rss",
        help="Comma-separated data sources (github, rss). Default: github,rss",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum items per source (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip mutations — no API calls or file writes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments forwarded to :func:`parse_args`.

    Returns:
        Exit code (0 = success).
    """
    args = parse_args(argv)

    # Configure root logger so all ``__name__`` loggers inherit the format.
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    valid = {"github", "rss"}
    invalid = set(sources) - valid
    if invalid:
        logger.error(
            "Invalid source(s): %s. Valid options: %s",
            ", ".join(sorted(invalid)),
            ", ".join(sorted(valid)),
        )
        return 1

    logger.info(
        "Pipeline starting — sources=%s, limit=%d, dry_run=%s, verbose=%s",
        sources,
        args.limit,
        args.dry_run,
        args.verbose,
    )

    pipeline = Pipeline(sources=sources, limit=args.limit, dry_run=args.dry_run)
    try:
        saved = await pipeline.run()
    except Exception:
        logger.exception("Pipeline failed unexpectedly")
        return 1

    logger.info("Pipeline complete — %d articles saved", saved)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
