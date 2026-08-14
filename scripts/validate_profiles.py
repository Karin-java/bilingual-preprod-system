#!/usr/bin/env python3
"""Validate deterministic profile outputs and evidence links."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_profiles import REQUIRED_FIELDS, build_profile_data


def validate_profiles(project_dir: Path) -> list[str]:
    project_dir = project_dir.resolve()
    path = project_dir / "data" / "profiles" / "profiles.json"
    if not path.exists():
        return ["profile bundle is missing"]
    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
        expected, profile_md, recap_md = build_profile_data(project_dir)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"profile inputs are invalid: {exc}"]
    errors: list[str] = []
    if actual != expected:
        errors.append("profile bundle is stale or non-deterministic")
    if actual.get("required_fields") != REQUIRED_FIELDS:
        errors.append("required profile fields are incomplete or reordered")
    character_ids: set[str] = set()
    for profile in actual.get("profiles", []):
        character_id = profile.get("character_id")
        if character_id in character_ids:
            errors.append(f"duplicate profile {character_id}")
        character_ids.add(character_id)
        fields = profile.get("fields", {})
        if list(fields) != REQUIRED_FIELDS:
            errors.append(f"{character_id}: profile fields are incomplete or reordered")
        for field_name, field in fields.items():
            status = field.get("status")
            if status == "unknown" and (field.get("value_zh") is not None or field.get("facts") or field.get("fact_ids")):
                errors.append(f"{character_id}.{field_name}: unknown field must not contain fabricated facts")
            if status != "unknown" and not field.get("value_zh"):
                errors.append(f"{character_id}.{field_name}: resolved field needs a display value")
    for task in actual.get("recap_tasks", []):
        if task.get("character_id") not in character_ids:
            errors.append(f"{task.get('task_id')}: unknown character")
        if len({field.get("field") for field in task.get("fields", [])}) != len(task.get("fields", [])):
            errors.append(f"{task.get('task_id')}: duplicate pending profile fields")
        for clue in task.get("context_clues", []):
            if clue.get("translation_zh") == "译文待补充":
                errors.append(f"{task.get('task_id')}: clue translation is missing")
    profile_path = project_dir / "deliverables" / "角色基础信息档案.md"
    recap_path = project_dir / "work" / "recap" / "角色资料复盘清单.md"
    if not profile_path.exists() or profile_path.read_text(encoding="utf-8") != profile_md:
        errors.append("human-readable profile Markdown is stale")
    if not recap_path.exists() or recap_path.read_text(encoding="utf-8") != recap_md:
        errors.append("recap Markdown is stale")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验跨章角色档案与复盘清单")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    errors = validate_profiles(args.project_dir)
    if errors:
        print("PROFILE VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    data = json.loads((args.project_dir.resolve() / "data" / "profiles" / "profiles.json").read_text(encoding="utf-8"))
    print(f"PROFILE VALIDATION PASSED: {len(data['profiles'])} profiles; {len(data['recap_tasks'])} recap tasks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
