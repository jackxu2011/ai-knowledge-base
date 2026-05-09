---
name: github-trending
description: 当需要采集github热门开源项目时使用此技能, 适用于知识采集
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# GitHub Trending 采集技能

## 使用场景

当需要采集 GitHub 热门开源项目时使用此技能，自动从 GitHub Trending 页面获取 AI/LLM/Agent 相关的高质量项目。

## 执行步骤

1. **搜索热门仓库**：调用 GitHub API 获取 GitHub Trending 页面数据
2. **提取信息**：解析返回的仓库数据，提取 name、url、stars、language、topics 等字段
3. **过滤**：
   - 纳入条件：topics 或 description 包含 AI、LLM、Agent、ML、Machine Learning 等关键词
   - 排除条件：项目名包含 Awesome（此类列表项目不符合独立工具定位）
4. **去重**：基于仓库 URL 去重，避免重复采集相同项目
5. **撰写中文摘要**：使用公式「项目名 + 做什么 + 为什么值得关注」生成中文摘要
6. **排序取 Top 20**：按 stars 降序排列，取前 20 个最具价值的项目
7. **输出 JSON**：将处理结果以 JSON 格式保存到 `knowledge/raw/YYYY-MM-DD-github_trending.json`

## 注意事项

- GitHub API 有速率限制，采集时需控制请求频率
- 只采集开源仓库，忽略 fork 的项目
- language 字段可能为空，需做兜底处理
- topics 为空时，根据 description 推断可能的主题标签
- 中文摘要需简洁，控制在 50-100 字以内

## 输出格式

```json
{
  "source": "github_trending",
  "collected_at": "2026-05-09T12:00:00Z",
  "items": [
    {
      "title": "repo-name",
      "url": "https://github.com/owner/repo",
      "summary": "项目名是一个 XXX工具，帮助用户实现 XXX，推荐理由是 XXX",
      "stars": 12345,
      "language": "Python",
      "topics": ["llm", "ai-agent"]
    }
  ]
}
```
