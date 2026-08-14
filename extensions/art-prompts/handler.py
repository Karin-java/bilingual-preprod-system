"""Prepare one evidence-bounded scene packet for a future art-prompt Agent."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from build_registries import atomic_write, json_bytes, sha256


def descriptor(project_dir: Path, path: Path, data_type: str) -> dict[str, str]:
    value = load_json(path)
    return {
        "path": path.relative_to(project_dir).as_posix(), "sha256": sha256(path.read_bytes()),
        "data_type": data_type, "schema_version": value["schema_version"],
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def analysis_path(project_dir: Path, chapter_id: str) -> Path:
    resolved = project_dir / "data" / "views" / "analysis" / f"{chapter_id.lower()}.resolved.json"
    return resolved if resolved.is_file() else project_dir / "data" / "analysis" / f"{chapter_id.lower()}.analysis.json"


def translations(analysis: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for unit in analysis["unit_analyses"]:
        values = [
            segment.get("translation", {}).get("text_zh", "").strip()
            for segment in unit.get("segments", [])
            if segment.get("translation", {}).get("text_zh", "").strip()
        ]
        if values:
            result[unit["unit_id"]] = " ".join(values)
    return result


def evidence_rows(scene: dict[str, Any], scene_appearances: list[dict[str, Any]], translation_map: dict[str, str]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    candidates.extend(scene.get("boundary_evidence", []))
    candidates.extend(scene.get("location", {}).get("evidence", []))
    for field in ("int_ext", "time_of_day", "reality_layer"):
        candidates.extend(scene.get(field, {}).get("evidence", []))
    for beat in scene.get("beats", []):
        candidates.extend(beat.get("evidence", []))
    for appearance in scene_appearances:
        candidates.extend(appearance.get("evidence", []))
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        key = (item["source_unit_id"], item["quote"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "source_unit_id": key[0], "quote_en": key[1],
            "translation_zh": translation_map.get(key[0], "译文待补充"),
        })
        if len(rows) >= 12:
            break
    return rows


def compact_profile(profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    known: dict[str, dict[str, Any]] = {}
    unresolved: list[str] = []
    for field_name, field in profile["fields"].items():
        if field["status"] == "unknown":
            unresolved.append(field_name)
        else:
            known[field_name] = {"value_zh": field["value_zh"], "status": field["status"]}
    return known, unresolved


def prepare(project_dir: Path, manifest: dict[str, Any], target: str | None) -> dict[str, Any]:
    registry_path = project_dir / "data" / "registries" / "entities.json"
    profiles_path = project_dir / "data" / "profiles" / "profiles.json"
    appearances_path = project_dir / "data" / "appearances" / "appearances.json"
    for name, path in (("registry", registry_path), ("profiles", profiles_path), ("appearances", appearances_path)):
        if not path.is_file():
            raise ValueError(f"art-prompts extension requires core output: {name}")
    registry = load_json(registry_path)
    profile_bundle = load_json(profiles_path)
    appearance_bundle = load_json(appearances_path)
    if target is None:
        analysis_inputs = sorted(appearance_bundle["analysis_inputs"], key=lambda item: int(item["chapter_id"][1:]))
        if not analysis_inputs:
            raise ValueError("art-prompts extension cannot find a scene")
        first_analysis = load_json(project_dir / analysis_inputs[0]["path"])
        if not first_analysis["scenes"]:
            raise ValueError("art-prompts extension cannot find a scene")
        target = first_analysis["scenes"][0]["scene_id"]
    if not re.fullmatch(r"P[0-9]{2,}-S[0-9]{3}", target):
        raise ValueError(f"art prompt target must be one scene ID: {target}")
    chapter_id = target.split("-")[0]
    chapter_path = analysis_path(project_dir, chapter_id)
    if not chapter_path.is_file():
        raise ValueError(f"art-prompts extension cannot find chapter analysis: {chapter_id}")
    analysis = load_json(chapter_path)
    scene = next((item for item in analysis["scenes"] if item["scene_id"] == target), None)
    if scene is None:
        raise ValueError(f"scene does not exist: {target}")
    registry_characters = {item["character_id"]: item for item in registry["characters"]}
    profiles = {item["character_id"]: item for item in profile_bundle["profiles"]}
    scene_appearances = [item for item in appearance_bundle["appearances"] if item["scene_id"] == target]
    characters: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for appearance in scene_appearances:
        character = registry_characters[appearance["character_id"]]
        profile = profiles.get(appearance["character_id"])
        known_fields, unknown_fields = compact_profile(profile) if profile else ({}, [])
        characters.append({
            "character_id": character["character_id"], "canonical_name": character["canonical_name"],
            "chinese_name": character["chinese_name"], "presence_type": appearance["appearance_type"],
            "has_dialogue": appearance["has_dialogue"], "scene_summary_zh": appearance["summary_zh"],
            "known_profile_fields": known_fields,
        })
        if unknown_fields:
            unresolved.append({"character_id": character["character_id"], "fields": unknown_fields})
    scene_unknowns = [
        name for name in ("location", "int_ext", "time_of_day", "reality_layer")
        if scene[name].get("status") in {"unknown", "conflict"}
    ]
    packet = {
        "packet_version": "1.0.0", "module_id": "art-prompts", "project_id": load_json(project_dir / "project.json")["project_id"],
        "target": {"type": "scene", "scene_id": target, "chapter_id": chapter_id},
        "scene_facts": {
            "summary_zh": scene["summary_zh"],
            "location": {
                key: scene["location"].get(key)
                for key in ("source_text", "standardized_name", "parent_location", "sub_location", "status")
            },
            "int_ext": {key: scene["int_ext"].get(key) for key in ("value", "status")},
            "time_of_day": {key: scene["time_of_day"].get(key) for key in ("value", "status")},
            "reality_layer": {key: scene["reality_layer"].get(key) for key in ("value", "status")},
            "beats": [{"beat_id": item["beat_id"], "summary_zh": item["summary_zh"]} for item in scene["beats"]],
        },
        "characters": characters,
        "unresolved": {"scene_fields": scene_unknowns, "character_fields": unresolved},
        "evidence": evidence_rows(scene, scene_appearances, translations(analysis)),
        "rules_ref": "references/art_prompt_extension_rules.md",
        "output_path": f"deliverables/art_prompts/{target.lower()}.md",
    }
    packet_path = project_dir / "extensions" / "art-prompts" / "work" / f"{target.lower()}.packet.json"
    packet_data = json_bytes(packet)
    atomic_write(packet_path, packet_data)
    minimum, maximum = __import__("run_extension").estimate_tokens(packet_data)
    packet_relative = packet_path.relative_to(project_dir).as_posix()
    inputs = [
        descriptor(project_dir, project_dir / "project.json", "project"),
        descriptor(project_dir, chapter_path, "chapter-analysis"),
        descriptor(project_dir, registry_path, "entity-registry"),
        descriptor(project_dir, profiles_path, "profile-bundle"),
        descriptor(project_dir, appearances_path, "appearance-bundle"),
    ]
    task = {
        "task_type": "create_art_prompt", "target_id": target,
        "input_path": packet_relative, "input_sha256": sha256(packet_data),
        "output_path": packet["output_path"], "output_format": "markdown",
        "input_utf8_bytes": len(packet_data), "estimated_input_tokens_min": minimum,
        "estimated_input_tokens_max": maximum, "token_estimate_method": "mixed-character-heuristic-v1",
    }
    return {
        "target": packet["target"], "inputs": inputs,
        "artifacts": [{
            "path": packet_relative, "sha256": sha256(packet_data),
            "artifact_type": "art-prompt-packet", "format": "json",
        }],
        "task": task,
        "summary_zh": f"已为 {target} 准备单场景事实包；未调用模型，也未改动核心数据。",
    }
