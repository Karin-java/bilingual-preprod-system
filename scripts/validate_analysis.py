#!/usr/bin/env python3
"""Validate one V3 chapter record without calling a model."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from preprod import BEAT_RE, CHAPTER_RE, SCENE_RE, find_chapter, load_project, sha256
from validate_ingest import validate_project

ROOT_KEYS = {
    "schema_version", "chapter_id", "source_sha256", "input_sha256", "title_en", "title_zh", "pov",
    "blocks", "front_matter_block_ids", "scenes", "characters", "character_facts", "locations",
    "appearances", "identity_links", "asset_candidates", "issues",
}
KINDS = {"heading", "narration", "action", "dialogue", "thought", "separator"}
INT_EXT = {"INT", "EXT", "MIXED", "UNKNOWN"}
TIMES = {"DAY", "NIGHT", "DAWN", "DUSK", "CONTINUOUS", "UNKNOWN"}
FACT_FIELDS = {"age", "gender", "race", "identity", "appearance", "hair", "body", "special_marks", "clothing", "personality"}


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _require_object_list(record: dict[str, Any], key: str, errors: list[str]) -> list[dict[str, Any]]:
    value = record.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        errors.append(f"{key} must be an array of objects")
        return []
    return value


def _evidence(owner: str, value: Any, block_ids: set[str], errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{owner}: source block IDs are required")
        return
    missing = [item for item in value if item not in block_ids]
    if missing:
        errors.append(f"{owner}: unknown source blocks {missing}")
    if len(value) != len(set(value)):
        errors.append(f"{owner}: duplicate source block IDs")


def validate_record(project_dir: Path, chapter_id: str, record: dict[str, Any], *, check_packet: bool = True) -> list[str]:
    errors: list[str] = []
    if set(record) != ROOT_KEYS:
        missing = sorted(ROOT_KEYS - set(record))
        extra = sorted(set(record) - ROOT_KEYS)
        if missing:
            errors.append(f"missing root keys: {missing}")
        if extra:
            errors.append(f"unexpected root keys: {extra}")
    if record.get("schema_version") != "3.0.0":
        errors.append("schema_version must be 3.0.0")
    if record.get("chapter_id") != chapter_id or not CHAPTER_RE.fullmatch(chapter_id):
        errors.append("chapter_id mismatch")

    try:
        _, manifest = load_project(project_dir)
        chapter = find_chapter(manifest, chapter_id)
        source_bytes = (project_dir / chapter["source_path"]).read_bytes()
        source_text = source_bytes.decode("utf-8")
    except (OSError, KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"source unreadable: {exc}"]
    if record.get("source_sha256") != chapter["sha256"]:
        errors.append("source_sha256 does not match immutable chapter")
    packet_path = project_dir / "work" / "chapters" / f"{chapter_id.lower()}.packet.json"
    if check_packet:
        if not packet_path.exists():
            errors.append("analysis packet is missing")
        elif record.get("input_sha256") != sha256(packet_path.read_bytes()):
            errors.append("input_sha256 does not match the current chapter packet")

    blocks = _require_object_list(record, "blocks", errors)
    if not blocks:
        errors.append("blocks must not be empty")
        return errors
    ids = [str(item.get("block_id", "")) for item in blocks]
    if _duplicates(ids):
        errors.append(f"duplicate block IDs: {_duplicates(ids)}")
    expected_ids = [f"{chapter_id}-B{ordinal:04d}" for ordinal in range(1, len(blocks) + 1)]
    if ids != expected_ids:
        errors.append("block IDs must be sequential and follow source order")
    block_ids = set(ids)
    reconstructed: list[str] = []
    character_refs: set[str] = set()
    for item in _require_object_list(record, "characters", errors):
        ref = item.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            errors.append("character ref must be non-empty")
        else:
            character_refs.add(ref)
        if not isinstance(item.get("canonical_name_en"), str) or not item["canonical_name_en"].strip():
            errors.append(f"{ref}: canonical English name is required")
        if item.get("category") not in {"named", "speaking", "recurring_role", "crowd"}:
            errors.append(f"{ref}: invalid character category")
        if item.get("importance") not in {"major", "supporting", "minor", "unknown"}:
            errors.append(f"{ref}: invalid importance")
    if len(character_refs) != len(record.get("characters", [])):
        errors.append("character refs must be unique")

    issue_scopes = {
        str(issue.get("scope_id"))
        for issue in record.get("issues", []) if isinstance(issue, dict)
    }
    for index, block in enumerate(blocks, 1):
        block_id = ids[index - 1]
        text_en = block.get("text_en")
        text_zh = block.get("text_zh")
        if not isinstance(text_en, str) or not text_en:
            errors.append(f"{block_id}: text_en must be non-empty")
        else:
            reconstructed.append(text_en)
        kind = block.get("kind")
        if kind not in KINDS:
            errors.append(f"{block_id}: invalid kind")
        if kind != "separator" and (not isinstance(text_zh, str) or not text_zh.strip()):
            errors.append(f"{block_id}: Chinese translation is required")
        speaker = block.get("speaker_ref")
        if kind == "dialogue":
            if not isinstance(speaker, str) or not speaker:
                errors.append(f"{block_id}: dialogue needs speaker_ref")
            elif speaker != "unknown" and speaker not in character_refs:
                errors.append(f"{block_id}: unknown speaker ref {speaker}")
            elif speaker == "unknown" and block_id not in issue_scopes:
                errors.append(f"{block_id}: unknown speaker needs a pending issue")
        elif speaker is not None:
            errors.append(f"{block_id}: only dialogue may have speaker_ref")
    if "".join(reconstructed) != source_text:
        errors.append("blocks do not reconstruct the immutable English chapter exactly")

    locations = _require_object_list(record, "locations", errors)
    location_refs = {str(item.get("ref")) for item in locations if isinstance(item.get("ref"), str) and item.get("ref")}
    if len(location_refs) != len(locations):
        errors.append("location refs must be unique and non-empty")
    all_assigned: list[str] = []
    front = record.get("front_matter_block_ids")
    if not isinstance(front, list):
        errors.append("front_matter_block_ids must be an array")
        front = []
    if front != ids[:len(front)]:
        errors.append("front matter must be a contiguous prefix of blocks")
    all_assigned.extend(front)

    scenes = _require_object_list(record, "scenes", errors)
    if not scenes:
        errors.append("at least one scene is required")
    scene_ids: set[str] = set()
    for ordinal, scene in enumerate(scenes, 1):
        scene_id = str(scene.get("scene_id", ""))
        expected = f"{chapter_id}-S{ordinal:03d}"
        if scene_id != expected or scene.get("ordinal") != ordinal or not SCENE_RE.fullmatch(scene_id):
            errors.append(f"scene {ordinal}: ID and ordinal must be {expected}/{ordinal}")
        scene_ids.add(scene_id)
        scene_blocks = scene.get("block_ids")
        if not isinstance(scene_blocks, list) or not scene_blocks:
            errors.append(f"{scene_id}: block_ids must not be empty")
            scene_blocks = []
        all_assigned.extend(scene_blocks)
        if any(item not in block_ids for item in scene_blocks):
            errors.append(f"{scene_id}: references unknown blocks")
        if not isinstance(scene.get("name_zh"), str) or not scene["name_zh"].strip():
            errors.append(f"{scene_id}: a concrete Chinese scene name is required")
        if scene.get("location_ref") not in location_refs:
            errors.append(f"{scene_id}: location_ref must name a declared location")
        if scene.get("int_ext") not in INT_EXT or scene.get("time") not in TIMES:
            errors.append(f"{scene_id}: invalid INT/EXT or time value")
        if (scene.get("int_ext") == "UNKNOWN" or scene.get("time") == "UNKNOWN") and scene_id not in issue_scopes:
            errors.append(f"{scene_id}: unknown production field needs a pending issue")
        refs = scene.get("character_refs")
        if not isinstance(refs, list) or any(ref not in character_refs for ref in refs):
            errors.append(f"{scene_id}: character_refs contain undeclared values")
        beats = scene.get("beats")
        if not isinstance(beats, list) or not beats:
            errors.append(f"{scene_id}: at least one Beat is required")
            beats = []
        beat_blocks: list[str] = []
        for beat_ordinal, beat in enumerate(beats, 1):
            beat_id = str(beat.get("beat_id", ""))
            expected_beat = f"{scene_id}-B{beat_ordinal:03d}"
            if beat_id != expected_beat or not BEAT_RE.fullmatch(beat_id):
                errors.append(f"{scene_id}: Beat IDs must be sequential")
            values = beat.get("block_ids")
            if not isinstance(values, list) or not values:
                errors.append(f"{beat_id}: block_ids must not be empty")
            else:
                beat_blocks.extend(values)
        if beat_blocks != scene_blocks:
            errors.append(f"{scene_id}: Beats must partition scene blocks in order")
    if all_assigned != ids:
        errors.append("front matter and Scenes must partition every block exactly once and in order")

    for fact in _require_object_list(record, "character_facts", errors):
        owner = str(fact.get("fact_id", "character fact"))
        if fact.get("subject_ref") not in character_refs:
            errors.append(f"{owner}: subject_ref is undeclared")
        if fact.get("field") not in FACT_FIELDS:
            errors.append(f"{owner}: invalid field")
        if not isinstance(fact.get("value_zh"), str) or not fact["value_zh"].strip():
            errors.append(f"{owner}: value_zh is required")
        _evidence(owner, fact.get("source_block_ids"), block_ids, errors)
    for location in locations:
        for fact in location.get("facts", []):
            owner = str(fact.get("fact_id", "location fact"))
            if fact.get("field") not in {"space", "environment", "furniture", "state_change"}:
                errors.append(f"{owner}: invalid location fact field")
            _evidence(owner, fact.get("source_block_ids"), block_ids, errors)
    for appearance in _require_object_list(record, "appearances", errors):
        owner = str(appearance.get("appearance_id", "appearance"))
        if appearance.get("character_ref") not in character_refs:
            errors.append(f"{owner}: undeclared character")
        if appearance.get("scene_id") not in scene_ids:
            errors.append(f"{owner}: unknown scene")
        if appearance.get("presence") not in {"actual", "memory", "dream", "mentioned"}:
            errors.append(f"{owner}: invalid presence")
        _evidence(owner, appearance.get("source_block_ids"), block_ids, errors)
    for link in _require_object_list(record, "identity_links", errors):
        owner = str(link.get("link_id", "identity link"))
        refs = character_refs if link.get("entity_type") == "character" else location_refs
        if link.get("entity_type") not in {"character", "location"}:
            errors.append(f"{owner}: invalid entity type")
        if link.get("left_ref") not in refs or link.get("right_ref") not in refs:
            errors.append(f"{owner}: identity refs must be declared in this chapter")
        if link.get("relationship") not in {"same", "possible_same"}:
            errors.append(f"{owner}: invalid relationship")
        _evidence(owner, link.get("source_block_ids"), block_ids, errors)
    for candidate in _require_object_list(record, "asset_candidates", errors):
        owner = f"asset {candidate.get('candidate_key', '?')}"
        if candidate.get("type") not in {"character", "location", "costume", "prop"}:
            errors.append(f"{owner}: invalid asset type")
        if any(scene_id not in scene_ids for scene_id in candidate.get("scene_ids", [])):
            errors.append(f"{owner}: unknown scene")
        _evidence(owner, candidate.get("source_block_ids"), block_ids, errors)
    issues = _require_object_list(record, "issues", errors)
    issue_ids = [str(issue.get("issue_id", "")) for issue in issues]
    if len(issue_ids) != len(set(issue_ids)):
        errors.append("issue IDs must be unique")
    for issue in issues:
        owner = str(issue.get("issue_id", "issue"))
        if issue.get("status") not in {"pending", "resolved"}:
            errors.append(f"{owner}: invalid issue status")
        if issue.get("status") == "resolved" and not issue.get("resolution_zh"):
            errors.append(f"{owner}: resolved issue needs resolution_zh")
        _evidence(owner, issue.get("evidence_block_ids"), block_ids, errors)

    analysis_path = project_dir / "data" / "chapters" / f"{chapter_id.lower()}.json"
    if analysis_path.exists():
        limit = len(source_bytes) * 4 + 12_288
        if len(analysis_path.read_bytes()) > limit:
            errors.append(f"chapter record exceeds lean size budget: {len(analysis_path.read_bytes())} > {limit} bytes")
    return errors


def validate_analysis(project_dir: Path, chapter_id: str, path: Path | None = None, *, check_packet: bool = True) -> list[str]:
    project_dir = project_dir.resolve()
    ingest_errors = validate_project(project_dir)
    if ingest_errors:
        return ["ingest validation failed: " + "; ".join(ingest_errors)]
    path = path or project_dir / "data" / "chapters" / f"{chapter_id.lower()}.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"chapter record unreadable: {exc}"]
    if not isinstance(record, dict):
        return ["chapter record must be a JSON object"]
    return validate_record(project_dir, chapter_id, record, check_packet=check_packet)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 V3 单章双语结构记录")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True)
    args = parser.parse_args()
    errors = validate_analysis(args.project_dir, args.chapter)
    if errors:
        print("CHAPTER VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("CHAPTER VALIDATION PASSED: source, translation, Scene/Beat and fact deltas are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
