#!/usr/bin/env python3
"""Validate deterministic bilingual Markdown outputs for one chapter."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from render_chapter import issue_evidence, load_current_analysis, objects_by_scope, segment_index, translated_context


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_render(project_dir: Path, chapter_id: str) -> list[str]:
    project_dir = project_dir.resolve()
    errors: list[str] = []
    try:
        analysis, analysis_path, analysis_data = load_current_analysis(project_dir, chapter_id)
        manifest_path = project_dir / "data" / "render" / f"{chapter_id.lower()}.render-manifest.json"
        manifest = load_json(manifest_path)
        script_path = project_dir / manifest["script_path"]
        review_path = project_dir / manifest["review_path"]
        script_data = script_path.read_bytes()
        review_data = review_path.read_bytes()
        script = script_data.decode("utf-8")
        review = review_data.decode("utf-8")
    except (OSError, ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"render inputs unreadable: {exc}"]

    if manifest.get("schema_version") != "1.0.0" or manifest.get("renderer_version") != "bilingual-markdown-v1":
        errors.append("render manifest version is invalid")
    if manifest.get("chapter_id") != chapter_id:
        errors.append("render manifest chapter mismatch")
    if manifest.get("analysis_path") != analysis_path.relative_to(project_dir).as_posix():
        errors.append("render manifest analysis path mismatch")
    if manifest.get("analysis_sha256") != sha256(analysis_data):
        errors.append("render input analysis hash mismatch")
    if manifest.get("source_sha256") != analysis.get("source_sha256"):
        errors.append("render source hash mismatch")
    if manifest.get("script_sha256") != sha256(script_data):
        errors.append("bilingual script hash mismatch")
    if manifest.get("review_sha256") != sha256(review_data):
        errors.append("review sheet hash mismatch")
    issue_ids = [item["issue_id"] for item in analysis["review"]["issues"]]
    if manifest.get("pending_issue_ids") != issue_ids:
        errors.append("render pending issue list mismatch")

    all_segments = [segment for unit in analysis["unit_analyses"] for segment in unit["segments"]]
    for segment in all_segments:
        marker = f"<!-- {segment['segment_id']} -->"
        marker_at = script.find(marker)
        if marker_at < 0:
            errors.append(f"script is missing segment marker {segment['segment_id']}")
            continue
        next_marker = script.find("<!-- ", marker_at + len(marker))
        block = script[marker_at:] if next_marker < 0 else script[marker_at:next_marker]
        if segment["source_text"] not in block:
            errors.append(f"script changed or omitted English source {segment['segment_id']}")
        if segment["translation"]["text_zh"] not in block:
            errors.append(f"script omitted Chinese translation {segment['segment_id']}")
    for scene in analysis["scenes"]:
        if scene["scene_id"] not in script:
            errors.append(f"script is missing scene {scene['scene_id']}")
        for beat in scene["beats"]:
            if beat["beat_id"] not in script:
                errors.append(f"script is missing beat {beat['beat_id']}")

    _, by_unit = segment_index(analysis)
    targets = objects_by_scope(analysis)
    for issue in analysis["review"]["issues"]:
        if issue["issue_id"] not in review:
            errors.append(f"review sheet is missing issue {issue['issue_id']}")
            continue
        target = targets[(issue["scope_type"], issue["scope_id"])]
        evidence = issue_evidence(issue, target)
        if not evidence:
            evidence = [{"source_unit_id": unit_id, "quote": by_unit.get(unit_id, [{}])[0].get("source_text", "")} for unit_id in issue["source_unit_ids"]]
        for item in evidence:
            _, context_zh = translated_context(item["source_unit_id"], item["quote"], by_unit)
            if item["quote"] not in review:
                errors.append(f"review issue {issue['issue_id']} omitted English evidence")
            if context_zh not in review:
                errors.append(f"review issue {issue['issue_id']} omitted Chinese evidence translation")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验中英 Markdown 剧本和双语人工验收清单")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True)
    args = parser.parse_args()
    errors = validate_render(args.project_dir, args.chapter)
    if errors:
        print("RENDER VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("RENDER VALIDATION PASSED: English source, Chinese translations, scenes, beats, review evidence and hashes are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
