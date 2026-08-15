#!/usr/bin/env python3
"""Verify deterministic V3 Markdown outputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from preprod import sha256
from render_chapter import render_markdown, render_review
from review_analysis import load_current_analysis
from validate_analysis import validate_record


def validate_render(project_dir: Path, chapter_id: str) -> list[str]:
    project_dir = project_dir.resolve()
    try:
        record, current_path, current_data = load_current_analysis(project_dir, chapter_id)
        errors = validate_record(project_dir, chapter_id, record)
        if errors:
            return errors
        script_path = project_dir / "deliverables" / "scripts_bilingual" / f"{chapter_id.lower()}.md"
        review_path = project_dir / "work" / "review" / f"{chapter_id.lower()}.review.md"
        manifest_path = project_dir / "data" / "render" / f"{chapter_id.lower()}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        script_data = script_path.read_bytes()
        review_data = review_path.read_bytes()
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"render output unreadable: {exc}"]
    errors = []
    if script_data != render_markdown(record).encode("utf-8"):
        errors.append("bilingual script is stale or modified")
    if review_data != render_review(record).encode("utf-8"):
        errors.append("review sheet is stale or modified")
    if manifest.get("chapter_input") != {"path": current_path.relative_to(project_dir).as_posix(), "sha256": sha256(current_data)}:
        errors.append("render manifest input mismatch")
    expected = [
        {"path": script_path.relative_to(project_dir).as_posix(), "sha256": sha256(script_data)},
        {"path": review_path.relative_to(project_dir).as_posix(), "sha256": sha256(review_data)},
    ]
    if manifest.get("outputs") != expected:
        errors.append("render manifest output mismatch")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 V3 单章 Markdown")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True)
    args = parser.parse_args()
    errors = validate_render(args.project_dir, args.chapter)
    if errors:
        print("RENDER VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("RENDER VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
