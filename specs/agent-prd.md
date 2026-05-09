# AI知识库 - 三个Agent prd 1.0

## 总流程

每天 UTC 0:00 触发 · collector -> analyzer -> organizer · 串行。

## Agent 职责

- collector: 抓取 github trending top 50 · 过滤AI相关 · 存knowldege/raw
- anlyzer: 读raw · 给每条打3维度标签
- organizer: 读已标注 · 整理成md

## 开放问题(?用to-issues 细化成任务)
- 上游失败下游怎么办？
- 数据怎么传？文件or消息？
- 重跑策略？
- 进度追踪？
