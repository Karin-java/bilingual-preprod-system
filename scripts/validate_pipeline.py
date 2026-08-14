#!/usr/bin/env python3
"""Validate a resumable pipeline snapshot, its next task and user progress view."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from run_pipeline import PROGRESS_PATH, STATE_PATH, TASK_PATH, TOKEN_METHOD, active_recap_events, load_recap_events, render_progress, sha256, task_with_hash
from validate_analysis import validate_analysis
from validate_appearance_reports import validate_appearance_reports
from validate_ingest import validate_project
from validate_profiles import validate_profiles
from validate_registries import validate_registries
from validate_render import validate_render


def descriptor_errors(project_dir: Path, value: Any, context: str) -> list[str]:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        return [f"{context}: invalid input descriptor"]
    path = project_dir / value["path"]
    if not path.exists():
        return [f"{context}: referenced file is missing"]
    if sha256(path.read_bytes()) != value["sha256"]:
        return [f"{context}: file hash mismatch"]
    return []


def validate_pipeline(project_dir: Path) -> list[str]:
    project_dir = project_dir.resolve()
    errors = [f"ingest: {item}" for item in validate_project(project_dir)]
    state_path = project_dir / STATE_PATH
    progress_path = project_dir / PROGRESS_PATH
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        progress = progress_path.read_text(encoding="utf-8")
        project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return errors + [f"pipeline outputs unreadable: {exc}"]
    if state.get("schema_version") != "1.0.0" or state.get("pipeline_version") != "preprod-pipeline-v1":
        errors.append("pipeline state version is invalid")
    if state.get("project_id") != project.get("project_id"):
        errors.append("pipeline project ID mismatch")
    errors.extend(descriptor_errors(project_dir, state.get("source_manifest"), "source manifest"))
    chapters = state.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        errors.append("pipeline chapters are missing")
        chapters = []
    chapter_ids: set[str] = set()
    for record in chapters:
        if not isinstance(record, dict):
            errors.append("chapter state is not an object")
            continue
        chapter_id = record.get("chapter_id")
        if chapter_id in chapter_ids:
            errors.append(f"duplicate chapter state {chapter_id}")
        chapter_ids.add(chapter_id)
        status = record.get("status")
        if record.get("packet") is not None:
            errors.extend(descriptor_errors(project_dir, record["packet"], f"{chapter_id} packet"))
        if status == "ready":
            for field in ("analysis", "resolved_view", "render"):
                errors.extend(descriptor_errors(project_dir, record.get(field), f"{chapter_id} {field}"))
            analysis_errors = validate_analysis(project_dir, chapter_id)
            render_errors = validate_render(project_dir, chapter_id)
            if analysis_errors:
                errors.append(f"{chapter_id}: state says ready but analysis is invalid")
            if render_errors:
                errors.append(f"{chapter_id}: state says ready but render is invalid")
        elif status == "waiting_analysis":
            if record.get("analysis") is not None:
                errors.append(f"{chapter_id}: waiting chapter unexpectedly has analysis descriptor")
        elif status == "needs_repair":
            if record.get("analysis") is None or not record.get("errors"):
                errors.append(f"{chapter_id}: repair state lacks analysis or errors")
            elif record.get("analysis") is not None:
                errors.extend(descriptor_errors(project_dir, record["analysis"], f"{chapter_id} analysis"))
        else:
            errors.append(f"{chapter_id}: invalid pipeline chapter status")

    outputs = state.get("global_outputs", {})
    validators = {"registries": validate_registries, "profiles": validate_profiles, "appearances": validate_appearance_reports}
    for name, validator in validators.items():
        item = outputs.get(name)
        if not isinstance(item, dict) or item.get("status") not in {"not_available", "reused", "rebuilt"}:
            errors.append(f"{name}: invalid global output state")
            continue
        if item["status"] == "not_available":
            if item.get("path") is not None or item.get("sha256") is not None:
                errors.append(f"{name}: unavailable output must have null path and hash")
        else:
            errors.extend(descriptor_errors(project_dir, {"path": item.get("path"), "sha256": item.get("sha256")}, name))
            if validator(project_dir):
                errors.append(f"{name}: global output validation failed")

    try:
        events, event_data = load_recap_events(project_dir)
        active, retracted = active_recap_events(events)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"recap review log is invalid: {exc}")
        events, event_data, active, retracted = [], b"", [], []
    event_log = state.get("recap_review_log", {})
    if event_log.get("sha256") != sha256(event_data):
        errors.append("recap review log hash mismatch")
    if event_log.get("active_event_ids") != [event["event_id"] for event in active] or event_log.get("retracted_event_ids") != retracted:
        errors.append("recap review event state mismatch")

    task = state.get("next_task")
    task_path = project_dir / TASK_PATH
    if task is None:
        if task_path.exists():
            errors.append("completed pipeline retained a stale next-task file")
        if state.get("status") != "complete":
            errors.append("non-complete pipeline must expose one next task")
    else:
        if state.get("status") == "complete":
            errors.append("complete pipeline cannot expose a next task")
        try:
            task_file = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"next-task file is unreadable: {exc}")
            task_file = None
        if task_file != task:
            errors.append("next-task file differs from pipeline state")
        expected = task_with_hash({key: value for key, value in task.items() if key != "task_sha256"})
        if expected != task:
            errors.append("next task hash mismatch")
        input_path = project_dir / task.get("input_path", "")
        if not input_path.exists() or sha256(input_path.read_bytes()) != task.get("input_sha256"):
            errors.append("next task input is missing or stale")
        metrics = state.get("last_run", {})
        for field in ("input_utf8_bytes", "estimated_input_tokens_min", "estimated_input_tokens_max"):
            metric_name = "agent_" + field if field == "input_utf8_bytes" else field
            if metrics.get(metric_name) != task.get(field):
                errors.append(f"next task {field} differs from last-run metrics")
    metrics = state.get("last_run", {})
    if metrics.get("token_estimate_method") != TOKEN_METHOD:
        errors.append("token estimate method is missing or changed")
    if progress != render_progress(state):
        errors.append("human-readable pipeline progress is stale")
    expected_project_state = "validated" if state.get("status") == "complete" else "rendered" if chapters and all(item.get("status") == "ready" for item in chapters) else "structured" if any(item.get("status") == "ready" for item in chapters) else "ingested"
    if project.get("state") != expected_project_state:
        errors.append("project state does not match pipeline progress")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验断点续跑状态、单任务队列、指纹和用户进度表")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    errors = validate_pipeline(args.project_dir)
    if errors:
        print("PIPELINE VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    state = json.loads((args.project_dir.resolve() / STATE_PATH).read_text(encoding="utf-8"))
    print(f"PIPELINE VALIDATION PASSED: {state['status']}; one bounded next task or complete state is consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
