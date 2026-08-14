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
PROFILE_FIELDS = {"age", "gender", "race", "identity", "appearance", "hair", "body_type", "special_marks", "clothing", "personality", "story_role", "basic_info_summary"}
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
    scene_units_by_id: dict[str, set[str]] = {}
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
        scene_units_by_id[expected_id] = set(scene_units)
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

    observations = analysis.get("character_observations")
    if not isinstance(observations, list):
        errors.append("character_observations must be an array")
        observations = []
    observed_speaker_segment_counts: dict[str, int] = {}
    observation_ids: set[str] = set()
    for observation_index, observation in enumerate(observations, 1):
        context = f"character observation {observation_index}"
        if not isinstance(observation, dict):
            errors.append(f"{context}: record is not an object")
            continue
        expected_id = f"COBS-{chapter_id}-{observation_index:04d}"
        if observation.get("observation_id") != expected_id:
            errors.append(f"{context}: must use stable ID {expected_id}")
        else:
            observation_ids.add(expected_id)
        entity_key = observation.get("entity_key")
        if not isinstance(entity_key, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entity_key):
            errors.append(f"{context}: entity_key is invalid")
        if not isinstance(observation.get("canonical_label"), str) or not observation["canonical_label"].strip():
            errors.append(f"{context}: canonical_label is required")
        if observation.get("character_type") not in {"named", "role", "crowd"}:
            errors.append(f"{context}: character_type is invalid")
        if observation.get("importance_hint") not in {"candidate_major", "supporting", "minor", "unknown"}:
            errors.append(f"{context}: importance_hint is invalid")
        if observation.get("presence_type") not in {"physical", "dream", "flashback", "mentioned", "unknown"}:
            errors.append(f"{context}: presence_type is invalid")
        matched = observation.get("matched_character_id")
        if matched is not None and (not isinstance(matched, str) or not re.fullmatch(r"CHAR-[0-9]{4}", matched)):
            errors.append(f"{context}: matched_character_id is invalid")
        obs_scene = observation.get("scene_id")
        if obs_scene is not None and obs_scene not in scene_ids:
            errors.append(f"{context}: references unknown scene")
        obs_units = observation.get("source_unit_ids")
        if not isinstance(obs_units, list) or not obs_units or any(unit_id not in source_texts for unit_id in obs_units):
            errors.append(f"{context}: source_unit_ids are invalid")
            obs_units = []
        if obs_scene is not None and any(unit_id not in scene_units_by_id.get(obs_scene, set()) for unit_id in obs_units):
            errors.append(f"{context}: source units are outside the referenced scene")
        speaker_segments = observation.get("speaker_segment_ids")
        if not isinstance(speaker_segments, list) or len(speaker_segments) != len(set(speaker_segments)) or any(segment_id not in segment_ids for segment_id in speaker_segments):
            errors.append(f"{context}: speaker_segment_ids are invalid")
            speaker_segments = []
        has_dialogue = observation.get("has_dialogue")
        if not isinstance(has_dialogue, bool) or (has_dialogue and not speaker_segments) or (not has_dialogue and speaker_segments):
            errors.append(f"{context}: has_dialogue and speaker_segment_ids disagree")
        for segment_id in speaker_segments:
            observed_speaker_segment_counts[segment_id] = observed_speaker_segment_counts.get(segment_id, 0) + 1
        if observation.get("status") not in STATUSES:
            errors.append(f"{context}: status is invalid")
        elif observation.get("status") in {"unknown", "conflict"}:
            unresolved.append(f"{context} identity")
        check_confidence(observation.get("confidence"), context, errors)
        check_evidence(observation.get("evidence"), source_texts, context, errors)
        for field in ("aliases", "chinese_aliases"):
            values = observation.get(field)
            if not isinstance(values, list) or len(values) != len(set(values)) or any(not isinstance(value, str) or not value.strip() for value in values):
                errors.append(f"{context}: {field} is invalid")

    required_speaker_segments = {
        segment["segment_id"]
        for unit in analyses
        if isinstance(unit, dict)
        for segment in unit.get("segments", [])
        if isinstance(segment, dict) and segment.get("text_type") in TEXT_TYPES_WITH_SPEAKER and isinstance(segment.get("speaker"), dict) and segment["speaker"].get("kind") != "unknown"
    }
    observed_speaker_segments = set(observed_speaker_segment_counts)
    if observed_speaker_segments != required_speaker_segments or any(count != 1 for count in observed_speaker_segment_counts.values()):
        missing = sorted(required_speaker_segments - observed_speaker_segments)
        extra = sorted(observed_speaker_segments - required_speaker_segments)
        duplicates = sorted(segment_id for segment_id, count in observed_speaker_segment_counts.items() if count > 1)
        if missing:
            errors.append("identified speaker segments missing character observations: " + ", ".join(missing))
        if extra:
            errors.append("character observations reference non-identified speaker segments: " + ", ".join(extra))
        if duplicates:
            errors.append("identified speaker segments belong to multiple character observations: " + ", ".join(duplicates))

    profile_observations = analysis.get("profile_observations")
    if not isinstance(profile_observations, list):
        errors.append("profile_observations must be an array")
        profile_observations = []
    profile_observation_ids: set[str] = set()
    profile_keys: set[tuple[str, str, str]] = set()
    for index, observation in enumerate(profile_observations, 1):
        context = f"profile observation {index}"
        if not isinstance(observation, dict):
            errors.append(f"{context}: record is not an object")
            continue
        expected_id = f"POBS-{chapter_id}-{index:04d}"
        if observation.get("profile_observation_id") != expected_id:
            errors.append(f"{context}: must use stable ID {expected_id}")
        else:
            profile_observation_ids.add(expected_id)
        entity_key = observation.get("entity_key")
        if not isinstance(entity_key, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", entity_key):
            errors.append(f"{context}: entity_key is invalid")
        matched = observation.get("matched_character_id")
        if matched is not None and (not isinstance(matched, str) or not re.fullmatch(r"CHAR-[0-9]{4}", matched)):
            errors.append(f"{context}: matched_character_id is invalid")
        field = observation.get("field")
        if field not in PROFILE_FIELDS:
            errors.append(f"{context}: field is invalid")
        for name in ("value_zh", "normalized_value"):
            if not isinstance(observation.get(name), str) or not observation[name].strip():
                errors.append(f"{context}: {name} is required")
        if observation.get("status") not in {"explicit", "inferred", "user_confirmed"}:
            errors.append(f"{context}: status must describe an actual observed fact")
        check_confidence(observation.get("confidence"), context, errors)
        check_evidence(observation.get("evidence"), source_texts, context, errors)
        profile_units = observation.get("source_unit_ids")
        if not isinstance(profile_units, list) or not profile_units or len(profile_units) != len(set(profile_units)) or any(unit_id not in source_texts for unit_id in profile_units):
            errors.append(f"{context}: source_unit_ids are invalid")
        if not isinstance(observation.get("note_zh"), str):
            errors.append(f"{context}: note_zh must be a string")
        key = (str(entity_key), str(field), str(observation.get("normalized_value")))
        if key in profile_keys:
            errors.append(f"{context}: duplicate same-chapter profile fact")
        profile_keys.add(key)

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
                or (scope_type == "character_observation" and scope_id in observation_ids)
                or (scope_type == "profile_observation" and scope_id in profile_observation_ids)
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
