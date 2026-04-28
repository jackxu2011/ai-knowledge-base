---
name: top-rated
description: 当用户要"推荐 / 高分 / 最佳 / score 最高"的知识库文章时触发。典型用语:推荐几个 xxx / score 最高的 / 最值得看的。基于本地 kb,不需要联网。
allowed-tools:
  - Read
---

# 高分推荐

## 做法

1. Read knowledge/articles/index.json
2. 按 relevance_score 降序排序
3. 默认取 top 5(用户给数字就用用户的)
4. 去重(同一个 title 只保留 score 最高的一条)
5. 回复格式:
   ⭐ 高分推荐 top N:
   1. <title> · score <score> · <category>
      id: <id>

## 禁止

- 别 read 目录(EISDIR)
- 别返回低于 0.85 score 的
