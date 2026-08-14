#!/usr/bin/env python3
"""Validate stable character, alias and location registries."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from build_registries import build_registry_data, normalize_character_name, resolve_character_id

ENTITY_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CHARACTER_ID_RE = re.compile(r"^CHAR-[0-9]{4}$")
LOCATION_ID_RE = re.compile(r"^LOC-[0-9]{4}$")


def validate_registries(project_dir: Path) -> list[str]:
    project_dir = project_dir.resolve()
    registry_path = project_dir / "data" / "registries" / "entities.json"
    review_path = project_dir / "work" / "registry" / "entities.md"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        review = review_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"registry outputs unreadable: {exc}"]
    errors: list[str] = []
    if registry.get("schema_version") != "2.0.0" or registry.get("registry_version") != "entity-registry-v2":
        errors.append("registry version is invalid")
    character_allocations = registry.get("character_allocations")
    character_key_index = registry.get("character_key_index")
    character_name_index = registry.get("character_name_index")
    redirects = registry.get("character_id_redirects")
    location_allocations = registry.get("location_allocations")
    if not isinstance(character_allocations, dict) or any(not ENTITY_KEY_RE.fullmatch(key) or not CHARACTER_ID_RE.fullmatch(value) for key, value in character_allocations.items()):
        errors.append("character allocations are invalid")
        character_allocations = {}
    if not isinstance(character_key_index, dict) or any(not ENTITY_KEY_RE.fullmatch(key) or not CHARACTER_ID_RE.fullmatch(value) for key, value in character_key_index.items()):
        errors.append("character key index is invalid")
        character_key_index = {}
    if not isinstance(redirects, dict) or any(not CHARACTER_ID_RE.fullmatch(key) or not CHARACTER_ID_RE.fullmatch(value) or key == value for key, value in redirects.items()):
        errors.append("character ID redirects are invalid")
        redirects = {}
    else:
        for character_id in redirects:
            try:
                resolve_character_id(redirects, character_id)
            except ValueError as exc:
                errors.append(str(exc))
                break
    if not isinstance(character_name_index, dict):
        errors.append("character name index is invalid")
        character_name_index = {}
    if not isinstance(location_allocations, dict) or any(not ENTITY_KEY_RE.fullmatch(key) or not LOCATION_ID_RE.fullmatch(value) for key, value in location_allocations.items()):
        errors.append("location allocations are invalid")
        location_allocations = {}

    characters = registry.get("characters")
    if not isinstance(characters, list):
        errors.append("characters must be an array")
        characters = []
    character_ids = [item.get("character_id") for item in characters if isinstance(item, dict)]
    if len(character_ids) != len(set(character_ids)):
        errors.append("character IDs are duplicated")
    canonical_ids = set(character_ids)
    for source_id, target_id in redirects.items():
        try:
            final_id = resolve_character_id(redirects, source_id)
        except ValueError:
            continue
        if final_id not in canonical_ids:
            errors.append(f"{source_id}: redirect target is not a canonical character")
    for key, original_id in character_allocations.items():
        try:
            expected_id = resolve_character_id(redirects, original_id)
        except ValueError:
            continue
        if character_key_index.get(key) != expected_id:
            errors.append(f"{key}: resolved character key index mismatch")
    for item in characters:
        if not isinstance(item, dict):
            errors.append("character record is not an object")
            continue
        if item.get("schema_version") != "2.0.0" or not CHARACTER_ID_RE.fullmatch(str(item.get("character_id"))):
            errors.append("character record version or ID is invalid")
        keys = item.get("entity_keys")
        if not isinstance(keys, list) or not keys or any(character_key_index.get(key) != item.get("character_id") for key in keys):
            errors.append(f"{item.get('character_id')}: entity key allocation mismatch")
        if not item.get("canonical_name") or not item.get("first_observation_id") or not item.get("observation_ids") or not item.get("chapter_ids") or not item.get("evidence") or not isinstance(item.get("identity_event_ids"), list):
            errors.append(f"{item.get('character_id')}: required registry facts are missing")
    for name, values in character_name_index.items():
        if name != normalize_character_name(name) or not isinstance(values, list) or not values or len(values) != len(set(values)) or any(value not in canonical_ids for value in values):
            errors.append(f"{name}: character name index entry is invalid")

    identity_candidates = registry.get("identity_candidates")
    if not isinstance(identity_candidates, list):
        errors.append("identity candidates must be an array")
        identity_candidates = []
    candidate_ids = [item.get("candidate_id") for item in identity_candidates if isinstance(item, dict)]
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append("identity candidate IDs are duplicated")
    for item in identity_candidates:
        if not isinstance(item, dict) or item.get("left_character_id") not in canonical_ids or item.get("right_character_id") not in canonical_ids:
            errors.append("identity candidate refers to a non-canonical character")

    event_log = registry.get("identity_event_log")
    if not isinstance(event_log, dict) or event_log.get("path") != "data/registries/character-identity.events.jsonl":
        errors.append("identity event log metadata is invalid")

    locations = registry.get("locations")
    if not isinstance(locations, list):
        errors.append("locations must be an array")
        locations = []
    location_ids = [item.get("location_id") for item in locations if isinstance(item, dict)]
    if len(location_ids) != len(set(location_ids)):
        errors.append("location IDs are duplicated")
    for item in locations:
        if not isinstance(item, dict):
            errors.append("location record is not an object")
            continue
        keys = item.get("entity_keys")
        if item.get("schema_version") != "2.0.0" or not LOCATION_ID_RE.fullmatch(str(item.get("location_id"))):
            errors.append("location record version or ID is invalid")
        if not isinstance(keys, list) or not keys or any(location_allocations.get(key) != item.get("location_id") for key in keys):
            errors.append(f"{item.get('location_id')}: entity key allocation mismatch")
        if not item.get("canonical_name") or not item.get("first_scene_id") or not item.get("scene_ids") or not item.get("chapter_ids") or not item.get("evidence"):
            errors.append(f"{item.get('location_id')}: required registry facts are missing")

    try:
        expected, expected_review = build_registry_data(project_dir, registry)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        errors.append(f"registry cannot be rebuilt: {exc}")
    else:
        if registry != expected:
            errors.append("registry differs from deterministic rebuild")
        if review != expected_review:
            errors.append("registry review Markdown differs from deterministic rebuild")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验角色、别名和场景地点登记表")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    errors = validate_registries(args.project_dir)
    if errors:
        print("REGISTRY VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("REGISTRY VALIDATION PASSED: stable IDs, aliases, chapter links, scene links, pending candidates and deterministic rebuild are consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
