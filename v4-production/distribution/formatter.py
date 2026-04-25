"""
distribution/formatter.py — 多格式内容转换器

将知识库 JSON 条目转换为 Markdown、Telegram、飞书等渠道格式。
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# ============================================================
# 基础格式转换
# ============================================================

def json_to_markdown(article: dict) -> str:
    """将单篇文章 JSON 转换为可读的 Markdown 格式。

    Args:
        article: 知识条目字典，需包含 title, source, url, summary, tags, collected_at 等字段

    Returns:
        Markdown 格式字符串
    """
    tags_str = ", ".join(f"`{tag}`" for tag in article.get("tags", []))
    score = article.get("relevance_score", 0)
    score_bar = "🟢" if score >= 0.8 else "🟡" if score >= 0.6 else "🔴"

    return f"""## {article['title']}

- **来源**：{article.get('source', '未知')}
- **日期**：{article.get('collected_at', '未知')[:10]}
- **相关性**：{score_bar} {score:.1f}
- **标签**：{tags_str}

{article.get('summary', '暂无摘要')}

🔗 [原文链接]({article.get('url', '#')})

---
"""


def json_to_telegram(article: dict) -> str:
    """将单篇文章 JSON 转换为 Telegram MarkdownV2 格式。

    Telegram MarkdownV2 需要转义特殊字符。
    输出包含标题链接、摘要、标签。

    Args:
        article: 知识条目字典

    Returns:
        Telegram MarkdownV2 格式字符串
    """
    # Telegram MarkdownV2 需要转义的特殊字符
    def escape_md(text: str) -> str:
        special_chars = r"_*[]()~`>#+-=|{}.!"
        for char in special_chars:
            text = text.replace(char, f"\\{char}")
        return text

    title = escape_md(article["title"])
    summary = escape_md(article.get("summary", "暂无摘要"))
    source = escape_md(article.get("source", "未知"))
    url = article.get("url", "#")
    tags = " ".join(f"\\#{escape_md(tag.replace('-', '_'))}" for tag in article.get("tags", []))
    score = article.get("relevance_score", 0)
    score_str = escape_md(f"{score:.1f}")  # 0.9 → 0\.9 (MarkdownV2 要求 . 转义)

    return f"""📌 [{title}]({url})

{summary}

📊 相关性：{score_str} \\| 来源：{source}
{tags}"""


def json_to_feishu(article: dict) -> dict:
    """将单篇文章 JSON 转换为飞书卡片消息格式。

    飞书卡片使用 Interactive Message 格式，
    参考：https://open.feishu.cn/document/common-capabilities/message-card/overview

    Args:
        article: 知识条目字典

    Returns:
        飞书卡片消息 JSON 结构
    """
    score = article.get("relevance_score", 0)
    score_color = "green" if score >= 0.8 else "yellow" if score >= 0.6 else "red"
    tags_text = " | ".join(article.get("tags", []))

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📌 {article['title']}"
                },
                "template": score_color
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": article.get("summary", "暂无摘要")
                    }
                },
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**来源**：{article.get('source', '未知')}"
                            }
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**相关性**：{score:.1f}"
                            }
                        }
                    ]
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": f"标签：{tags_text}"
                        }
                    ]
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "查看原文"
                            },
                            "url": article.get("url", "#"),
                            "type": "primary"
                        }
                    ]
                }
            ]
        }
    }


# ============================================================
# 每日简报生成
# ============================================================

def generate_daily_digest(
    knowledge_dir: str = "knowledge/articles",
    date: str | None = None,
    top_n: int = 5,
) -> dict[str, str]:
    """生成每日简报，输出 Markdown / Telegram / 飞书三种格式。

    扫描指定日期的知识条目，按 relevance_score 排序取 Top N，
    生成三种渠道格式的简报内容。

    Args:
        knowledge_dir: 知识条目目录路径
        date: 目标日期（YYYY-MM-DD），默认今天
        top_n: 取前 N 篇文章

    Returns:
        字典，包含 markdown / telegram / feishu 三个键
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    articles_path = Path(knowledge_dir)
    articles: list[dict] = []

    # 扫描当日文章
    for json_file in articles_path.glob(f"{date}-*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                article = json.load(f)
                articles.append(article)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[WARN] 跳过格式异常文件：{json_file} ({e})")
            continue

    # 按相关性排序，取 Top N
    articles.sort(key=lambda a: a.get("relevance_score", 0), reverse=True)
    top_articles = articles[:top_n]

    if not top_articles:
        empty_msg = f"📭 {date} 暂无新增知识条目。"
        return {
            "markdown": empty_msg,
            "telegram": empty_msg,
            "feishu": _build_empty_feishu_card(date),
        }

    # 生成三种格式
    return {
        "markdown": _build_markdown_digest(date, top_articles),
        "telegram": _build_telegram_digest(date, top_articles),
        "feishu": _build_feishu_digest(date, top_articles),
    }


def _build_markdown_digest(date: str, articles: list[dict]) -> str:
    """构建 Markdown 格式的每日简报。"""
    header = f"# 📰 AI 知识库每日简报 — {date}\n\n"
    header += f"今日精选 **{len(articles)}** 篇高质量技术资讯：\n\n"

    body = ""
    for i, article in enumerate(articles, 1):
        tags = ", ".join(f"`{t}`" for t in article.get("tags", []))
        body += f"### {i}. {article['title']}\n\n"
        body += f"{article.get('summary', '暂无摘要')}\n\n"
        body += f"- 来源：{article.get('source', '未知')} | "
        body += f"相关性：{article.get('relevance_score', 0):.1f}\n"
        body += f"- 标签：{tags}\n"
        body += f"- 🔗 [原文链接]({article.get('url', '#')})\n\n"

    footer = "---\n\n*由 AI 知识库自动生成 | "
    footer += f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"

    return header + body + footer


def _build_telegram_digest(date: str, articles: list[dict]) -> str:
    """构建 Telegram 格式的每日简报。"""

    def escape_md(text: str) -> str:
        for char in r"_*[]()~`>#+-=|{}.!":
            text = text.replace(char, f"\\{char}")
        return text

    lines = [f"📰 *AI 知识库每日简报 — {escape_md(date)}*\n"]

    for i, article in enumerate(articles, 1):
        title = escape_md(article["title"])
        summary = escape_md(article.get("summary", "暂无摘要"))
        url = article.get("url", "#")
        score = article.get("relevance_score", 0)
        tags = " ".join(
            f"\\#{escape_md(t.replace('-', '_'))}"
            for t in article.get("tags", [])
        )

        score_str = escape_md(f"{score:.1f}")  # MarkdownV2: 0.9 → 0\.9
        lines.append(f"{i}\\. [{title}]({url})")
        lines.append(f"   {summary[:80]}{'\\.\\.\\.' if len(summary) > 80 else ''}")
        lines.append(f"   📊 {score_str} {tags}\n")

    return "\n".join(lines)


def _build_feishu_digest(date: str, articles: list[dict]) -> dict:
    """构建飞书卡片格式的每日简报。"""
    elements = []

    for i, article in enumerate(articles, 1):
        tags = " | ".join(article.get("tags", []))
        score = article.get("relevance_score", 0)

        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**{i}. {article['title']}**\n"
                    f"{article.get('summary', '暂无摘要')[:100]}...\n"
                    f"来源：{article.get('source', '未知')} | "
                    f"相关性：{score:.1f} | 标签：{tags}"
                )
            }
        })

        elements.append({
            "tag": "action",
            "actions": [{
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看原文"},
                "url": article.get("url", "#"),
                "type": "default"
            }]
        })

        # 条目之间加分隔线
        if i < len(articles):
            elements.append({"tag": "hr"})

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📰 AI 知识库每日简报 — {date}"
                },
                "template": "blue"
            },
            "elements": elements
        }
    }


def _build_empty_feishu_card(date: str) -> dict:
    """构建空简报的飞书卡片。"""
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📭 AI 知识库每日简报 — {date}"
                },
                "template": "grey"
            },
            "elements": [{
                "tag": "div",
                "text": {
                    "tag": "plain_text",
                    "content": "今日暂无新增知识条目。"
                }
            }]
        }
    }


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import sys

    date = sys.argv[1] if len(sys.argv) > 1 else None
    digest = generate_daily_digest(date=date)

    print("=" * 60)
    print("Markdown 版本：")
    print("=" * 60)
    print(digest["markdown"])
    print()
    print("=" * 60)
    print("Telegram 版本（MarkdownV2）：")
    print("=" * 60)
    print(digest["telegram"])
    print()
    print("=" * 60)
    print("飞书卡片版本（JSON）：")
    print("=" * 60)
    if isinstance(digest["feishu"], dict):
        print(json.dumps(digest["feishu"], ensure_ascii=False, indent=2))
    else:
        print(digest["feishu"])
