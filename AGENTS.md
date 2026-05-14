# AI 知识库助手 - 协作规范

## 1. 项目概述

AI 驱动的技术动态知识库，自动从 GitHub Trending 和 Hacker News 采集 AI/LLM/Agent 领域热点，经大模型结构化分析后存储为标准 JSON 格式（不存数据库，使用文件系统），支持多渠道分发（Telegram/飞书），帮助开发者快速追踪前沿技术趋势。

## 2. 技术栈

| 组件 | 选型 |
|------|------|
| 运行时 | Python 3.12+ |
| 开发框架 | OpenCode + 国产大模型 |
| 工作流编排 | LangGraph |
| 浏览器自动化 | OpenClaw |
| 包管理 | uv |

## 3. 编码规范

### 风格 & 工具
- **Python**: black 格式化（line_length=88，不改单/双引号）+ ruff lint（只做 lint，不做格式化）
- **TypeScript**: biome 统一 lint + format（`biome check --write` 一次性修复并格式化）

### 命名 & 文档
- **风格规范**: 严格遵循 PEP 8
- **命名规范**: 变量/函数使用 snake_case，常量使用 UPPER_SNAKE_CASE
- **文档字符串**: Google 风格 docstring，所有公开函数必须有文档

### 日志 & 异步
- **日志输出**: 禁止裸 print()，统一使用 logging 模块
  - ERROR：异常需人工介入
  - WARNING：异常但不需介入
  - INFO：关键业务节点
  - DEBUG：调试用
  - 格式：统一包含 timestamp + logger name + level + message
  - 生产环境：INFO 级别
- **异步优先**: IO 密集型操作优先使用 async/await
  - 网络请求、文件 IO 必须 async
  - 第三方库无 async 版本时用 asyncio.to_thread 包装
  - 主入口用 asyncio.run 在顶层统一管理

### 代码质量
- **魔法字符串**: 枚举常量集中管理，禁止在 constants/ 目录外直接使用字面量字符串
  - constants/collectors.py：source_type、API endpoints
  - constants/analyzers.py：tech_depth、scoring weights
  - constants/organizers.py：status、technical_depth 枚举
- **TODO**: 不允许 TODO 提交到 main（存量摸底后分类处理）
- **单测覆盖率**: ≥ 80%（按 coverage.py 默认语句覆盖计量）
- **测试 mock**: 涉及 IO 的测试必须用 mock，避免真实网络请求；Python 用 pytest-mock + aioresponses

### CI 验证
- Python: black check + ruff check + pytest
- TypeScript: biome check（lint）+ biome format（format）

## 4. 项目结构

```
.opencode-test/
├── .opencode/
│   ├── agents/           # Agent 定义与工作流
│   └── skills/           # 可复用技能模块
├── knowledge/
│   ├── raw/              # 原始采集数据（统一 JSON 格式）
│   └── articles/         # 结构化知识条目（JSON）
├── main.py               # 入口脚本
└── pyproject.toml        # 项目配置
```

## 5. 知识条目 JSON 格式

```json
{
  "id": "string",
  "title": "string",
  "source_url": "string",
  "source_type": "github|hn",
  "summary": "string",
  "published_at": "ISO-8601 datetime",
  "collected_at": "ISO-8601 datetime",
  "status": "pending|analyzed|published",
  "metadata": {
    "stars": 0,
    "author": "string"
  },
  "analysis": {
    "score": 0,
    "scoring_reasons": "string",
    "scoring_dimensions": {
      "tech_depth": 0,
      "innovation": 0,
      "usability": 0
    },
    "technical_highlights": ["string"],
    "tags": ["string"]
  }
}
```

## 6. Agent 角色概览

| Agent 角色 | 职责 | 输入 | 输出 |
|-----------|------|------|------|
| **Collector** | 采集 GitHub Trending（最多 20 条，先关键字过滤 AI 相关）、Hacker News 页面内容；不补充非 Trending 内容；架构预留扩展接口（未来可接入 ArXiv/Reddit） | 数据源配置 | raw/ 目录下原始文件 |
| **Analyzer** | 对采集的内容（raw_description、stars、topics 等）进行摘要、评分和分类，生成结构化 JSON；只做分析，不写入文件系统 | 原始数据文件（JSON） | 返回结构化 JSON（分析结果由 Organizer 写入） |
| **Organizer** | 读取 Analyzer 输出，先按 source_url 去重，再进行质量校验（JSON Schema 校验必须字段完整性）、数据格式化、文件写入（articles/）、分发队列管理 | Analyzer 输出的结构化 JSON | articles/ 目录下分发就绪的知识条目 |

### 6.1 Pipeline 执行顺序

```
Collect → 去重（按 source_url）→ Analyze（LLM）→ Organize → Save
```

去重在 Analyze 之前执行，确保重复条目不会浪费 LLM token。

## 7. 边界 & 验收

### 失败处理
- 单次失败：重试 3 次后放弃，跳过该条数据（不回滚其他成功数据）
- 多次失败：连续失败发邮件告警通知
- 超时机制：整个 pipeline 超过 1 小时强制终止，已分析数据保留

### 性能要求
- 每天处理时间控制在 10 分钟内
- 定时任务：凌晨 3 点（服务器空闲）运行，不影响次日分发时效性

### 成本控制
- 大模型 token 成本：先运行观察后再定预算
- 超预算策略：继续运行并发邮件告警
- 监控方式：每次运行完后统计 token 消耗

### 质量保证
- 工具自动化验证项目可运行（能启动不报错）；若生成的 JSON 为空或格式错误，视为不可运行
- JSON Schema 强制校验 12 个标准字段 + 多维度评分字段，确保必要内容不缺失（summary 由 AI 生成，理论不为空）
- 模型多维度评分 + 前期人工 review 双重保障；通过持续迭代模型评分标准优化长期质量

## 8. 红线（绝对禁止）

1. 禁止在代码中硬编码 API Key 或敏感凭证
2. 禁止修改 knowledge/ 目录下已归档的历史数据
3. 禁止同步阻塞调用大模型 API，必须使用异步客户端
4. 禁止跳过去重逻辑重复采集相同 URL
5. 禁止在采集阶段对目标站点发起高频率请求
6. 禁止做通用爬虫（产品定位；架构需预留扩展接口）
7. 禁止跳过 JSON Schema 校验直接输出知识条目
8. 禁止单次采集超过 20 条 GitHub Trending 数据
9. 禁止 pipeline 运行超过 1 小时不设超时终止
