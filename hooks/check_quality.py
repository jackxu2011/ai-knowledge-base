#!/usr/bin/env python3
"""Knowledge entry quality scorer.

Usage:
    python hooks/check_quality.py <json_file> [json_file2 ...]
    python hooks/check_quality.py knowledge/articles/*.json

Exit codes:
    0 - all files grade B or above
    1 - one or more files grade C
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DimensionScore:
    dimension: str
    score: float
    max_score: float
    detail: str


@dataclass
class QualityReport:
    file: Path
    grade: str
    total_score: float
    dimensions: list[DimensionScore]
    errors: list[str] = field(default_factory=list)


BUZZWORD_ZH = frozenset([
    "赋能", "抓手", "闭环", "打通", "全链路",
    "底层逻辑", "颗粒度", "对齐", "拉通", "沉淀",
    "强大的", "革命性的", "颠覆性的", "引爆点",
    "私域", "增量", "变量", "抓手", "赋能",
])

BUZZWORD_EN = frozenset([
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "best-in-class", "world-class",
    "next-generation", "state-of-the-art", " paradigm-shifting",
    "disruptive", "bleeding-edge", "industry-leading",
])

TECH_KEYWORDS = frozenset([
    "api", "sdk", "llm", "gpt", "agent", "rag",
    "embedding", "vector", "fine-tuning", "rlhf",
    "transformer", "attention", "inference",
    "deployment", "benchmark", "latency", "throughput",
    "python", "javascript", "typescript", "rust", "go",
    "kubernetes", "docker", "microservice", "api",
    "openai", "anthropic", "claude", "gemini",
    "agent", "tool", "memory", "planner", "executor",
    "retrieval", "chunk", "index", "embedding",
])

VALID_STATUSES = frozenset(["draft", "review", "published", "archived"])

REQUIRED_FIELDS_SCORE = {"id", "title", "source_url", "status"}
TIME_FIELDS = {"published_at", "collected_at"}


def score_summary(text: str) -> tuple[float, str]:
    length = len(text)
    detail_parts = []

    if length >= 50:
        base = 20
        detail_parts.append(f"长度 {length} >= 50字 (20分)")
    elif length >= 20:
        base = 10
        detail_parts.append(f"长度 {length} >= 20字 (10分)")
    else:
        base = 0
        detail_parts.append(f"长度 {length} < 20字 (0分)")
        return base, "; ".join(detail_parts)

    text_lower = text.lower()
    tech_kw_found = TECH_KEYWORDS & {w for w in text_lower.split() if len(w) > 2}
    if tech_kw_found:
        bonus = min(5, len(tech_kw_found))
        base += bonus
        detail_parts.append(f"含技术词 {tech_kw_found} (+{bonus}分)")

    detail_parts.append(f"小计 {base}/25分")
    return base, "; ".join(detail_parts)


def score_technical_depth(data: dict) -> tuple[float, str]:
    analysis = data.get("analysis", {})
    article_score = analysis.get("score")

    if article_score is None:
        return 0, "无 score 字段 (0分)"

    try:
        s = float(article_score)
    except (TypeError, ValueError):
        return 0, f"score 非数字: {article_score!r} (0分)"

    if not (1 <= s <= 10):
        return 0, f"score 超范围 {s} (需 1-10) (0分)"

    mapped = round(s / 10 * 25, 1)
    return mapped, f"score={s} -> {mapped}/25分"


def score_format_compliance(data: dict) -> tuple[float, str]:
    score = 0.0
    parts = []

    for f in REQUIRED_FIELDS_SCORE:
        if f in data and data[f] and str(data[f]).strip():
            score += 4
            parts.append(f"{f} ✓ (+4)")
        else:
            parts.append(f"{f} ✗ (0)")

    for f in TIME_FIELDS:
        v = data.get(f)
        if v and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", str(v)):
            score += 4
            parts.append(f"{f} ✓ (+4)")
        else:
            parts.append(f"{f} ✗ (0)")

    score = min(score, 20.0)
    return score, "; ".join(parts)


def score_tag_precision(data: dict) -> tuple[float, str]:
    tags = data.get("tags")
    if tags is None:
        analysis = data.get("analysis", {})
        tags = analysis.get("tags", [])

    if not isinstance(tags, list) or not tags:
        return 0, "无标签 (0分)"

    n = len(tags)
    if 1 <= n <= 3:
        tag_score = 15
        detail = f"{n} 个标签 (满分)"
    elif n > 3:
        tag_score = max(0, 15 - (n - 3) * 3)
        detail = f"{n} 个标签 ({- (n-3)*3}扣分) = {tag_score}"
    else:
        tag_score = 0
        detail = f"{n} 个标签 (0分)"

    return tag_score, detail


def score_fluff(texts: list[str]) -> tuple[float, str]:
    total_buzz = 0
    found = []

    for text in texts:
        if not isinstance(text, str):
            continue
        t = text.lower()
        for bw in BUZZWORD_ZH:
            if bw in t:
                found.append(f"中文: {bw}")
                total_buzz += 1
        for bw in BUZZWORD_EN:
            if bw.lower() in t:
                found.append(f"英文: {bw}")
                total_buzz += 1

    if total_buzz == 0:
        return 15, "无空洞词 (满分)"

    penalty = min(15, total_buzz * 3)
    score = 15 - penalty
    unique_found = ", ".join(set(found))
    return max(0, score), f"检测到 {total_buzz} 处空洞词 ({penalty}扣分): {unique_found}"


def assess_quality(path: Path) -> QualityReport:
    errors = []

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        errors.append(f"JSON解析错误: {e.msg}")
        return QualityReport(path, "C", 0, [], errors)

    if not isinstance(data, dict):
        errors.append("根类型非 object")
        return QualityReport(path, "C", 0, [], errors)

    summary = data.get("summary", "")
    dims = []

    s_score, s_detail = score_summary(summary)
    dims.append(DimensionScore("摘要质量", s_score, 25, s_detail))

    t_score, t_detail = score_technical_depth(data)
    dims.append(DimensionScore("技术深度", t_score, 25, t_detail))

    f_score, f_detail = score_format_compliance(data)
    dims.append(DimensionScore("格式规范", f_score, 20, f_detail))

    tp_score, tp_detail = score_tag_precision(data)
    dims.append(DimensionScore("标签精度", tp_score, 15, tp_detail))

    texts_to_check = [
        summary,
        data.get("title", ""),
        data.get("analysis", {}).get("scoring_reasons", ""),
    ]
    tags = data.get("tags") or data.get("analysis", {}).get("tags", [])
    if isinstance(tags, list):
        texts_to_check.extend(str(t) for t in tags)

    fluff_score, fluff_detail = score_fluff(texts_to_check)
    dims.append(DimensionScore("空洞词检测", fluff_score, 15, fluff_detail))

    total = sum(d.score for d in dims)

    if total >= 80:
        grade = "A"
    elif total >= 60:
        grade = "B"
    else:
        grade = "C"

    return QualityReport(path, grade, total, dims, errors)


def grade_to_str(grade: str) -> str:
    colors = {"A": "\033[92m", "B": "\033[93m", "C": "\033[91m"}
    reset = "\033[0m"
    return f"{colors.get(grade, '')}{grade}{reset}"


def print_report(report: QualityReport, verbose: bool = False) -> None:
    grade_str = grade_to_str(report.grade)
    status = "FAIL" if report.grade == "C" else "PASS"
    print(f"[{status}] {report.file.name}  {grade_str}  {report.total_score:.1f}/100")

    if verbose:
        for d in report.dimensions:
            pct = d.score / d.max_score * 100 if d.max_score else 0
            bar_len = int(pct / 100 * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"  {d.dimension:<12} {bar} {d.score:5.1f}/{d.max_score}  {d.detail}")

    if report.errors:
        for e in report.errors:
            print(f"  ERROR: {e}")


def expand_paths(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for p in paths:
        if "*" in p or "?" in p:
            result.extend(sorted(Path().glob(p)))
        else:
            result.append(Path(p))
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python hooks/check_quality.py <json_file> [json_file2 ... | *.json]", file=sys.stderr)
        sys.exit(1)

    paths = expand_paths(argv[1:])
    if not paths:
        print("No files matched.", file=sys.stderr)
        sys.exit(1)

    total = len(paths)
    any_c = False

    for i, p in enumerate(paths):
        report = assess_quality(p)
        if report.grade == "C":
            any_c = True

        pct = (i + 1) / total * 100
        bar = "=" * int(pct / 2) + ">" + " " * (50 - int(pct / 2))
        suffix = f"{i+1}/{total} ({pct:.0f}%) [{bar}] {p.name}"
        print(f"\r{suffix}", end="", flush=True)

        print()
        print_report(report, verbose=True)
        print()

    print(f"\r{' ' * 80}\r", end="")

    grade_counts = {"A": 0, "B": 0, "C": 0}
    all_scores = []
    for p in paths:
        r = assess_quality(p)
        grade_counts[r.grade] += 1
        all_scores.append(r.total_score)

    avg = sum(all_scores) / len(all_scores)
    print(f"Summary: {grade_counts['A']}A {grade_counts['B']}B {grade_counts['C']}C  avg={avg:.1f}")

    return 1 if any_c else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))