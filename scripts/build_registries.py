#!/usr/bin/env python3
"""Build stable character, identity and location registries from chapter analyses."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

from render_chapter import load_current_analysis

CHARACTER_ID_RE = re.compile(r"^CHAR-([0-9]{4})$")
LOCATION_ID_RE = re.compile(r"^LOC-([0-9]{4})$")
IDENTITY_EVENT_ID_RE = re.compile(r"^IDENT-([0-9]{6})$")
IMPORTANCE_RANK = {"unknown": 0, "minor": 1, "supporting": 2, "candidate_major": 3}
TYPE_RANK = {"crowd": 0, "role": 1, "named": 2}
STATUS_RANK = {"inferred": 0, "explicit": 1, "user_confirmed": 2}
TYPE_LABELS = {"named": "具名角色", "role": "身份角色", "crowd": "群众角色"}
IMPORTANCE_LABELS = {"candidate_major": "主要角色候选", "supporting": "配角", "minor": "次要角色", "unknown": "待确认"}
PENDING_STATUS_LABELS = {"conflict": "证据冲突", "unknown": "待确认"}
IDENTITY_EVENT_PATH = "data/registries/character-identity.events.jsonl"


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def chapter_number(chapter_id: str) -> int:
    return int(chapter_id[1:])


def observation_sort_key(observation: dict[str, Any]) -> tuple[int, str]:
    chapter_id = observation["observation_id"].split("-")[1]
    return chapter_number(chapter_id), observation["observation_id"]


def append_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def unique_evidence(observations: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for observation in observations:
        for evidence in observation["evidence"]:
            key = (evidence["source_unit_id"], evidence["quote"])
            if key not in seen:
                seen.add(key)
                result.append(copy.deepcopy(evidence))
                if len(result) >= limit:
                    return result
    return result


def next_id(allocations: dict[str, str], pattern: re.Pattern[str], prefix: str) -> str:
    used = [int(match.group(1)) for value in allocations.values() if (match := pattern.fullmatch(value))]
    return f"{prefix}-{(max(used, default=0) + 1):04d}"


def normalize_character_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def resolve_character_id(redirects: dict[str, str], character_id: str) -> str:
    seen: set[str] = set()
    current = character_id
    while current in redirects:
        if current in seen:
            raise ValueError("character ID redirect cycle detected")
        seen.add(current)
        current = redirects[current]
    return current


def resolve_character_reference(registry: dict[str, Any], reference: str) -> dict[str, Any]:
    """Resolve a stable ID or any indexed name without exposing merge history downstream."""
    if CHARACTER_ID_RE.fullmatch(reference):
        resolved = resolve_character_id(registry.get("character_id_redirects", {}), reference)
        known = {item["character_id"] for item in registry.get("characters", [])}
        return {"status": "resolved", "character_id": resolved} if resolved in known else {"status": "not_found", "character_id": None}
    matches = registry.get("character_name_index", {}).get(normalize_character_name(reference), [])
    if len(matches) == 1:
        return {"status": "resolved", "character_id": matches[0]}
    if len(matches) > 1:
        return {"status": "ambiguous", "character_ids": matches}
    return {"status": "not_found", "character_id": None}


def location_key(location: dict[str, Any]) -> str:
    raw = "|".join(str(location.get(field) or "").strip().casefold() for field in ("parent_location", "standardized_name", "sub_location"))
    return "loc-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_analyses(project_dir: Path) -> list[tuple[dict[str, Any], Path, bytes]]:
    base_dir = project_dir / "data" / "analysis"
    chapter_ids: list[str] = []
    for path in base_dir.glob("p*.analysis.json"):
        candidate = path.name.split(".", 1)[0].upper()
        if re.fullmatch(r"P[0-9]{2,}", candidate):
            chapter_ids.append(candidate)
    if not chapter_ids:
        raise ValueError("no chapter analyses found")
    return [load_current_analysis(project_dir, chapter_id) for chapter_id in sorted(set(chapter_ids), key=chapter_number)]


def load_identity_events(project_dir: Path) -> tuple[list[dict[str, Any]], bytes]:
    path = project_dir / IDENTITY_EVENT_PATH
    if not path.exists():
        return [], b""
    data = path.read_bytes()
    events: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    last_number = 0
    for line_number, raw_line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid identity event at line {line_number}: {exc}") from exc
        match = IDENTITY_EVENT_ID_RE.fullmatch(str(event.get("event_id")))
        if event.get("schema_version") != "2.0.0" or not match:
            raise ValueError(f"invalid identity event version or ID at line {line_number}")
        number = int(match.group(1))
        if number <= last_number or event["event_id"] in seen:
            raise ValueError("identity event IDs must be unique and increasing")
        last_number = number
        operation = event.get("operation")
        if operation == "merge_characters":
            if not CHARACTER_ID_RE.fullmatch(str(event.get("source_character_id"))) or not CHARACTER_ID_RE.fullmatch(str(event.get("target_character_id"))):
                raise ValueError(f"{event['event_id']}: merge character IDs are invalid")
            for field in ("canonical_name", "chinese_name"):
                if field not in event:
                    raise ValueError(f"{event['event_id']}: missing {field}")
            for field in ("aliases", "chinese_aliases", "source_observation_ids"):
                if not isinstance(event.get(field), list):
                    raise ValueError(f"{event['event_id']}: {field} must be an array")
        elif operation == "retract_event":
            target = event.get("target_event_id")
            if target not in seen or seen[target].get("operation") != "merge_characters":
                raise ValueError(f"{event['event_id']}: retraction must target an earlier merge event")
        else:
            raise ValueError(f"{event['event_id']}: unsupported identity operation")
        if not isinstance(event.get("note"), str) or not event["note"].strip():
            raise ValueError(f"{event['event_id']}: note is required")
        events.append(event)
        seen[event["event_id"]] = event
    return events, data


def active_identity_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    retracted = {event["target_event_id"] for event in events if event["operation"] == "retract_event"}
    active = [event for event in events if event["operation"] == "merge_characters" and event["event_id"] not in retracted]
    return active, sorted(retracted, key=lambda value: int(value.split("-")[1]))


def prepare_character_groups(
    analyses: list[dict[str, Any]], allocations: dict[str, str]
) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    allocations = dict(allocations)
    by_key: dict[str, list[dict[str, Any]]] = {}
    for analysis in analyses:
        for observation in analysis["character_observations"]:
            by_key.setdefault(observation["entity_key"], []).append(observation)

    eligible: dict[str, list[dict[str, Any]]] = {}
    candidates: list[dict[str, Any]] = []
    for key, raw_observations in by_key.items():
        observations = sorted(raw_observations, key=observation_sort_key)
        distinct_positions = {(item["scene_id"], item["observation_id"].split("-")[1]) for item in observations}
        identity_resolved = any(item["status"] not in {"unknown", "conflict"} for item in observations)
        qualifies = identity_resolved and (
            any(item["character_type"] != "crowd" or item["has_dialogue"] for item in observations)
            or len(distinct_positions) >= 2
        )
        if not qualifies:
            first = observations[0]
            candidates.append({
                "entity_key": key,
                "canonical_label": first["canonical_label"],
                "chinese_label": first["chinese_label"],
                "observation_ids": [item["observation_id"] for item in observations],
                "chapter_ids": list(dict.fromkeys(item["observation_id"].split("-")[1] for item in observations)),
                "reason": "identity_unresolved" if not identity_resolved else "crowd_requires_recurrence",
            })
            continue
        eligible[key] = observations
        if key not in allocations:
            matched = sorted({item["matched_character_id"] for item in observations if item["matched_character_id"]})
            allocations[key] = matched[0] if len(matched) == 1 else next_id(allocations, CHARACTER_ID_RE, "CHAR")
    return allocations, eligible, sorted(candidates, key=lambda item: item["entity_key"])


def build_redirects(
    all_character_ids: set[str], active_events: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    parent = {character_id: character_id for character_id in all_character_ids}

    def find(character_id: str) -> str:
        if character_id not in parent:
            raise ValueError(f"identity event refers to unknown character ID {character_id}")
        trail: list[str] = []
        while parent[character_id] != character_id:
            trail.append(character_id)
            character_id = parent[character_id]
        for item in trail:
            parent[item] = character_id
        return character_id

    for event in active_events:
        source = find(event["source_character_id"])
        target = find(event["target_character_id"])
        if source == target:
            raise ValueError(f"{event['event_id']}: characters already resolve to the same identity")
        parent[source] = target

    redirects = {character_id: find(character_id) for character_id in sorted(parent) if find(character_id) != character_id}
    events_by_root: dict[str, list[dict[str, Any]]] = {}
    for event in active_events:
        events_by_root.setdefault(find(event["target_character_id"]), []).append(event)
    return redirects, events_by_root


def build_character_data(
    analyses: list[dict[str, Any]], allocations: dict[str, str], active_events: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]], dict[str, str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    allocations, eligible, candidates = prepare_character_groups(analyses, allocations)
    all_ids = set(allocations.values())
    redirects, events_by_root = build_redirects(all_ids, active_events)
    key_index = {key: resolve_character_id(redirects, character_id) for key, character_id in allocations.items()}

    observations_by_id: dict[str, list[dict[str, Any]]] = {}
    keys_by_id: dict[str, list[str]] = {}
    identity_conflicts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for key, observations in eligible.items():
        original_id = allocations[key]
        canonical_id = resolve_character_id(redirects, original_id)
        observations_by_id.setdefault(canonical_id, []).extend(observations)
        keys_by_id.setdefault(canonical_id, []).append(key)
        for observation in observations:
            matched = observation.get("matched_character_id")
            if not matched:
                continue
            if matched not in all_ids:
                raise ValueError(f"{observation['observation_id']}: matched character ID {matched} is unknown")
            matched_canonical = resolve_character_id(redirects, matched)
            if matched_canonical != canonical_id:
                pair = tuple(sorted((canonical_id, matched_canonical)))
                identity_conflicts.setdefault(pair, []).append(observation)

    identity_candidates: list[dict[str, Any]] = []
    for ordinal, pair in enumerate(sorted(identity_conflicts), start=1):
        observations = sorted(identity_conflicts[pair], key=observation_sort_key)
        identity_candidates.append({
            "candidate_id": f"IDN-{ordinal:04d}",
            "left_character_id": pair[0],
            "right_character_id": pair[1],
            "observation_ids": list(dict.fromkeys(item["observation_id"] for item in observations)),
            "chapter_ids": list(dict.fromkeys(item["observation_id"].split("-")[1] for item in observations)),
            "reason": "matched_character_conflict",
            "evidence": unique_evidence(observations),
        })

    characters: list[dict[str, Any]] = []
    name_index: dict[str, list[str]] = {}
    for character_id in sorted(observations_by_id, key=lambda value: int(value.split("-")[1])):
        observations = sorted(observations_by_id[character_id], key=observation_sort_key)
        preferred = max(observations, key=lambda item: (TYPE_RANK[item["character_type"]], STATUS_RANK.get(item["status"], -1)))
        merge_events = events_by_root.get(character_id, [])
        canonical_name = preferred["canonical_label"]
        chinese_name = preferred["chinese_label"]
        aliases: list[str] = []
        chinese_aliases: list[str] = []
        for item in observations:
            append_unique(aliases, [item["canonical_label"], *item["aliases"]])
            append_unique(chinese_aliases, ([item["chinese_label"]] if item["chinese_label"] else []) + item["chinese_aliases"])
        for event in merge_events:
            if event.get("canonical_name"):
                canonical_name = event["canonical_name"]
            if event.get("chinese_name"):
                chinese_name = event["chinese_name"]
            append_unique(aliases, event.get("aliases", []))
            append_unique(chinese_aliases, event.get("chinese_aliases", []))
        aliases = [value for value in aliases if value != canonical_name]
        chinese_aliases = [value for value in chinese_aliases if value != chinese_name]
        record = {
            "schema_version": "2.0.0",
            "character_id": character_id,
            "entity_keys": sorted(set(keys_by_id[character_id])),
            "canonical_name": canonical_name,
            "chinese_name": chinese_name,
            "aliases": aliases,
            "chinese_aliases": chinese_aliases,
            "character_type": max((item["character_type"] for item in observations), key=lambda value: TYPE_RANK[value]),
            "importance": max((item["importance_hint"] for item in observations), key=lambda value: IMPORTANCE_RANK[value]),
            "first_observation_id": observations[0]["observation_id"],
            "observation_ids": list(dict.fromkeys(item["observation_id"] for item in observations)),
            "chapter_ids": list(dict.fromkeys(item["observation_id"].split("-")[1] for item in observations)),
            "identity_event_ids": [event["event_id"] for event in merge_events],
            "evidence": unique_evidence(observations),
        }
        characters.append(record)
        for label in [canonical_name, chinese_name, *aliases, *chinese_aliases]:
            if not label:
                continue
            normalized = normalize_character_name(label)
            if normalized:
                name_index.setdefault(normalized, [])
                append_unique(name_index[normalized], [character_id])
    name_index = {key: sorted(values, key=lambda value: int(value.split("-")[1])) for key, values in sorted(name_index.items())}
    return allocations, key_index, name_index, redirects, characters, candidates, identity_candidates


def build_location_data(
    analyses: list[dict[str, Any]], allocations: dict[str, str]
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    allocations = dict(allocations)
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    candidates: list[dict[str, Any]] = []
    keys_by_id: dict[str, list[str]] = {}
    for analysis in analyses:
        for scene in analysis["scenes"]:
            location = scene["location"]
            if location["status"] in {"unknown", "conflict"} or not location.get("standardized_name"):
                candidates.append({
                    "scene_id": scene["scene_id"], "chapter_id": analysis["chapter_id"],
                    "source_text": location.get("source_text"), "proposed_name": location.get("standardized_name"),
                    "status": location["status"] if location["status"] in {"unknown", "conflict"} else "unknown",
                    "evidence": copy.deepcopy(location["evidence"]),
                })
                continue
            key = location_key(location)
            allocated = allocations.get(key)
            matched = location.get("location_id")
            if matched and allocated and matched != allocated:
                raise ValueError(f"location identity conflict for {scene['scene_id']}")
            if matched:
                allocations[key] = matched
            elif not allocated:
                allocations[key] = next_id(allocations, LOCATION_ID_RE, "LOC")
            location_id = allocations[key]
            keys_by_id.setdefault(location_id, []).append(key)
            grouped.setdefault(location_id, []).append((scene["scene_id"], location))

    locations: list[dict[str, Any]] = []
    for location_id in sorted(grouped, key=lambda value: int(value.split("-")[1])):
        rows = grouped[location_id]
        first_scene, first = rows[0]
        aliases: list[str] = []
        parents: list[str] = []
        subs: list[str] = []
        evidence: list[dict[str, Any]] = []
        seen_evidence: set[tuple[str, str]] = set()
        for _, location in rows:
            if location.get("source_text") and location["source_text"] != first["standardized_name"]:
                append_unique(aliases, [location["source_text"]])
            if location.get("standardized_name") != first["standardized_name"]:
                append_unique(aliases, [location["standardized_name"]])
            if location.get("parent_location"):
                append_unique(parents, [location["parent_location"]])
            if location.get("sub_location"):
                append_unique(subs, [location["sub_location"]])
            for item in location["evidence"]:
                evidence_key = (item["source_unit_id"], item["quote"])
                if evidence_key not in seen_evidence and len(evidence) < 8:
                    seen_evidence.add(evidence_key)
                    evidence.append(copy.deepcopy(item))
        locations.append({
            "schema_version": "2.0.0", "location_id": location_id,
            "entity_keys": sorted(set(keys_by_id[location_id])), "canonical_name": first["standardized_name"],
            "aliases": aliases, "parent_names": parents, "sub_locations": subs,
            "first_scene_id": first_scene, "scene_ids": [scene_id for scene_id, _ in rows],
            "chapter_ids": list(dict.fromkeys(scene_id.split("-")[0] for scene_id, _ in rows)),
            "status": max((location["status"] for _, location in rows), key=lambda value: STATUS_RANK[value]),
            "evidence": evidence,
        })
    return allocations, locations, candidates


def translation_by_unit(analyses: list[dict[str, Any]]) -> dict[str, str]:
    translations: dict[str, str] = {}
    for analysis in analyses:
        for unit in analysis["unit_analyses"]:
            values = [segment["translation"]["text_zh"] for segment in unit["segments"] if segment.get("translation", {}).get("text_zh")]
            if values:
                translations[unit["unit_id"]] = "".join(values)
    return translations


def render_registry_markdown(registry: dict[str, Any], translations: dict[str, str]) -> str:
    lines = ["# 角色与场景登记表", "", "本文件用于人工查看；稳定 ID、多名称索引和归并历史由系统内部维护。", "", "## 角色", ""]
    if not registry["characters"]:
        lines.extend(["暂无已登记角色。", ""])
    for item in registry["characters"]:
        name = item["canonical_name"] + (f" / {item['chinese_name']}" if item["chinese_name"] else "")
        lines.extend([f"### {item['character_id']}｜{name}", "", f"- 类型：{TYPE_LABELS[item['character_type']]}", f"- 剧情重要性：{IMPORTANCE_LABELS[item['importance']]}", f"- 涉及章节：{'、'.join(item['chapter_ids'])}"])
        if item["aliases"] or item["chinese_aliases"]:
            lines.append(f"- 已知称谓：{'、'.join([*item['aliases'], *item['chinese_aliases']])}")
        lines.append("")
    if registry["identity_candidates"]:
        by_id = {item["character_id"]: item for item in registry["characters"]}
        lines.extend(["## 待确认的同一角色", ""])
        for candidate in registry["identity_candidates"]:
            left = by_id[candidate["left_character_id"]]
            right = by_id[candidate["right_character_id"]]
            lines.extend(["---", "", f"### {candidate['candidate_id']}", "", f"系统发现“{left['canonical_name']}”与“{right['canonical_name']}”可能是同一角色，请确认是否归并。", ""])
            for ordinal, evidence in enumerate(candidate["evidence"], start=1):
                lines.extend([f"#### 线索 {ordinal}", "", f"- 原文：{evidence['quote']}"])
                translated = translations.get(evidence["source_unit_id"])
                if translated:
                    lines.append(f"- 中文参考：{translated}")
                lines.append("")
        lines.extend(["---", ""])
    if registry["character_candidates"]:
        lines.extend(["## 待确认角色候选", ""])
        for item in registry["character_candidates"]:
            message = "身份证据仍有冲突，确认后再分配正式角色 ID。" if item["reason"] == "identity_unresolved" else "当前仅出现一次，达到重复出现条件后再分配正式角色 ID。"
            lines.extend([f"- {item['canonical_label']}：{message}", ""])
    lines.extend(["## 场景地点", ""])
    if not registry["locations"]:
        lines.extend(["暂无已确认场景地点。", ""])
    for item in registry["locations"]:
        lines.extend([f"### {item['location_id']}｜{item['canonical_name']}", "", f"- 出现场景：{'、'.join(item['scene_ids'])}"])
        if item["aliases"]:
            lines.append(f"- 原文称呼：{'、'.join(item['aliases'])}")
        if item["parent_names"]:
            lines.append(f"- 上级地点：{'、'.join(item['parent_names'])}")
        lines.append("")
    if registry["location_candidates"]:
        lines.extend(["## 待确认场景地点", ""])
        for item in registry["location_candidates"]:
            label = item["proposed_name"] or item["source_text"] or "地点未说明"
            lines.extend([f"- {item['scene_id']}：{label}（{PENDING_STATUS_LABELS[item['status']]}）", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_registry_data(project_dir: Path, existing: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    loaded = load_analyses(project_dir)
    analyses = [item[0] for item in loaded]
    existing = existing or {}
    events, event_data = load_identity_events(project_dir)
    active_events, retracted_event_ids = active_identity_events(events)
    character_result = build_character_data(analyses, existing.get("character_allocations", {}), active_events)
    character_allocations, character_key_index, character_name_index, redirects, characters, character_candidates, identity_candidates = character_result
    location_allocations, locations, location_candidates = build_location_data(analyses, existing.get("location_allocations", {}))
    registry = {
        "schema_version": "2.0.0", "registry_version": "entity-registry-v2",
        "analysis_inputs": [{"chapter_id": analysis["chapter_id"], "path": path.relative_to(project_dir).as_posix(), "sha256": sha256(data)} for analysis, path, data in loaded],
        "identity_event_log": {
            "path": IDENTITY_EVENT_PATH, "sha256": sha256(event_data) if event_data else None,
            "active_event_ids": [event["event_id"] for event in active_events], "retracted_event_ids": retracted_event_ids,
        },
        "character_allocations": dict(sorted(character_allocations.items())),
        "character_key_index": dict(sorted(character_key_index.items())),
        "character_name_index": character_name_index,
        "character_id_redirects": dict(sorted(redirects.items())),
        "characters": characters, "character_candidates": character_candidates, "identity_candidates": identity_candidates,
        "location_allocations": dict(sorted(location_allocations.items())), "locations": locations, "location_candidates": location_candidates,
    }
    return registry, render_registry_markdown(registry, translation_by_unit(analyses))


def build_registries(project_dir: Path) -> tuple[Path, Path, dict[str, Any]]:
    project_dir = project_dir.resolve()
    registry_path = project_dir / "data" / "registries" / "entities.json"
    review_path = project_dir / "work" / "registry" / "entities.md"
    existing = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else None
    registry, markdown = build_registry_data(project_dir, existing)
    atomic_write(registry_path, json_bytes(registry))
    atomic_write(review_path, markdown.encode("utf-8"))
    return registry_path, review_path, registry


def main() -> int:
    parser = argparse.ArgumentParser(description="从当前章节解析视图建立统一角色、多名称索引与场景地点登记表")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    try:
        registry_path, review_path, registry = build_registries(args.project_dir)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"REGISTRY BUILD FAILED: {exc}")
        return 1
    print(f"ENTITY REGISTRY: {registry_path}")
    print(f"REVIEW MARKDOWN: {review_path}")
    print(f"CHARACTERS: {len(registry['characters'])}; LOCATIONS: {len(registry['locations'])}")
    print(f"PENDING IDENTITIES: {len(registry['identity_candidates'])}; PENDING CHARACTER GROUPS: {len(registry['character_candidates'])}; PENDING LOCATIONS: {len(registry['location_candidates'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
