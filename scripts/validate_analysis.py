#!/usr/bin/env python3
"""Validate one chapter's semantic segments, scenes, beats and evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from validate_ingest import validate_project

TEXT_TYPES_WITH_SPEAKER = {"dialogue", "inner_thought", "voice_over"}
TEXT_TYPES = {
    "chapter_heading", "scene_heading", "action", "narration", "dialogue",
    "inner_thought", "voice_over", "letter", "on_screen_text", "transition", "other",
}
BEAT_TYPES = {"opening", "goal", "conflict", "revelation", "emotion", "power_shift", "character_entry", "action", "other"}
STATUSES = {"explicit", "inferred", "user_confirmed", "conflict", "unknown"}
GENERIC_LOCATIONS = {"某处", "未知地点", "日常卧室", "室内场景", "室外场景", "普通场景"}
CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_evidence(value: Any, units: dict[str, str], context: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{context}: evidence must be a non-empty array")
        return
    for index, evidence in enumerate(value, 1):
        if not isinstance(evidence, dict):
            errors.append(f"{context}: evidence {index} is not an object")
            continue
        unit_id = evidence.get("source_unit_id")
        quote = evidence.get("quote")
        if unit_id not in units:
            errors.append(f"{context}: evidence references unknown unit {unit_id}")
        elif not isinstance(quote, str) or not quote:
            errors.append(f"{context}: evidence quote is empty")
        elif quote not in units[unit_id]:
            errors.append(f"{context}: evidence quote is not present in {unit_id}")


def check_confidence(value: Any, context: str, errors: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        errors.append(f"{context}: confidence must be between 0 and 1")


def check_assessment(
    value: Any,
    allowed_values: set[str],
    units: dict[str, str],
    context: str,
    errors: list[str],
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{context}: assessment is not an object")
        return True
    if value.get("value") not in allowed_values:
        errors.append(f"{context}: invalid value {value.get('value')}")
    status = value.get("status")
    if status not in STATUSES:
        errors.append(f"{context}: invalid status {status}")
    check_confidence(value.get("confidence"), context, errors)
    check_evidence(value.get("evidence"), units, context, errors)
    return value.get("value") == "unknown" or status in {"unknown", "conflict"}


def validate_analysis(project_dir: Path, chapter_id: str) -> list[str]:
    project_dir = project_dir.resolve()
    errors = [f"ingest: {error}" for error in validate_project(project_dir)]
    if errors:
        return errors
    try:
        project = load_json(project_dir / "project.json")
        manifest = load_json(project_dir / project["source_manifest"])
        chapter = next(item for item in manifest["chapters"] if item["chapter_id"] == chapter_id)
        packet_path = project_dir / "work" / "analysis" / f"{chapter_id.lower()}.packet.json"
        packet_bytes = packet_path.read_bytes()
        packet = json.loads(packet_bytes.decode("utf-8"))
        analysis = load_json(project_dir / packet["output_path"])
        unit_rows = (project_dir / chapter["units_path"]).read_text(encoding="utf-8").splitlines()
        source_units = [json.loads(line) for line in unit_rows]
    except (OSError, StopIteration, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"analysis inputs unreadable: {exc}"]

    source_ids = [unit["unit_id"] for unit in source_units]
    source_texts = {unit["unit_id"]: unit["source_text"] for unit in source_units}
    packet_hash = hashlib.sha256(packet_bytes).hexdigest()
    hash_path = packet_path.with_suffix(".sha256")
    if hash_path.is_file() and hash_path.read_text(encoding="ascii").strip() != packet_hash:
        errors.append("packet sidecar hash mismatch")
    if packet.get("chapter_id") != chapter_id or packet.get("source_unit_ids") != source_ids or packet.get("source_sha256") != chapter["sha256"]:
        errors.append("packet does not match the ingested chapter")
    if analysis.get("schema_version") != "2.0.0" or analysis.get("analysis_method") != "agent-semantic-v1":
        errors.append("analysis version or method is invalid")
    if analysis.get("chapter_id") != chapter_id:
        errors.append("analysis chapter_id mismatch")
    if analysis.get("source_sha256") != chapter["sha256"]:
        errors.append("analysis source hash mismatch")
    if analysis.get("input_packet_sha256") != packet_hash:
        errors.append("analysis packet hash mismatch")
    if analysis.get("source_unit_ids") != source_ids:
        errors.append("analysis source_unit_ids must exactly match source order")

    unresolved: list[str] = []
    analyses = analysis.get("unit_analyses")
    if not isinstance(analyses, list):
        errors.append("unit_analyses must be an array")
        analyses = []
    analysis_ids = [item.get("unit_id") for item in analyses if isinstance(item, dict)]
    if analysis_ids != source_ids:
        errors.append("unit_analyses must contain every source unit exactly once and in order")

    content_unit_ids: list[str] = []
    segment_ids: set[str] = set()
    for unit_position, unit_id in enumerate(source_ids):
        if unit_position >= len(analyses) or not isinstance(analyses[unit_position], dict):
            continue
        unit_analysis = analyses[unit_position]
        if unit_analysis.get("unit_id") != unit_id:
            continue
        source_text = source_texts[unit_id]
        segments = unit_analysis.get("segments")
        if not isinstance(segments, list):
            errors.append(f"{unit_id}: segments must be an array")
            continue
        cursor = 0
        has_content = False
        for ordinal, segment in enumerate(segments, 1):
            context = f"{unit_id} segment {ordinal}"
            if not isinstance(segment, dict):
                errors.append(f"{context}: segment is not an object")
                continue
            expected_id = f"{unit_id}-G{ordinal:03d}"
            if segment.get("schema_version") != "2.0.0":
                errors.append(f"{context}: invalid schema_version")
            if segment.get("segment_id") != expected_id or segment.get("ordinal") != ordinal:
                errors.append(f"{context}: unstable segment identity")
            else:
                segment_ids.add(expected_id)
            if segment.get("unit_id") != unit_id:
                errors.append(f"{context}: unit_id mismatch")
            start, end = segment.get("start"), segment.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or start < cursor or end <= start or end > len(source_text):
                errors.append(f"{context}: invalid or overlapping character range")
                continue
            if source_text[cursor:start].strip():
                errors.append(f"{context}: uncovered non-whitespace text before segment")
            if segment.get("source_text") != source_text[start:end]:
                errors.append(f"{context}: source_text does not match character range")
            if not segment.get("source_text"):
                errors.append(f"{context}: source_text is empty")
            translation = segment.get("translation")
            if not isinstance(translation, dict):
                errors.append(f"{context}: translation is required")
            else:
                text_zh = translation.get("text_zh")
                if not isinstance(text_zh, str) or not text_zh.strip():
                    errors.append(f"{context}: translation.text_zh is empty")
                elif LATIN_RE.search(segment.get("source_text", "")) and not CJK_RE.search(text_zh):
                    errors.append(f"{context}: translation.text_zh must contain Chinese text")
                if translation.get("status") not in {"agent_draft", "user_confirmed"}:
                    errors.append(f"{context}: invalid translation status")
            cursor = end
            text_type = segment.get("text_type")
            if text_type not in TEXT_TYPES:
                errors.append(f"{context}: invalid text_type {text_type}")
            if text_type != "chapter_heading":
                has_content = True
            classification = segment.get("classification")
            if not isinstance(classification, dict):
                errors.append(f"{context}: classification is not an object")
            else:
                status = classification.get("status")
                if status not in STATUSES:
                    errors.append(f"{context}: invalid classification status")
                if status in {"unknown", "conflict"}:
                    unresolved.append(f"{context} classification")
                check_confidence(classification.get("confidence"), context, errors)
                check_evidence(classification.get("evidence"), source_texts, context, errors)
            speaker = segment.get("speaker")
            if text_type in TEXT_TYPES_WITH_SPEAKER:
                if not isinstance(speaker, dict):
                    errors.append(f"{context}: speaker is required")
                else:
                    if speaker.get("kind") not in {"named", "role", "crowd", "unknown"}:
                        errors.append(f"{context}: invalid speaker kind")
                    if speaker.get("kind") != "unknown" and not speaker.get("canonical_label"):
                        errors.append(f"{context}: identified speaker requires canonical_label")
                    if speaker.get("kind") == "unknown" or speaker.get("status") in {"unknown", "conflict"}:
                        unresolved.append(f"{context} speaker")
                    if speaker.get("status") not in STATUSES:
                        errors.append(f"{context}: invalid speaker status")
                    check_confidence(speaker.get("confidence"), context + " speaker", errors)
                    check_evidence(speaker.get("evidence"), source_texts, context + " speaker", errors)
            elif speaker is not None:
                errors.append(f"{context}: non-speech segment must have null speaker")
        if source_text[cursor:].strip():
            errors.append(f"{unit_id}: uncovered non-whitespace text after final segment")
        if has_content:
            content_unit_ids.append(unit_id)

    scenes = analysis.get("scenes")
    if not isinstance(scenes, list):
        errors.append("scenes must be an array")
        scenes = []
    flattened_scenes: list[str] = []
    scene_ids: set[str] = set()
    beat_ids: set[str] = set()
    for scene_ordinal, scene in enumerate(scenes, 1):
        context = f"scene {scene_ordinal}"
        if not isinstance(scene, dict):
            errors.append(f"{context}: scene is not an object")
            continue
        expected_id = f"{chapter_id}-S{scene_ordinal:03d}"
        if scene.get("scene_id") != expected_id or scene.get("ordinal") != scene_ordinal:
            errors.append(f"{context}: unstable scene identity")
        else:
            scene_ids.add(expected_id)
        if not isinstance(scene.get("summary_zh"), str) or not scene["summary_zh"].strip():
            errors.append(f"{context}: summary_zh is required")
        scene_units = scene.get("source_unit_ids")
        if not isinstance(scene_units, list) or not scene_units:
            errors.append(f"{context}: source_unit_ids must be non-empty")
            scene_units = []
        flattened_scenes.extend(scene_units)
        if any(unit_id not in source_texts for unit_id in scene_units):
            errors.append(f"{context}: references unknown source unit")
        check_evidence(scene.get("boundary_evidence"), source_texts, context + " boundary", errors)

        location = scene.get("location")
        if not isinstance(location, dict):
            errors.append(f"{context}: location is not an object")
            unresolved.append(f"{context} location")
        else:
            name = location.get("standardized_name")
            status = location.get("status")
            if status not in STATUSES:
                errors.append(f"{context}: invalid location status")
            if status != "unknown" and not location.get("source_text"):
                errors.append(f"{context}: resolved location requires source_text")
            if not isinstance(name, str) or not name.strip() or status in {"unknown", "conflict"}:
                unresolved.append(f"{context} location")
            elif name.strip() in GENERIC_LOCATIONS:
                errors.append(f"{context}: generic location name is not production-ready: {name}")
            check_confidence(location.get("confidence"), context + " location", errors)
            check_evidence(location.get("evidence"), source_texts, context + " location", errors)
        if check_assessment(scene.get("int_ext"), {"INT", "EXT", "INT_EXT", "unknown"}, source_texts, context + " int_ext", errors):
            unresolved.append(f"{context} int_ext")
        if check_assessment(scene.get("time_of_day"), {"day", "night", "dawn", "morning", "noon", "afternoon", "dusk", "continuous", "unknown"}, source_texts, context + " time", errors):
            unresolved.append(f"{context} time")
        if check_assessment(scene.get("reality_layer"), {"present", "flashback", "dream", "imagined", "unknown"}, source_texts, context + " reality", errors):
            unresolved.append(f"{context} reality")

        beats = scene.get("beats")
        if not isinstance(beats, list) or not beats:
            errors.append(f"{context}: beats must be non-empty")
            beats = []
        flattened_beats: list[str] = []
        for beat_ordinal, beat in enumerate(beats, 1):
            beat_context = f"{context} beat {beat_ordinal}"
            if not isinstance(beat, dict):
                errors.append(f"{beat_context}: beat is not an object")
                continue
            expected_beat_id = f"{expected_id}-B{beat_ordinal:03d}"
            if beat.get("beat_id") != expected_beat_id or beat.get("ordinal") != beat_ordinal:
                errors.append(f"{beat_context}: unstable beat identity")
            else:
                beat_ids.add(expected_beat_id)
            if not isinstance(beat.get("summary_zh"), str) or not beat["summary_zh"].strip():
                errors.append(f"{beat_context}: summary_zh is required")
            if beat.get("change_type") not in BEAT_TYPES:
                errors.append(f"{beat_context}: invalid change_type")
            beat_units = beat.get("source_unit_ids")
            if not isinstance(beat_units, list) or not beat_units:
                errors.append(f"{beat_context}: source_unit_ids must be non-empty")
                beat_units = []
            flattened_beats.extend(beat_units)
            status = beat.get("status")
            if status not in STATUSES:
                errors.append(f"{beat_context}: invalid status")
            if status in {"unknown", "conflict"}:
                unresolved.append(beat_context)
            check_confidence(beat.get("confidence"), beat_context, errors)
            check_evidence(beat.get("evidence"), source_texts, beat_context, errors)
        if flattened_beats != scene_units:
            errors.append(f"{context}: beats must partition scene units exactly and in order")
    if flattened_scenes != content_unit_ids:
        errors.append("scenes must partition all non-heading content units exactly and in order")

    review = analysis.get("review")
    if not isinstance(review, dict):
        errors.append("review is not an object")
    else:
        review_status = review.get("status")
        issues = review.get("issues")
        if review_status not in {"ready", "provisional"} or not isinstance(issues, list):
            errors.append("review status or issues is invalid")
            issues = []
        issue_ids: set[str] = set()
        for issue_index, issue in enumerate(issues, 1):
            if not isinstance(issue, dict) or not issue.get("code") or not issue.get("message"):
                errors.append(f"review issue {issue_index} is invalid")
                continue
            expected_issue_id = f"ISS-{chapter_id}-{issue_index:04d}"
            issue_id = issue.get("issue_id")
            if issue_id != expected_issue_id:
                errors.append(f"review issue {issue_index} must use stable ID {expected_issue_id}")
            elif issue_id in issue_ids:
                errors.append(f"duplicate review issue ID {issue_id}")
            else:
                issue_ids.add(issue_id)
            scope_type = issue.get("scope_type")
            scope_id = issue.get("scope_id")
            valid_scope = (
                (scope_type == "chapter" and scope_id == chapter_id)
                or (scope_type == "scene" and scope_id in scene_ids)
                or (scope_type == "beat" and scope_id in beat_ids)
                or (scope_type == "source_unit" and scope_id in source_texts)
                or (scope_type == "segment" and scope_id in segment_ids)
            )
            if not valid_scope:
                errors.append(f"review issue {issue_index} has invalid scope {scope_type}:{scope_id}")
            field_path = issue.get("field_path")
            if field_path is not None and (not isinstance(field_path, str) or not field_path.strip()):
                errors.append(f"review issue {issue_index} field_path is invalid")
            issue_units = issue.get("source_unit_ids")
            if not isinstance(issue_units, list):
                errors.append(f"review issue {issue_index} source_unit_ids is invalid")
            elif any(unit_id not in source_texts for unit_id in issue_units):
                errors.append(f"review issue {issue_index} references unknown source unit")
        if unresolved and review_status != "provisional":
            errors.append("unresolved semantic fields require review.status = provisional")
        if unresolved and not issues:
            errors.append("unresolved semantic fields require at least one review issue")
        if review_status == "ready" and issues:
            errors.append("ready analysis cannot contain review issues")
        if review_status == "provisional" and not issues:
            errors.append("provisional analysis requires at least one review issue")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验单章文本片段、说话人、场景、Beat和证据")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True, help="chapter ID, e.g. P01")
    args = parser.parse_args()
    errors = validate_analysis(args.project_dir, args.chapter)
    if errors:
        print("ANALYSIS VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    analysis_path = args.project_dir.resolve() / "data" / "analysis" / f"{args.chapter.lower()}.analysis.json"
    analysis = load_json(analysis_path)
    issues = analysis.get("review", {}).get("issues", [])
    if issues:
        print(f"ANALYSIS VALIDATION PASSED WITH {len(issues)} PENDING ISSUE(S): structure and evidence are consistent.")
        for issue in issues:
            print(f" - {issue.get('issue_id')}: {issue.get('scope_id')} {issue.get('message')}")
    else:
        print("ANALYSIS VALIDATION PASSED: segments, scenes, beats and evidence are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
