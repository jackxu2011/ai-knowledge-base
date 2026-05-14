#!/usr/bin/env python3
"""MCP Knowledge Server — search local AI knowledge base via JSON-RPC 2.0 over stdio.

Implements the Model Context Protocol (JSON-RPC 2.0 over stdio) with three tools:
    - search_articles(keyword, limit=5)
    - get_article(article_id)
    - knowledge_stats()

No third-party dependencies; Python standard library only.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ARTICLES_DIR = Path("knowledge/articles")
PROTOCOL_VERSION = "2024-11-05"


def _load_articles() -> list[dict[str, Any]]:
    """Load all JSON article files from knowledge/articles/."""
    if not ARTICLES_DIR.is_dir():
        return []
    articles: list[dict[str, Any]] = []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                articles.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return articles


def _build_index(articles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a lookup index keyed by article id."""
    return {a.get("id", ""): a for a in articles if a.get("id")}


def search_articles(keyword: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search articles by keyword in title and summary."""
    kw = keyword.lower()
    articles = _load_articles()
    scored: list[tuple[int, dict[str, Any]]] = []
    for a in articles:
        title = a.get("title", "").lower()
        summary = a.get("summary", "").lower()
        score = 0
        if kw in title:
            score += 10
        if kw in summary:
            score += 5
        if score > 0:
            scored.append((score, a))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = [a for _, a in scored[:limit]]
    return results


def get_article(article_id: str) -> dict[str, Any] | None:
    """Retrieve a single article by its id, or None if not found."""
    index = _build_index(_load_articles())
    return index.get(article_id)


def knowledge_stats() -> dict[str, Any]:
    """Return statistics about the knowledge base."""
    articles = _load_articles()
    total = len(articles)

    source_counter: Counter[str] = Counter()
    tag_counter: Counter[str] = Counter()
    scores: list[int] = []

    for a in articles:
        source_counter[a.get("source_type", "unknown")] += 1
        analysis = a.get("analysis", {})
        score_val = analysis.get("score")
        if isinstance(score_val, (int, float)):
            scores.append(score_val)
        for tag in a.get("tags", []):
            if tag:
                tag_counter[str(tag)] += 1

    top_tags = [(tag, count) for tag, count in tag_counter.most_common(10)]

    avg_score: float | str = "N/A"
    if scores:
        avg_score = round(sum(scores) / len(scores), 2)

    return {
        "total_articles": total,
        "source_distribution": dict(source_counter),
        "top_tags": top_tags,
        "average_score": avg_score,
        "score_count": len(scores),
    }


def _handle_initialize(params: dict[str, Any]) -> dict[str, Any]:
    client_info = params.get("clientInfo", {})
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {
                "list": True,
                "call": True,
            }
        },
        "serverInfo": {
            "name": "knowledge-server",
            "version": "1.0.0",
        },
    }


def _handle_tools_list() -> dict[str, Any]:
    return {
        "tools": [
            {
                "name": "search_articles",
                "description": "Search articles by keyword in title and summary",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "Search keyword"},
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default 5)",
                            "default": 5,
                        },
                    },
                    "required": ["keyword"],
                },
            },
            {
                "name": "get_article",
                "description": "Get a single article by its id",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "article_id": {"type": "string", "description": "Article id"},
                    },
                    "required": ["article_id"],
                },
            },
            {
                "name": "knowledge_stats",
                "description": "Return knowledge base statistics",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
    }


def _handle_tools_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "search_articles":
        keyword = arguments.get("keyword", "")
        limit = arguments.get("limit", 5)
        results = search_articles(keyword, limit=limit)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(results, ensure_ascii=False),
                }
            ]
        }
    elif name == "get_article":
        article_id = arguments.get("article_id", "")
        article = get_article(article_id)
        if article is None:
            return {
                "content": [{"type": "text", "text": "Article not found"}],
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": json.dumps(article, ensure_ascii=False)}]
        }
    elif name == "knowledge_stats":
        stats = knowledge_stats()
        return {
            "content": [{"type": "text", "text": json.dumps(stats, ensure_ascii=False)}]
        }
    else:
        return {
            "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            "isError": True,
        }


def _dispatch(request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        result = _handle_initialize(params)
    elif method == "tools/list":
        result = _handle_tools_list()
    elif method == "tools/call":
        arguments = params.get("arguments", {})
        tool_name = params.get("name", "")
        result = _handle_tools_call(tool_name, arguments)
    else:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": f"Method not found: {method}"},
            "id": req_id,
        }

    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def main() -> None:
    """Read JSON-RPC requests from stdin and write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            resp = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            }
            print(json.dumps(resp, ensure_ascii=False), flush=True)
            continue

        response = _dispatch(request)
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()