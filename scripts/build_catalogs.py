#!/usr/bin/env python3
"""Incrementally cache chapter deltas and deterministically build V3 catalogs."""
from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from preprod import active_events, load_project, normalize_key, read_jsonl, sha256, table_cell, unique, write_json
from review_analysis import load_current_analysis
from validate_analysis import validate_record

CHARACTER_FIELDS = ["age", "gender", "race", "identity", "appearance", "hair", "body", "special_marks", "clothing", "personality"]
FIELD_ZH = {
    "age": "年龄", "gender": "性别", "race": "种族", "identity": "身份", "appearance": "外貌",
    "hair": "发型", "body": "体型", "special_marks": "特殊标记", "clothing": "服装", "personality": "性格",
    "space": "空间", "environment": "环境", "furniture": "陈设", "state_change": "状态变化",
}
ASSET_STATUS_ZH = {
    "pending": "待审核", "approved": "已确认", "excluded": "不制作", "information_pending": "资料待补充",
}


class UnionFind:
    def __init__(self, values: Iterable[str] = ()) -> None:
        self.parent = {value: value for value in values}

    def add(self, value: str) -> None:
        self.parent.setdefault(value, value)

    def find(self, value: str) -> str:
        self.add(value)
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            first, second = sorted((left_root, right_root))
            self.parent[second] = first

    def groups(self) -> list[list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for value in self.parent:
            grouped[self.find(value)].append(value)
        return [sorted(values) for _, values in sorted(grouped.items())]


def _chapter_observation(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_id": record["chapter_id"],
        "title_en": record["title_en"], "title_zh": record["title_zh"],
        "scenes": [{key: scene[key] for key in ("scene_id", "name_zh", "name_en", "location_ref", "int_ext", "time", "summary_zh", "character_refs")} for scene in record["scenes"]],
        "characters": copy.deepcopy(record["characters"]),
        "character_facts": copy.deepcopy(record["character_facts"]),
        "locations": copy.deepcopy(record["locations"]),
        "appearances": copy.deepcopy(record["appearances"]),
        "identity_links": copy.deepcopy(record["identity_links"]),
        "asset_candidates": copy.deepcopy(record["asset_candidates"]),
        "issues": copy.deepcopy(record["issues"]),
    }


def update_observation_cache(project_dir: Path) -> tuple[dict[str, Any], int, int]:
    cache_path = project_dir / "data" / "catalogs" / "observations.json"
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        cache = {"schema_version": "3.0.0", "chapter_inputs": {}, "chapters": {}}
    _, manifest = load_project(project_dir)
    valid_ids: set[str] = set()
    reused = changed = 0
    for chapter in manifest["chapters"]:
        chapter_id = chapter["chapter_id"]
        base = project_dir / "data" / "chapters" / f"{chapter_id.lower()}.json"
        if not base.exists():
            continue
        record, _, data = load_current_analysis(project_dir, chapter_id)
        errors = validate_record(project_dir, chapter_id, record)
        if errors:
            continue
        fingerprint = sha256(data)
        valid_ids.add(chapter_id)
        if cache["chapter_inputs"].get(chapter_id) == fingerprint:
            reused += 1
            continue
        cache["chapter_inputs"][chapter_id] = fingerprint
        cache["chapters"][chapter_id] = _chapter_observation(record)
        changed += 1
    for chapter_id in list(cache["chapters"]):
        if chapter_id not in valid_ids:
            cache["chapters"].pop(chapter_id, None)
            cache["chapter_inputs"].pop(chapter_id, None)
            changed += 1
    write_json(cache_path, cache)
    return cache, reused, changed


def _previous_map(previous: dict[str, Any], kind: str, id_field: str, history_key: str) -> tuple[dict[str, set[str]], int]:
    mapping: dict[str, set[str]] = {
        entity_id: set(refs) for entity_id, refs in previous.get(history_key, {}).items()
    }
    largest = 0
    for item in previous.get(kind, []):
        entity_id = item[id_field]
        mapping[entity_id] = set(item.get("source_refs", []))
        try:
            largest = max(largest, int(entity_id.split("-")[-1]))
        except ValueError:
            pass
    return mapping, largest


def _assign_ids(groups: list[list[str]], previous_refs: dict[str, set[str]], prefix: str, largest: int, preferred_ids: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    assigned: dict[str, str] = {}
    redirects: dict[str, str] = {}
    used: set[str] = set()
    ordered = sorted(groups, key=lambda values: values[0])
    for values in ordered:
        matches = sorted(
            (entity_id for entity_id, refs in previous_refs.items() if refs.intersection(values) and entity_id not in used),
            key=lambda value: int(value.split("-")[-1]),
        )
        preferred = next((value for value in reversed(preferred_ids) if value in matches), None)
        if matches:
            entity_id = preferred or matches[0]
            for old_id in matches:
                if old_id == entity_id:
                    continue
                redirects[old_id] = entity_id
        else:
            largest += 1
            entity_id = f"{prefix}-{largest:04d}"
        used.add(entity_id)
        for value in values:
            assigned[value] = entity_id
    return assigned, redirects


def _entity_events(project_dir: Path) -> list[dict[str, Any]]:
    return active_events(read_jsonl(project_dir / "data" / "reviews" / "entities.events.jsonl"))


def _event_unions(union: UnionFind, events: list[dict[str, Any]], previous_refs: dict[str, set[str]], entity_type: str) -> None:
    for event in events:
        if event.get("operation") != "merge" or event.get("entity_type") != entity_type:
            continue
        source_refs = previous_refs.get(str(event.get("source_id")), set())
        target_refs = previous_refs.get(str(event.get("target_id")), set())
        combined = sorted(source_refs | target_refs)
        for value in combined:
            union.add(value)
        for value in combined[1:]:
            union.union(combined[0], value)


def _fact_fields(observations: list[dict[str, Any]], refs: set[str], entity_type: str) -> dict[str, dict[str, Any]]:
    collected: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for chapter in observations:
        chapter_id = chapter["chapter_id"]
        if entity_type == "character":
            items = [item for item in chapter["character_facts"] if normalize_key(item["subject_ref"]) in refs]
        else:
            items = []
            for location in chapter["locations"]:
                if normalize_key(location["ref"]) in refs:
                    items.extend(location["facts"])
        for item in items:
            source = {"chapter_id": chapter_id, "block_ids": item["source_block_ids"]}
            collected[item["field"]][item["value_zh"]].append(source)
    required = CHARACTER_FIELDS if entity_type == "character" else ["space", "environment", "furniture", "state_change"]
    result: dict[str, dict[str, Any]] = {}
    for field in required:
        values = [
            {"value_zh": value, "sources": sources}
            for value, sources in collected.get(field, {}).items()
        ]
        result[field] = {"status": "unknown" if not values else "confirmed" if len(values) == 1 else "conflict", "values": values}
    return result


def _priority(values: Iterable[str], ranking: list[str], fallback: str) -> str:
    values = set(values)
    return next((value for value in ranking if value in values), fallback)


def _choose_text(values: Iterable[str | None]) -> str | None:
    return next((value for value in values if isinstance(value, str) and value.strip()), None)


def _resolve_ref(ref: str, mapping: dict[str, str], previous: dict[str, Any], entity_type: str) -> str | None:
    normalized = normalize_key(ref)
    if normalized in mapping:
        return mapping[normalized]
    id_field = "character_id" if entity_type == "character" else "location_id"
    records = previous.get("characters" if entity_type == "character" else "locations", [])
    match = next((item for item in records if item[id_field] == ref), None)
    if match:
        targets = {mapping[value] for value in match.get("source_refs", []) if value in mapping}
        if len(targets) == 1:
            return next(iter(targets))
    return None


def _apply_entity_sets(catalog: dict[str, Any], events: list[dict[str, Any]]) -> None:
    char_by_id = {item["character_id"]: item for item in catalog["characters"]}
    loc_by_id = {item["location_id"]: item for item in catalog["locations"]}
    redirects = {**catalog["character_redirects"], **catalog["location_redirects"]}
    for event in events:
        if event.get("operation") != "set":
            continue
        target_id = str(event.get("target_id"))
        while target_id in redirects:
            target_id = redirects[target_id]
        item = char_by_id.get(target_id) if event.get("entity_type") == "character" else loc_by_id.get(target_id)
        if item is None:
            continue
        field = event.get("field")
        if event.get("entity_type") == "character" and field in CHARACTER_FIELDS:
            item["fields"][field] = {"status": "user_confirmed", "values": [{"value_zh": str(event.get("value")), "sources": []}]}
        elif field in item:
            item[field] = copy.deepcopy(event.get("value"))


def _build_entities(project_dir: Path, observations: list[dict[str, Any]], previous: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events = _entity_events(project_dir)
    char_refs = {normalize_key(item["ref"]) for chapter in observations for item in chapter["characters"]}
    loc_refs = {normalize_key(item["ref"]) for chapter in observations for item in chapter["locations"]}
    char_union, loc_union = UnionFind(char_refs), UnionFind(loc_refs)
    for chapter in observations:
        for link in chapter["identity_links"]:
            if link["relationship"] != "same":
                continue
            target = char_union if link["entity_type"] == "character" else loc_union
            target.union(normalize_key(link["left_ref"]), normalize_key(link["right_ref"]))
    previous_chars, largest_char = _previous_map(previous, "characters", "character_id", "character_id_history")
    previous_locs, largest_loc = _previous_map(previous, "locations", "location_id", "location_id_history")
    _event_unions(char_union, events, previous_chars, "character")
    _event_unions(loc_union, events, previous_locs, "location")
    char_preferred = [str(event["target_id"]) for event in events if event.get("operation") == "merge" and event.get("entity_type") == "character"]
    loc_preferred = [str(event["target_id"]) for event in events if event.get("operation") == "merge" and event.get("entity_type") == "location"]
    char_map, char_redirects = _assign_ids(char_union.groups(), previous_chars, "CHAR", largest_char, char_preferred)
    loc_map, loc_redirects = _assign_ids(loc_union.groups(), previous_locs, "LOC", largest_loc, loc_preferred)
    characters: list[dict[str, Any]] = []
    for group in char_union.groups():
        refs = set(group)
        declarations = [item for chapter in observations for item in chapter["characters"] if normalize_key(item["ref"]) in refs]
        aliases_en = unique(value for item in declarations for value in [item["canonical_name_en"], *item.get("aliases_en", [])])
        aliases_zh = unique(value for item in declarations for value in ([item.get("name_zh")] if item.get("name_zh") else []) + item.get("aliases_zh", []))
        entity_id = char_map[group[0]]
        appearances = []
        for chapter in observations:
            scenes = {item["scene_id"]: item for item in chapter["scenes"]}
            for appearance in chapter["appearances"]:
                if normalize_key(appearance["character_ref"]) not in refs:
                    continue
                scene = scenes[appearance["scene_id"]]
                appearances.append({
                    "chapter_id": chapter["chapter_id"], "scene_id": appearance["scene_id"],
                    "scene_name_zh": scene["name_zh"], "presence": appearance["presence"],
                    "time": scene["time"], "int_ext": scene["int_ext"],
                    "summary_zh": appearance["summary_zh"], "source_block_ids": appearance["source_block_ids"],
                })
        roles = unique(item["story_role_zh"] for item in declarations if item.get("story_role_zh"))
        characters.append({
            "character_id": entity_id, "source_refs": group,
            "canonical_name_en": _choose_text(item.get("canonical_name_en") for item in declarations) or entity_id,
            "name_zh": _choose_text(item.get("name_zh") for item in declarations),
            "aliases_en": aliases_en, "aliases_zh": aliases_zh,
            "category": _priority((item["category"] for item in declarations), ["named", "speaking", "recurring_role", "crowd"], "crowd"),
            "importance": _priority((item["importance"] for item in declarations), ["major", "supporting", "minor", "unknown"], "unknown"),
            "story_role_zh": roles[0] if len(roles) == 1 else None,
            "story_role_candidates_zh": roles,
            "fields": _fact_fields(observations, refs, "character"),
            "appearances": appearances,
        })
    characters.sort(key=lambda item: int(item["character_id"].split("-")[1]))

    locations: list[dict[str, Any]] = []
    for group in loc_union.groups():
        refs = set(group)
        declarations = [item for chapter in observations for item in chapter["locations"] if normalize_key(item["ref"]) in refs]
        aliases_en = unique(value for item in declarations for value in ([item.get("name_en")] if item.get("name_en") else []) + item.get("aliases_en", []))
        aliases_zh = unique(value for item in declarations for value in [item["name_zh"], *item.get("aliases_zh", [])])
        entity_id = loc_map[group[0]]
        appearances = []
        for chapter in observations:
            for scene in chapter["scenes"]:
                if normalize_key(scene["location_ref"]) in refs:
                    appearances.append({
                        "chapter_id": chapter["chapter_id"], "scene_id": scene["scene_id"],
                        "scene_name_zh": scene["name_zh"], "summary_zh": scene["summary_zh"],
                    })
        locations.append({
            "location_id": entity_id, "source_refs": group,
            "name_en": _choose_text(item.get("name_en") for item in declarations),
            "name_zh": _choose_text(item.get("name_zh") for item in declarations) or entity_id,
            "aliases_en": aliases_en, "aliases_zh": aliases_zh,
            "parent_zh": _choose_text(item.get("parent_zh") for item in declarations),
            "int_ext": _priority((item["int_ext"] for item in declarations), ["INT", "EXT", "MIXED", "UNKNOWN"], "UNKNOWN"),
            "fields": _fact_fields(observations, refs, "location"), "appearances": appearances,
        })
    locations.sort(key=lambda item: int(item["location_id"].split("-")[1]))

    catalog = {
        "characters": characters, "locations": locations,
        "character_redirects": char_redirects, "location_redirects": loc_redirects,
        "character_id_history": {**{key: sorted(value) for key, value in previous_chars.items()}, **{item["character_id"]: item["source_refs"] for item in characters}},
        "location_id_history": {**{key: sorted(value) for key, value in previous_locs.items()}, **{item["location_id"]: item["source_refs"] for item in locations}},
    }
    _apply_entity_sets(catalog, events)
    return catalog, events


def _alias_index(records: list[dict[str, Any]], id_field: str, name_fields: list[str]) -> tuple[dict[str, str], dict[str, list[str]]]:
    candidates: dict[str, set[str]] = defaultdict(set)
    for item in records:
        values: list[str] = list(item.get("source_refs", []))
        for field in name_fields:
            value = item.get(field)
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, list):
                values.extend(str(entry) for entry in value)
        for value in values:
            candidates[normalize_key(value)].add(item[id_field])
    direct = {key: next(iter(ids)) for key, ids in candidates.items() if len(ids) == 1}
    ambiguous = {key: sorted(ids) for key, ids in candidates.items() if len(ids) > 1}
    return dict(sorted(direct.items())), dict(sorted(ambiguous.items()))


def _asset_events(project_dir: Path) -> list[dict[str, Any]]:
    return active_events(read_jsonl(project_dir / "data" / "reviews" / "assets.events.jsonl"))


def _asset_stable_id(stable_key: str, previous: dict[str, Any], used: set[str], largest: list[int]) -> str:
    match = next((item for item in previous.get("assets", []) if item.get("stable_key") == stable_key and item["asset_id"] not in used), None)
    if match:
        used.add(match["asset_id"])
        return match["asset_id"]
    largest[0] += 1
    asset_id = f"ASSET-{largest[0]:04d}"
    used.add(asset_id)
    return asset_id


def _build_assets(project_dir: Path, observations: list[dict[str, Any]], catalog: dict[str, Any], previous: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    prior_largest = max([int(item["asset_id"].split("-")[1]) for item in previous.get("assets", [])] or [0])
    largest, used = [prior_largest], set()
    assets: list[dict[str, Any]] = []
    for item in catalog["characters"]:
        actual = [entry for entry in item["appearances"] if entry["presence"] != "mentioned"]
        if not actual:
            continue
        stable = f"character:{item['character_id']}"
        assets.append({
            "asset_id": _asset_stable_id(stable, previous, used, largest), "stable_key": stable,
            "type": "character", "name_zh": item.get("name_zh") or item["canonical_name_en"], "name_en": item["canonical_name_en"],
            "subject_id": item["character_id"], "variant_zh": None,
            "first_appearance": actual[0]["scene_id"], "scene_ids": unique(entry["scene_id"] for entry in actual),
            "reason_zh": "角色在剧情中实际出镜，需要由用户决定是否设计角色资产。", "status": "pending", "source": "derived_character", "merged_into": None,
        })
    for item in catalog["locations"]:
        if not item["appearances"]:
            continue
        stable = f"location:{item['location_id']}"
        assets.append({
            "asset_id": _asset_stable_id(stable, previous, used, largest), "stable_key": stable,
            "type": "location", "name_zh": item["name_zh"], "name_en": item.get("name_en"),
            "subject_id": item["location_id"], "variant_zh": None,
            "first_appearance": item["appearances"][0]["scene_id"], "scene_ids": unique(entry["scene_id"] for entry in item["appearances"]),
            "reason_zh": "具体生产场景在剧情中出现，需要由用户决定是否设计场景资产。", "status": "pending", "source": "derived_location", "merged_into": None,
        })
    char_index = catalog["character_alias_index"]
    loc_index = catalog["location_alias_index"]
    grouped: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for chapter in observations:
        for candidate in chapter["asset_candidates"]:
            stable = f"chapter:{candidate['type']}:{normalize_key(candidate['candidate_key'])}"
            grouped[stable].append((candidate, chapter["chapter_id"]))
    for stable, candidates in sorted(grouped.items()):
        first, _ = candidates[0]
        subject_id = None
        if first.get("subject_ref"):
            index = char_index if first["type"] in {"character", "costume"} else loc_index
            subject_id = index.get(normalize_key(first["subject_ref"]))
        scenes = unique(scene for item, _ in candidates for scene in item["scene_ids"])
        assets.append({
            "asset_id": _asset_stable_id(stable, previous, used, largest), "stable_key": stable,
            "type": first["type"], "name_zh": first["name_zh"], "name_en": first.get("name_en"),
            "subject_id": subject_id, "variant_zh": first.get("variant_zh"),
            "first_appearance": scenes[0], "scene_ids": scenes,
            "reason_zh": first["reason_zh"], "status": "pending", "source": "chapter_candidate", "merged_into": None,
        })
    assets.sort(key=lambda item: int(item["asset_id"].split("-")[1]))
    redirects: dict[str, str] = {}
    events = _asset_events(project_dir)
    by_id = {item["asset_id"]: item for item in assets}
    for event in events:
        operation = event.get("operation")
        asset_id = str(event.get("asset_id"))
        while asset_id in redirects:
            asset_id = redirects[asset_id]
        if operation == "decide" and asset_id in by_id:
            by_id[asset_id]["status"] = event["status"]
        elif operation == "merge":
            target = str(event.get("target_asset_id"))
            while target in redirects:
                target = redirects[target]
            if asset_id in by_id and target in by_id and asset_id != target:
                source, destination = by_id[asset_id], by_id[target]
                destination["scene_ids"] = unique([*destination["scene_ids"], *source["scene_ids"]])
                destination["reason_zh"] = destination["reason_zh"] + "；" + source["reason_zh"]
                redirects[asset_id] = target
                source["merged_into"] = target
        elif operation == "split" and asset_id in by_id:
            source = by_id[asset_id]
            selected = [scene for scene in (event.get("scene_ids") or []) if scene in source["scene_ids"]]
            if selected:
                stable = f"split:{event['event_id']}"
                new_id = _asset_stable_id(stable, previous, used, largest)
                split_item = copy.deepcopy(source)
                split_item.update({"asset_id": new_id, "stable_key": stable, "name_zh": event["name_zh"], "scene_ids": selected, "first_appearance": selected[0], "status": "pending", "source": "user_split", "merged_into": None})
                source["scene_ids"] = [scene for scene in source["scene_ids"] if scene not in selected]
                by_id[new_id] = split_item
    return sorted(by_id.values(), key=lambda item: int(item["asset_id"].split("-")[1])), redirects


def _issue_summary(observations: list[dict[str, Any]], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    issues = []
    for chapter in observations:
        for issue in chapter["issues"]:
            if issue["status"] == "pending":
                issues.append({"issue_id": issue["issue_id"], "chapter_id": chapter["chapter_id"], "scope_id": issue["scope_id"], "question_zh": issue["question_zh"], "kind": "chapter", "evidence_block_ids": issue["evidence_block_ids"]})
        for link in chapter["identity_links"]:
            if link["relationship"] == "possible_same":
                index = catalog["character_alias_index"] if link["entity_type"] == "character" else catalog["location_alias_index"]
                left_id = index.get(normalize_key(link["left_ref"]))
                right_id = index.get(normalize_key(link["right_ref"]))
                pair = sorted([left_id, right_id]) if left_id and right_id else []
                if left_id and left_id == right_id:
                    continue
                if pair and {"entity_type": link["entity_type"], "ids": pair} in catalog.get("separate_entity_pairs", []):
                    continue
                issues.append({"issue_id": link["link_id"], "chapter_id": chapter["chapter_id"], "scope_id": link["left_ref"], "question_zh": f"是否将 {link['left_ref']} 与 {link['right_ref']} 归并为同一{('角色' if link['entity_type'] == 'character' else '地点')}？", "kind": "identity", "evidence_block_ids": link["source_block_ids"]})
    for character in catalog["characters"]:
        for field, value in character["fields"].items():
            if value["status"] == "conflict":
                sources = [source for entry in value["values"] for source in entry["sources"]]
                issues.append({"issue_id": f"CONFLICT-{character['character_id']}-{field}", "chapter_id": None, "scope_id": character["character_id"], "question_zh": f"{character.get('name_zh') or character['canonical_name_en']}的{FIELD_ZH[field]}存在冲突，请确认。", "kind": "fact_conflict", "evidence_refs": sources})
        if len(character["story_role_candidates_zh"]) > 1:
            issues.append({"issue_id": f"CONFLICT-{character['character_id']}-story-role", "chapter_id": None, "scope_id": character["character_id"], "question_zh": "人物剧情定位存在冲突，请确认。", "kind": "fact_conflict", "evidence_refs": []})
    return issues


def _character_title(item: dict[str, Any]) -> str:
    return f"{item['name_zh']} / {item['canonical_name_en']}" if item.get("name_zh") else item["canonical_name_en"]


def render_character_library(catalog: dict[str, Any]) -> str:
    lines = ["# 角色信息库", "", "仅整理剧情事实；角色造型与审美方案由用户决定。", ""]
    for item in catalog["characters"]:
        lines.extend([f"## {_character_title(item)}", "", f"**角色编号**：{item['character_id']}", ""])
        if item["story_role_zh"]:
            lines.extend([f"**剧情定位**：{item['story_role_zh']}", ""])
        lines.extend(["| 参数 | 已确认信息 | 原文位置 |", "|---|---|---|"])
        for field in CHARACTER_FIELDS:
            fact = item["fields"][field]
            if fact["status"] == "unknown":
                value, sources = "待确认", "—"
            else:
                value = " / ".join(entry["value_zh"] for entry in fact["values"])
                sources = "、".join(unique(source["chapter_id"] for entry in fact["values"] for source in entry["sources"])) or "用户确认"
                if fact["status"] == "conflict":
                    value += "（存在冲突）"
            lines.append(f"| {FIELD_ZH[field]} | {table_cell(value)} | {table_cell(sources)} |")
        lines.extend(["", "| 出场章节 | 出场场景（原文位置） | 出场方式 | 情节概括 |", "|---|---|---|---|"])
        for app in item["appearances"]:
            lines.append(f"| {app['chapter_id']} | {app['scene_name_zh']}（{app['scene_id']}） | {app['presence']} | {table_cell(app['summary_zh'])} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_location_library(catalog: dict[str, Any]) -> str:
    lines = ["# 场景信息库", "", "只记录原文明示的空间事实，不自动决定建筑风格、色彩、光线或构图。", ""]
    for item in catalog["locations"]:
        title = f"{item['name_zh']} / {item['name_en']}" if item.get("name_en") else item["name_zh"]
        lines.extend([f"## {title}", "", f"**场景编号**：{item['location_id']}", "", "| 项目 | 信息 | 原文位置 |", "|---|---|---|"])
        lines.append(f"| 归属地点 | {table_cell(item['parent_zh'])} | — |")
        lines.append(f"| 内外景 | {table_cell(item['int_ext'])} | — |")
        for field, fact in item["fields"].items():
            value = "待确认" if fact["status"] == "unknown" else " / ".join(entry["value_zh"] for entry in fact["values"])
            sources = "—" if fact["status"] == "unknown" else "、".join(unique(source["chapter_id"] for entry in fact["values"] for source in entry["sources"]))
            lines.append(f"| {FIELD_ZH[field]} | {table_cell(value)} | {table_cell(sources)} |")
        lines.extend(["", "| 章节 | Scene | 剧情用途或明确状态 |", "|---|---|---|"])
        for app in item["appearances"]:
            lines.append(f"| {app['chapter_id']} | {app['scene_name_zh']}（{app['scene_id']}） | {table_cell(app['summary_zh'])} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_appearance_matrix(catalog: dict[str, Any], chapter_ids: list[str]) -> str:
    lines = ["# 全角色章节出镜表", "", "实际出镜、回忆或梦境会标记；仅被提及不计作出镜。", ""]
    header = "| 角色 | " + " | ".join(chapter_ids) + " |"
    lines.extend([header, "|---|" + "---|" * len(chapter_ids)])
    for item in catalog["characters"]:
        by_chapter: dict[str, list[str]] = defaultdict(list)
        for app in item["appearances"]:
            if app["presence"] != "mentioned":
                by_chapter[app["chapter_id"]].append(app["presence"])
        cells = []
        for chapter_id in chapter_ids:
            values = by_chapter.get(chapter_id, [])
            cells.append("●" if "actual" in values else "回忆" if "memory" in values else "梦境" if "dream" in values else "")
        lines.append(f"| {_character_title(item)} | " + " | ".join(cells) + " |")
    return "\n".join(lines).rstrip() + "\n"


def render_major_appearances(catalog: dict[str, Any]) -> str:
    lines = ["# 主要角色场景统计", ""]
    major = [item for item in catalog["characters"] if item["importance"] == "major"]
    if not major:
        lines.extend(["当前尚无已识别的主要角色。", ""])
    for item in major:
        identity = item["fields"]["identity"]
        basic = "待确认" if identity["status"] == "unknown" else "；".join(value["value_zh"] for value in identity["values"])
        lines.extend([f"## {_character_title(item)}", "", f"**基本信息**：{basic}", "", "| 出场章节 | 出场场景（原文位置） | 时间 | 备注 |", "|---|---|---|---|"])
        for app in item["appearances"]:
            if app["presence"] != "mentioned":
                time = {"DAY": "日", "NIGHT": "夜", "DAWN": "黎明", "DUSK": "黄昏", "CONTINUOUS": "连续", "UNKNOWN": "待确认"}[app["time"]]
                place = {"INT": "内", "EXT": "外", "MIXED": "内外", "UNKNOWN": "待确认"}[app["int_ext"]]
                lines.append(f"| {app['chapter_id']} | {app['scene_name_zh']}（{app['scene_id']}） | {time}·{place} | {table_cell(app['summary_zh'])} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_assets(catalog: dict[str, Any]) -> str:
    type_zh = {"character": "角色", "location": "场景", "costume": "服装", "prop": "道具"}
    lines = ["# 全文美术资产候选清单", "", "本表只列出可能需要设计的资产。是否制作、合并、拆分或排除，均由用户决定。", "", "| 编号 | 类型 | 候选资产 | 剧情明确变体 | 首次出现 | 主要出现位置 | 用户决定 |", "|---|---|---|---|---|---|---|"]
    for item in catalog["assets"]:
        if item.get("merged_into"):
            continue
        name = f"{item['name_zh']} / {item['name_en']}" if item.get("name_en") else item["name_zh"]
        lines.append(f"| {item['asset_id']} | {type_zh[item['type']]} | {table_cell(name)} | {table_cell(item['variant_zh'])} | {item['first_appearance']} | {table_cell('、'.join(item['scene_ids']))} | {ASSET_STATUS_ZH[item['status']]} |")
    return "\n".join(lines).rstrip() + "\n"


def evidence_lookup(project_dir: Path, chapter_ids: Iterable[str]) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for chapter_id in chapter_ids:
        record, _, _ = load_current_analysis(project_dir, chapter_id)
        for block in record["blocks"]:
            lookup[block["block_id"]] = {"text_en": block["text_en"], "text_zh": block["text_zh"]}
    return lookup


def render_issues(catalog: dict[str, Any], evidence: dict[str, dict[str, str]] | None = None) -> str:
    evidence = evidence or {}
    lines = ["# 待确认问题汇总", "", "请按问题编号回复；章节中的英文线索与中文参考见对应验收文件。", ""]
    if not catalog["pending_issues"]:
        lines.extend(["当前没有待确认问题。", ""])
    for issue in catalog["pending_issues"]:
        lines.extend(["---", "", f"## {issue['issue_id']}", "", f"**位置**：{issue['scope_id']}", "", f"**需要确认**：{issue['question_zh']}", ""])
        refs = list(issue.get("evidence_block_ids", []))
        for source in issue.get("evidence_refs", []):
            refs.extend(source.get("block_ids", []))
        refs = unique(refs)
        if refs:
            lines.extend(["**原文线索与中文参考**：", ""])
            for block_id in refs:
                block = evidence.get(block_id)
                if not block:
                    lines.extend([f"- {block_id}（请查看对应章节验收文件）", ""])
                    continue
                lines.extend([f"- {block_id}", "", *[f"> {line}" if line else ">" for line in block["text_en"].split("\n")], "", "  中文参考：", "", *[f"> {line}" if line else ">" for line in block["text_zh"].split("\n")], ""])
        lines.extend(["**用户答复**：", "", "（请填写）", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_catalogs(project_dir: Path) -> tuple[dict[str, Any], dict[str, int]]:
    project_dir = project_dir.resolve()
    catalog_path = project_dir / "data" / "catalogs" / "catalog.json"
    previous = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {}
    cache, reused, changed = update_observation_cache(project_dir)
    observations = [cache["chapters"][key] for key in sorted(cache["chapters"], key=lambda value: int(value[1:]))]
    entities, entity_events = _build_entities(project_dir, observations, previous)
    char_index, ambiguous_char = _alias_index(entities["characters"], "character_id", ["canonical_name_en", "name_zh", "aliases_en", "aliases_zh"])
    loc_index, ambiguous_loc = _alias_index(entities["locations"], "location_id", ["name_en", "name_zh", "aliases_en", "aliases_zh"])
    catalog = {
        "schema_version": "3.0.0", "chapter_inputs": cache["chapter_inputs"],
        **entities,
        "character_alias_index": char_index, "ambiguous_character_aliases": ambiguous_char,
        "location_alias_index": loc_index, "ambiguous_location_aliases": ambiguous_loc,
        "separate_entity_pairs": [
            {"entity_type": event["entity_type"], "ids": sorted([str(event["source_id"]), str(event["target_id"])])}
            for event in entity_events if event.get("operation") == "separate"
        ],
    }
    assets, asset_redirects = _build_assets(project_dir, observations, catalog, previous)
    catalog["assets"] = assets
    catalog["asset_redirects"] = asset_redirects
    catalog["pending_issues"] = _issue_summary(observations, catalog)
    write_json(catalog_path, catalog)
    deliverables = project_dir / "deliverables"
    deliverables.mkdir(parents=True, exist_ok=True)
    chapter_ids = sorted(cache["chapters"], key=lambda value: int(value[1:]))
    outputs = {
        "角色信息库.md": render_character_library(catalog),
        "场景信息库.md": render_location_library(catalog),
        "全角色章节出镜表.md": render_appearance_matrix(catalog, chapter_ids),
        "主要角色场景统计.md": render_major_appearances(catalog),
        "全文美术资产候选清单.md": render_assets(catalog),
        "待确认问题汇总.md": render_issues(catalog, evidence_lookup(project_dir, chapter_ids)),
    }
    for filename, content in outputs.items():
        (deliverables / filename).write_text(content, encoding="utf-8", newline="\n")
    return catalog, {"reused_chapters": reused, "updated_chapters": changed}


def main() -> int:
    parser = argparse.ArgumentParser(description="增量维护全剧角色、场景、出镜与资产候选数据库")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    try:
        catalog, metrics = build_catalogs(args.project_dir)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"CATALOG BUILD FAILED: {exc}")
        return 1
    print(f"CATALOG BUILD PASSED: {len(catalog['characters'])} characters, {len(catalog['locations'])} locations, {len(catalog['assets'])} asset candidates")
    print(f"CHAPTER CACHE: {metrics['reused_chapters']} reused, {metrics['updated_chapters']} updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
