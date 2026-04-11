"""
工作流节点定义 — V3 知识库流水线的 6 个节点（+HumanFlag 终点）

每个节点是一个纯函数: State → dict（部分状态更新）
LangGraph 会自动将返回值合并到全局 State 中。

节点职责严格隔离（Single Responsibility）：

    ① plan       → Planner     → 动态规划策略（patterns/planner.py）
    ② collect    → Collector   → 数据采集
    ③ analyze    → Analyzer    → 单条 LLM 分析
    ④ review     → Reviewer    → 审核 analyses（1-10 分 × 5 维）
    ⑤ revise     → Reviser     → 读 feedback，LLM 定向修改 analyses（只在未通过时）
    ⑥ organize   → Organizer   → 过滤 + 去重 + 格式化 + 写盘（终点，只在通过后）
    ⑦ human_flag → HumanFlag   → 超过 max_iterations，标记人工介入（终点）

拓扑：

    plan → collect → analyze → review ┬─[pass]────→ organize → END
                                      │
                                      ├─[fail]────→ revise → review（循环）
                                      │
                                      └─[>max]────→ human_flag → END
"""

import json
import os
from datetime import datetime, timezone

from patterns.planner import planner_node  # noqa: F401  # re-export for graph.py
from workflows.model_client import accumulate_usage, chat, chat_json
from workflows.state import KBState


# ═══════════════════════════════════════════════════════════════════════════
# ② Collector — 数据采集
# ═══════════════════════════════════════════════════════════════════════════
def collect_node(state: KBState) -> dict:
    """采集节点：调用 GitHub Trending API 获取今日热门项目

    读取 state["plan"]["per_source_limit"] 决定抓取条数（由 Planner 节点给出）。
    """
    import urllib.request
    import urllib.parse

    sources: list[dict] = []
    plan = state.get("plan", {}) or {}
    per_source_limit = int(plan.get("per_source_limit", 10))

    github_token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    one_week_ago = (
        datetime.now(timezone.utc) - __import__("datetime").timedelta(days=7)
    ).strftime("%Y-%m-%d")
    query = f"ai agent llm stars:>100 pushed:>{one_week_ago}"
    url = (
        f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}"
        f"&sort=stars&per_page={per_source_limit}"
    )

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())

        for repo in data.get("items", []):
            sources.append({
                "source": "github",
                "title": repo["full_name"],
                "url": repo["html_url"],
                "description": repo.get("description", ""),
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language", ""),
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
    except Exception as e:
        sources.append({
            "source": "github",
            "title": "[ERROR] GitHub API 请求失败",
            "url": "",
            "description": str(e),
            "stars": 0,
            "language": "",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"[Collector] 采集到 {len(sources)} 条原始数据")
    return {"sources": sources}


# ═══════════════════════════════════════════════════════════════════════════
# ③ Analyzer — 单条 LLM 分析
# ═══════════════════════════════════════════════════════════════════════════
def analyze_node(state: KBState) -> dict:
    """分析节点：对采集到的原始数据进行 LLM 分析

    为每条数据生成摘要 / 标签 / 相关性评分 / 分类 / 核心洞察。
    """
    sources = state["sources"]
    analyses: list[dict] = []
    tracker = state.get("cost_tracker", {})

    for item in sources:
        if item.get("title", "").startswith("[ERROR]"):
            continue

        prompt = f"""请分析以下技术项目/文章，用 JSON 格式返回：

项目名: {item['title']}
描述: {item.get('description', '无描述')}
来源: {item['source']}
URL: {item.get('url', '')}

请返回以下格式的 JSON:
{{
    "summary": "200字以内的中文技术摘要",
    "tags": ["标签1", "标签2", "标签3"],
    "relevance_score": 0.8,
    "category": "分类（如: llm, agent, rag, tool, framework）",
    "key_insight": "一句话核心洞察"
}}"""

        try:
            result, usage = chat_json(prompt)
            tracker = accumulate_usage(tracker, usage)

            analyses.append({
                **item,
                "summary": result.get("summary", ""),
                "tags": result.get("tags", []),
                "relevance_score": result.get("relevance_score", 0.5),
                "category": result.get("category", "other"),
                "key_insight": result.get("key_insight", ""),
            })
        except Exception as e:
            print(f"[Analyzer] 分析失败: {item['title']} - {e}")
            analyses.append({
                **item,
                "summary": f"分析失败: {e}",
                "tags": [],
                "relevance_score": 0.0,
                "category": "error",
                "key_insight": "",
            })

    print(f"[Analyzer] 完成 {len(analyses)} 条分析")
    return {"analyses": analyses, "cost_tracker": tracker}


# ═══════════════════════════════════════════════════════════════════════════
# ④ Reviewer — 五维度评分（只评估，不修改）
# ═══════════════════════════════════════════════════════════════════════════
# 权重写在代码里，不写在 prompt 里 —— 方便不改 prompt 调整权重
# 总分范围 0-10，通过阈值 7.0
REVIEWER_WEIGHTS = {
    "summary_quality": 0.25,  # 摘要质量
    "technical_depth": 0.25,  # 技术深度
    "relevance":       0.20,  # 相关性
    "originality":     0.15,  # 原创性
    "formatting":      0.15,  # 格式规范
}
REVIEWER_PASS_THRESHOLD = 7.0


def review_node(state: KBState) -> dict:
    """Reviewer 节点：对 analyses 进行 5 维度质量审核

    核心原则：**只评估不修改（Evaluate, don't modify）**。
    Reviewer 看到的是 Analyzer 输出的 analyses，不做任何改动，只给分 + 反馈。

    审核维度（每维 1-10 分）:
        1. summary_quality  - 摘要质量
        2. technical_depth  - 技术深度
        3. relevance        - 相关性
        4. originality      - 原创性
        5. formatting       - 格式规范

    Returns:
        review_passed, review_feedback, iteration, cost_tracker
    """
    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    tracker = state.get("cost_tracker", {})

    if not analyses:
        return {
            "review_passed": True,
            "review_feedback": "没有条目需要审核",
            "iteration": iteration + 1,
        }

    # 只审核前 5 条，控制 token 消耗
    sample = analyses[:5]

    prompt = f"""你是知识库质量审核员。请审核以下分析结果：

{json.dumps(sample, ensure_ascii=False, indent=2)}

请按以下维度评分（每项 1-10 分）：
1. summary_quality  - 摘要质量（准确、简洁、有洞察）
2. technical_depth  - 技术深度（原理分析、对比、实现细节）
3. relevance        - 相关性（与 AI/Agent 主题的匹配度）
4. originality      - 原创性（是否有独立见解）
5. formatting       - 格式规范（字段完整、标签清晰）

请用 JSON 格式回复：
{{
    "scores": {{
        "summary_quality": 8,
        "technical_depth": 6,
        "relevance": 9,
        "originality": 5,
        "formatting": 8
    }},
    "feedback": "具体的改进建议（指出弱项）",
    "weak_dimensions": ["technical_depth", "originality"]
}}

当前是第 {iteration + 1} 次审核。"""

    try:
        result, usage = chat_json(
            prompt,
            system="你是严格但公正的知识库质量审核员。给出具体、可操作的反馈。",
            temperature=0.1,  # 低温度保证评分一致性
        )
        tracker = accumulate_usage(tracker, usage)

        # 【关键设计】用代码重算加权总分，不信任模型算术
        scores = result.get("scores", {})
        weighted_total = sum(
            scores.get(dim, 0) * w for dim, w in REVIEWER_WEIGHTS.items()
        )
        weighted_total = round(weighted_total, 2)
        passed = weighted_total >= REVIEWER_PASS_THRESHOLD

        feedback = result.get("feedback", "")
        weak_dims = result.get("weak_dimensions", [])
        if weak_dims:
            feedback = f"[弱项: {', '.join(weak_dims)}] {feedback}"

        print(
            f"[Reviewer] 加权总分: {weighted_total}/10, "
            f"通过: {passed} (第 {iteration + 1} 次审核)"
        )

    except Exception as e:
        # LLM 调用失败时直接通过，不阻塞流程
        passed = True
        feedback = f"审核 LLM 调用失败: {e}，自动通过"
        print(f"[Reviewer] 审核失败，自动通过: {e}")

    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "iteration": iteration + 1,
        "cost_tracker": tracker,
    }


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ Reviser — 读 feedback，LLM 定向修改 analyses（只评估不修改的对立面）
# ═══════════════════════════════════════════════════════════════════════════
def revise_node(state: KBState) -> dict:
    """Reviser 节点：根据 Reviewer 反馈，定向修改 analyses

    核心原则：**只修改不评估（Modify, don't evaluate）**。
    Reviser 和 Reviewer 是两个独立的 Agent —— 这样做是为了避免 Reviewer 给自己高分。

    Reviser 读取 state["review_feedback"]，用 LLM 定向改 analyses 的弱项。
    修改后的 analyses 回流到 review_node 重新评分。
    """
    analyses = state.get("analyses", [])
    feedback = state.get("review_feedback", "")
    iteration = state.get("iteration", 0)
    tracker = state.get("cost_tracker", {})

    if not analyses or not feedback:
        print("[Reviser] 无可修改内容，跳过")
        return {}

    prompt = f"""你是知识库编辑。以下是审核员的反馈，请据此修改这些分析结果。

【审核反馈】
{feedback}

【当前分析结果】
{json.dumps(analyses, ensure_ascii=False, indent=2)}

【修改要求】
- 重点改进反馈中提到的弱项维度
- 保留已经不错的部分，不要过度修改
- 保持相同的字段结构和类型
- 返回修改后的 JSON 数组（和输入格式一致）"""

    try:
        improved, usage = chat_json(
            prompt,
            system="你是经验丰富的知识库编辑。根据反馈定向修改，不要过度发散。",
            temperature=0.4,  # 略高温度允许创造性改写
        )
        tracker = accumulate_usage(tracker, usage)

        if isinstance(improved, list) and improved:
            print(
                f"[Reviser] 定向修改 {len(improved)} 条 analyses (迭代 {iteration})"
            )
            return {"analyses": improved, "cost_tracker": tracker}
    except Exception as e:
        print(f"[Reviser] 修改失败: {e}，沿用原 analyses")

    return {"cost_tracker": tracker}


# ═══════════════════════════════════════════════════════════════════════════
# ⑥ Organizer — 整理入库（终点：过滤 + 去重 + 格式化 + 写盘）
# ═══════════════════════════════════════════════════════════════════════════
def organize_node(state: KBState) -> dict:
    """Organizer 节点：将通过审核的 analyses 整理成标准知识条目并入库

    这是工作流的**正常终点** —— 只有 Reviewer 通过后才会到达。

    职责:
        1. 按 plan.relevance_threshold 过滤低质条目
        2. URL 去重
        3. 格式化为标准 article 结构
        4. 写入 knowledge/articles/*.json
        5. 更新索引 index.json
    """
    analyses = state.get("analyses", [])
    plan = state.get("plan", {}) or {}
    tracker = state.get("cost_tracker", {})

    threshold = float(plan.get("relevance_threshold", 0.6))

    # Step 1: 相关性过滤
    qualified = [a for a in analyses if a.get("relevance_score", 0) >= threshold]

    # Step 2: URL 去重
    seen_urls: set[str] = set()
    unique: list[dict] = []
    for item in qualified:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)

    # Step 3: 格式化为标准 article
    articles: list[dict] = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for i, item in enumerate(unique):
        articles.append({
            "id": f"{today}-{i:03d}",
            "title": item.get("title", ""),
            "source": item.get("source", "unknown"),
            "url": item.get("url", ""),
            "collected_at": item.get("collected_at", ""),
            "summary": item.get("summary", ""),
            "tags": item.get("tags", []),
            "relevance_score": item.get("relevance_score", 0.5),
            "category": item.get("category", "other"),
            "key_insight": item.get("key_insight", ""),
        })

    print(f"[Organizer] 整理出 {len(articles)} 条知识条目（准备入库）")

    # Step 4: 写盘
    _save_articles_to_disk(articles, tracker)

    return {"articles": articles, "cost_tracker": tracker}


def _save_articles_to_disk(articles: list[dict], tracker: dict) -> None:
    """把 articles 写入 knowledge/articles/ 并更新 index.json"""
    if not articles:
        return

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    articles_dir = os.path.join(base_dir, "knowledge", "articles")
    os.makedirs(articles_dir, exist_ok=True)

    for article in articles:
        filepath = os.path.join(articles_dir, f"{article['id']}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(article, f, ensure_ascii=False, indent=2)

    # 更新索引
    index_path = os.path.join(articles_dir, "index.json")
    index: list[dict] = []
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)

    existing_ids = {entry["id"] for entry in index}
    for article in articles:
        if article["id"] not in existing_ids:
            index.append({
                "id": article["id"],
                "title": article["title"],
                "category": article.get("category", "other"),
                "relevance_score": article.get("relevance_score", 0.5),
            })

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"[Organizer] 已写入 {len(articles)} 篇到磁盘")
    print(f"[Organizer] 本次运行总成本: ¥{tracker.get('total_cost_yuan', 0)}")


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ HumanFlag — 人工介入（非正常终点）
# ═══════════════════════════════════════════════════════════════════════════
def human_flag_node(state: KBState) -> dict:
    """HumanFlag 节点：循环超过 max_iterations 仍未通过时走到这里

    职责:
        1. 记录审核链路（当前迭代数、最后一次反馈）
        2. 把 analyses 写入 knowledge/pending_review/ 目录
        3. 设置 needs_human_review=True 让外层知道需要人工
        4. END（不再保存到 articles/）
    """
    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")
    plan = state.get("plan", {}) or {}
    max_iter = int(plan.get("max_iterations", 3))

    print(
        f"[HumanFlag] ⚠️ 达到 {max_iter} 次审核仍未通过，标记人工介入"
    )
    print(f"[HumanFlag] 最后反馈: {feedback[:200]}")

    # 写入 pending_review 目录（不污染 articles/）
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pending_dir = os.path.join(base_dir, "knowledge", "pending_review")
    os.makedirs(pending_dir, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    filepath = os.path.join(pending_dir, f"pending-{today}.json")
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": today,
                "iterations_used": iteration,
                "max_iterations": max_iter,
                "last_feedback": feedback,
                "analyses": analyses,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"[HumanFlag] 已保存到 {filepath}，等待人工审核")

    return {"needs_human_review": True}
