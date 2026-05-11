---
name: github-trending
description: 从 GitHub Trending 采集热门开源项目，按 AI/LLM/Agent/ML 关键字智能过滤，输出结构化 JSON 数组。Fetches top trending repositories from GitHub Trending, filters by AI/LLM/Agent/ML topics, and outputs structured JSON. Use when 用户提到: github trending、trending on github、github 热门/热榜/排行/趋势/热榜排行、github hot/top/popular/trending/star repos、采集/抓取/获取/拉取/收集/查看 github 项目/仓库、fetch/scrape/get/pull/grab/collect/retrieve github trending、what's trending on github、explore github、github trends today/week、github 开源趋势/每日精选/今天最火、github AI/LLM/Agent/ML/大模型/人工智能 项目、github discovery、github top projects 等任何 GitHub 热门开源项目发现的场景。
allowed-tools:
  - WebFetch
  - Read
  - Bash
  - Glob
---

# GitHub Trending · 采集

## Quick start

自动抓取 `https://github.com/trending` Top 50 repo，按 AI/LLM/Agent/ML 过滤，stdout 输出 JSON 数组。失败返回 `[]`，不抛异常。

## Workflow

### 1. 拉取页面

用 `WebFetch` 获取 `https://github.com/trending`，使用 `format: "html"` 获取原始 HTML。

### 2. 解析 Top 50

从 HTML 中提取 repo 卡片，解析每个 repo：

| 字段 | 类型 | 提取来源 |
|------|------|---------|
| `title` | string | `<h2>` 内 repo 名（`owner/repo`） |
| `url` | string | 拼装 `https://github.com/owner/repo` |
| `stars` | int | "X stars today" 中的数字 |
| `topics` | string[] | topics 标签列表 |
| `description` | string | repo 简介段落 |
| `language` | string | 编程语言标签 |

### 3. 关键字过滤

`topics` 包含以下任一即可纳入（大小写不敏感）：

- `ai`
- `llm`
- `agent`
- `ml`

### 4. 输出

stdout 输出 JSON 数组，通过 JSON Schema 校验后输出。

## JSON Schema

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "required": ["title", "url", "stars", "topics", "description", "language"],
    "properties": {
      "title": { "type": "string" },
      "url": { "type": "string" },
      "stars": { "type": "integer" },
      "topics": { "type": "array", "items": { "type": "string" } },
      "description": { "type": "string" },
      "language": { "type": "string" }
    }
  }
}
```

## 边界约束

- 不调用 GitHub API（rate limit 太紧），直接解析 HTML
- 不存数据库，只 stdout
- 不做去重（由 caller 处理）
- 最多处理 50 条
- 单次执行 < 10s
- 不抛异常，任何失败返回 `[]`

## 验证

```bash
skill-invoke github-trending
```

验证点：输出为合法 JSON、字段完整、过滤正确。
