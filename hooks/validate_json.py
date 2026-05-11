#!/usr/bin/env python3
"""Knowledge entry JSON validator.

Usage:
    python hooks/validate_json.py <json_file> [json_file2 ...]
    python hooks/validate_json.py knowledge/articles/*.json

Exit codes:
    0 - all files passed
    1 - one or more files failed (with error list + summary)
"""

import json
import re
import sys
from pathlib import Path
from typing import Callable


REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "status": str,
}

OPTIONAL_FIELDS: dict[str, type] = {
    "tags": list,
}

VALID_STATUSES = {"draft", "review", "published", "archived"}

VALID_AUDIENCES = {"beginner", "intermediate", "advanced"}

ID_PATTERN = re.compile(r"^[a-z_]+-\d{8}-\d{3}$")

URL_PATTERN = re.compile(r"^https?://")

MIN_SUMMARY_LEN = 20


def validate_file(path: Path) -> list[str]:
    errors = []

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as e:
        return [f"JSON parse error: {e.msg} (line {e.lineno}, col {e.colno})"]
    except OSError as e:
        return [f"file read error: {e.strerror}"]

    if not isinstance(data, dict):
        return ["root must be a JSON object, not array or other type"]

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"missing required field: '{field}'")
        elif not isinstance(data[field], expected_type):
            actual = type(data[field]).__name__
            errors.append(f"field '{field}' must be {expected_type.__name__}, got {actual}")

    for field, expected_type in OPTIONAL_FIELDS.items():
        if field not in data:
            if field == "tags":
                analysis = data.get("analysis")
                if not (analysis and isinstance(analysis, dict) and isinstance(analysis.get("tags"), list)):
                    errors.append(f"missing required field: 'tags' (checked top-level and analysis.tags)")
        elif not isinstance(data[field], expected_type):
            actual = type(data[field]).__name__
            errors.append(f"field '{field}' must be {expected_type.__name__}, got {actual}")

        if field == "tags":
            tags_val = data.get("tags")
            if tags_val is None:
                analysis = data.get("analysis")
                if isinstance(analysis, dict):
                    tags_val = analysis.get("tags")
            if isinstance(tags_val, list) and len(tags_val) < 1:
                errors.append("tags must contain at least 1 tag")

    if "id" in data and isinstance(data["id"], str):
        if not ID_PATTERN.match(data["id"]):
            errors.append(
                f"id '{data['id']}' does not match format {{source}}-{{YYYYMMDD}}-{{NNN}} "
                "(e.g. github-20260317-001)"
            )

    if "status" in data and isinstance(data["status"], str):
        if data["status"] not in VALID_STATUSES:
            errors.append(
                f"status '{data['status']}' must be one of: {', '.join(sorted(VALID_STATUSES))}"
            )

    if "source_url" in data and isinstance(data["source_url"], str):
        if not URL_PATTERN.match(data["source_url"]):
            errors.append(f"source_url '{data['source_url']}' must start with http:// or https://")

    if "summary" in data and isinstance(data["summary"], str):
        if len(data["summary"]) < MIN_SUMMARY_LEN:
            errors.append(
                f"summary must be at least {MIN_SUMMARY_LEN} characters, got {len(data['summary'])}"
            )

    analysis = data.get("analysis")
    if analysis and isinstance(analysis, dict):
        score = analysis.get("score")
        if score is not None:
            if not isinstance(score, (int, float)):
                errors.append(f"analysis.score must be numeric, got {type(score).__name__}")
            elif not (1 <= score <= 10):
                errors.append(f"analysis.score must be 1-10, got {score}")

        audience = analysis.get("audience")
        if audience is not None and audience not in VALID_AUDIENCES:
            errors.append(
                f"analysis.audience must be one of: {', '.join(sorted(VALID_AUDIENCES))}, got '{audience}'"
            )

    return errors


def expand_paths(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    for p in paths:
        path = Path(p)
        if "*" in p or "?" in p:
            result.extend(sorted(Path().glob(p)))
        else:
            result.append(path)
    return result


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python hooks/validate_json.py <json_file> [json_file2 ... | *.json]", file=sys.stderr)
        sys.exit(1)

    paths = expand_paths(argv[1:])
    if not paths:
        print("No files matched.", file=sys.stderr)
        sys.exit(1)

    total_files = 0
    total_passed = 0
    total_failed = 0
    total_errors = 0

    for path in paths:
        total_files += 1
        errors = validate_file(path)
        if errors:
            total_failed += 1
            total_errors += len(errors)
            print(f"FAIL: {path}")
            for e in errors:
                print(f"  - {e}")
        else:
            total_passed += 1
            print(f"PASS: {path}")

    print()
    print(f"Summary: {total_passed} passed, {total_failed} failed, {total_files} total")
    print(f"Total errors: {total_errors}")

    return 1 if total_failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))