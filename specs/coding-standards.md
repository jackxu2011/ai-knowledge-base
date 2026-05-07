# AI 知识库 · 编码规范 v0.1

## 要做什么
- Python 用 black 格式化（line_length=88，不改单/双引号）
- TypeScript strict mode（全部 strict 开关打开，即 `"strict": true`）
- 所有公开函数必须有 Google 风格 docstring
- IO 密集型操作优先使用 async/await
- 禁止裸 print()，统一使用 logging 模块

## 不做什么
- 不用任何魔法字符串（枚举常量集中管理，见 constants/ 目录）
- 不允许 TODO 提交到 main（存量 TODO 摸底后分类处理）
- 不用 ruff format（ruff 只做 lint，不做格式化）

## 边界 & 验收
- 单测覆盖率 ≥ 80%（按 coverage.py 默认语句覆盖计量）
- 涉及 IO 的测试必须用 mock，避免真实网络请求

## 工具链
- Python: black（format）+ ruff（lint）+ pytest-mock（测试 mock）+ aioresponses（HTTP mock）
- TypeScript: biome（统一 lint + format）

## 日志规范
- ERROR：异常需人工介入
- WARNING：异常但不需介入
- INFO：关键业务节点
- DEBUG：调试用
- 格式：统一包含 timestamp + logger name + level + message
- 生产环境：INFO 级别

## 异步规范
- 网络请求、文件 IO 必须 async
- 第三方库无 async 版本时用 asyncio.to_thread 包装
- 主入口用 asyncio.run 在顶层统一管理

## 魔法字符串管理
- 枚举常量集中存放在 constants/ 目录
- constants/collectors.py：source_type、API endpoints
- constants/analyzers.py：tech_category labels、scoring weights
- constants/organizers.py：status、technical_depth 枚举
- 禁止在 constants/ 目录外直接使用字面量字符串

## CI 验证
- Python: black check + ruff check + pytest
- TypeScript: biome check（lint）+ biome format（format）
- biome check --write：一次性修复 lint 问题并格式化

## 怎么验证
- CI 上跑 lint + 单测
