#!/usr/bin/env python3
"""Validate the current V3 checkpoint without invoking a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from preprod import load_project, sha256
from validate_analysis import validate_analysis
from validate_catalogs import validate_catalogs
from validate_ingest import validate_project
from validate_render import validate_render


def validate_pipeline(project_dir: Path) -> list[str]:
    project_dir = project_dir.resolve()
    errors = validate_project(project_dir)
    if errors:
        return errors
    try:
        state = json.loads((project_dir / "data" / "pipeline" / "state.json").read_text(encoding="utf-8"))
        _, manifest = load_project(project_dir)
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"pipeline state unreadable: {exc}"]
    if state.get("schema_version") != "3.0.0":
        errors.append("pipeline state schema_version must be 3.0.0")
    by_id = {item["chapter_id"]: item for item in state.get("chapters", [])}
    ready = []
    for chapter in manifest["chapters"]:
        chapter_id = chapter["chapter_id"]
        record = project_dir / "data" / "chapters" / f"{chapter_id.lower()}.json"
        if not record.exists():
            continue
        chapter_errors = validate_analysis(project_dir, chapter_id)
        if chapter_errors:
            if by_id.get(chapter_id, {}).get("status") != "repair":
                errors.append(f"{chapter_id}: invalid record is not routed to repair")
        else:
            ready.append(chapter_id)
            errors.extend(f"{chapter_id}: {value}" for value in validate_render(project_dir, chapter_id))
    if ready:
        errors.extend(validate_catalogs(project_dir))
    task = state.get("next_task")
    task_path = project_dir / "work" / "pipeline" / "next-task.json"
    if task is None:
        if task_path.exists():
            errors.append("next-task.json exists but state has no next task")
    else:
        if not task_path.exists():
            errors.append("next task file is missing")
        elif json.loads(task_path.read_text(encoding="utf-8")) != task:
            errors.append("next task file differs from state")
        input_path = project_dir / task["input_path"]
        if not input_path.exists() or sha256(input_path.read_bytes()) != task["input_sha256"]:
            errors.append("next task input hash mismatch")
        if task["budget"]["rule_and_context_overhead_bytes"] > 8192 and task["task_type"] == "analyze_chapter":
            errors.append("chapter task exceeds the 8 KB rule/context overhead target")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 V3 断点、章节和全局交付")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    errors = validate_pipeline(args.project_dir)
    if errors:
        print("PIPELINE VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("PIPELINE VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
