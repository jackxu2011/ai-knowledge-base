---
name: hacker-news
description: 当需要从Hacker News采集技术热点时使用此技能，适用于知识采集
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# Hacker News 采集技能

## 使用场景

当需要从 Hacker News 采集 AI/LLM/Agent 领域的技术热点时使用此技能，自动从 HN API 获取高质量讨论。

## 执行步骤

1. **获取热门故事 ID 列表**：调用 HN Firebase API 获取 Top Stories
2. **遍历获取详情**：对每个 story ID 调用 `/v0/item/{id}.json` 获取完整信息
3. **过滤**：
   - 纳入条件：title、text 或 url 包含 AI、LLM、Agent、ML、Machine Learning、GPT、Neural 等关键词
   - 排除条件：问询帖（Ask HN）、投票帖（Show HN）
4. **去重**：基于 URL 去重，避免重复采集相同内容
5. **撰写中文摘要**：使用公式「标题 + 核心内容 + 为什么值得关注」生成中文摘要
6. **排序取 Top 20**：按 score 降序排列，取前 20 个最具价值的条目
7. **输出 JSON**：将处理结果以 JSON 格式保存到 `knowledge/raw/YYYY-MM-DD-hacker_news.json`

## 注意事项

- HN API 无需认证，但需控制请求频率（建议间隔 50ms）
- 只采集有 url 的条目（外部链接），忽略纯讨论帖
- score 和 descendants 可能为空，需做兜底处理
- 中文摘要需简洁，控制在 50-100 字以内

## 输出格式

```json
{
  "source": "hacker_news",
  "collected_at": "2026-05-09T12:00:00Z",
  "items": [
    {
      "title": "Article Title",
      "url": "https://example.com/article",
      "summary": "标题是一个 XXX工具/研究，帮助用户实现 XXX，推荐理由是 XXX",
      "score": 123,
      "comments": 45,
      "author": "username"
    }
  ]
}
```