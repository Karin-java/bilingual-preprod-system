#!/usr/bin/env python3
"""Materialize cross-chapter character profiles without rereading the source book."""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

from build_registries import atomic_write, build_registries, json_bytes, load_analyses, normalize_character_name, resolve_character_id, sha256
from validate_registries import validate_registries

REQUIRED_FIELDS = [
    "age", "gender", "race", "identity", "appearance", "hair", "body_type",
    "special_marks", "clothing", "personality", "story_role",
]
OBSERVATION_FIELDS = set(REQUIRED_FIELDS) | {"basic_info_summary"}
FIELD_LABELS = {
    "age": "年龄", "gender": "性别", "race": "种族", "identity": "身份",
    "appearance": "外貌", "hair": "发型", "body_type": "体型",
    "special_marks": "特殊标记", "clothing": "服装", "personality": "性格",
    "story_role": "剧情定位", "basic_info_summary": "基本信息摘要",
}
STATUS_LABELS = {
    "explicit": "原文明确", "inferred": "据原文推断", "user_confirmed": "用户已确认",
    "conflict": "证据冲突，待确认", "unknown": "待确认",
}
STATUS_RANK = {"inferred": 0, "explicit": 1, "user_confirmed": 2}
MULTI_VALUE_FIELDS = {"identity", "appearance", "hair", "body_type", "special_marks", "clothing", "personality"}
EVENT_RE = re.compile(r"^PROFILE-([0-9]{6})$")
EVENT_PATH = "data/profiles/profile-decisions.jsonl"


def chapter_number(value: str) -> int:
    return int(value[1:])


def character_number(value: str) -> int:
    return int(value.split("-")[1])


def append_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def load_events(project_dir: Path) -> tuple[list[dict[str, Any]], bytes]:
    path = project_dir / EVENT_PATH
    if not path.exists():
        return [], b""
    data = path.read_bytes()
    events: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    retracted_targets: set[str] = set()
    last = 0
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid profile event at line {line_number}: {exc}") from exc
        match = EVENT_RE.fullmatch(str(event.get("event_id")))
        required = {
            "schema_version", "event_id", "recorded_at", "actor", "operation", "character_ref",
            "field", "value_zh", "normalized_value", "note", "target_event_id",
        }
        if not isinstance(event, dict) or set(event) != required or event.get("schema_version") != "1.0.0" or not match:
            raise ValueError(f"invalid profile event fields or ID at line {line_number}")
        number = int(match.group(1))
        if number <= last:
            raise ValueError("profile event IDs must be increasing")
        last = number
        operation = event.get("operation")
        if operation == "set_field":
            if not isinstance(event.get("character_ref"), str) or not event["character_ref"].strip():
                raise ValueError(f"{event['event_id']}: character_ref is required")
            if event.get("field") not in OBSERVATION_FIELDS:
                raise ValueError(f"{event['event_id']}: unsupported profile field")
            if not isinstance(event.get("value_zh"), str) or not event["value_zh"].strip():
                raise ValueError(f"{event['event_id']}: value_zh is required")
            if not isinstance(event.get("normalized_value"), str) or not event["normalized_value"].strip():
                raise ValueError(f"{event['event_id']}: normalized_value is required")
            if event.get("target_event_id") is not None:
                raise ValueError(f"{event['event_id']}: set_field cannot target another event")
        elif operation == "retract_event":
            target = event.get("target_event_id")
            if target not in seen or seen[target]["operation"] != "set_field" or target in retracted_targets:
                raise ValueError(f"{event['event_id']}: invalid retraction target")
            retracted_targets.add(target)
            if any(event.get(field) is not None for field in ("character_ref", "field", "value_zh", "normalized_value")):
                raise ValueError(f"{event['event_id']}: retraction payload must be null")
        else:
            raise ValueError(f"{event['event_id']}: unsupported operation")
        actor = event.get("actor")
        if not isinstance(actor, dict) or set(actor) != {"type", "label"} or actor.get("type") not in {"user", "agent"} or not actor.get("label"):
            raise ValueError(f"{event['event_id']}: invalid actor")
        if not isinstance(event.get("note"), str) or not event["note"].strip():
            raise ValueError(f"{event['event_id']}: note is required")
        events.append(event)
        seen[event["event_id"]] = event
    return events, data


def active_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    retracted = {event["target_event_id"] for event in events if event["operation"] == "retract_event"}
    active = [event for event in events if event["operation"] == "set_field" and event["event_id"] not in retracted]
    return active, sorted(retracted, key=lambda item: int(item.split("-")[1]))


def translations_by_unit(analyses: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for analysis in analyses:
        for unit in analysis["unit_analyses"]:
            translations = [
                segment.get("translation", {}).get("text_zh", "").strip()
                for segment in unit.get("segments", [])
                if segment.get("translation", {}).get("text_zh", "").strip()
            ]
            if translations:
                result[unit["unit_id"]] = " ".join(translations)
    return result


def observation_character_id(observation: dict[str, Any], registry: dict[str, Any]) -> str | None:
    matched = observation.get("matched_character_id")
    if matched:
        return resolve_character_id(registry.get("character_id_redirects", {}), matched)
    return registry["character_key_index"].get(observation["entity_key"])


def chapter_fact(observation: dict[str, Any]) -> dict[str, Any]:
    chapter_id = observation["profile_observation_id"].split("-")[1]
    return {
        "fact_id": observation["profile_observation_id"],
        "origin": "chapter_observation",
        "field": observation["field"],
        "value_zh": observation["value_zh"],
        "normalized_value": observation["normalized_value"],
        "status": observation["status"],
        "confidence": observation["confidence"],
        "chapter_ids": [chapter_id],
        "source_unit_ids": copy.deepcopy(observation["source_unit_ids"]),
        "evidence": copy.deepcopy(observation["evidence"]),
        "note_zh": observation["note_zh"],
    }


def merge_equal_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        grouped.setdefault(fact["normalized_value"], []).append(fact)
    merged: list[dict[str, Any]] = []
    for normalized, rows in grouped.items():
        first = copy.deepcopy(rows[0])
        first["fact_id"] = rows[0]["fact_id"]
        first["status"] = max((row["status"] for row in rows), key=lambda value: STATUS_RANK[value])
        first["confidence"] = max(row["confidence"] for row in rows)
        for row in rows[1:]:
            append_unique(first["chapter_ids"], row["chapter_ids"])
            append_unique(first["source_unit_ids"], row["source_unit_ids"])
            existing = {(item["source_unit_id"], item["quote"]) for item in first["evidence"]}
            for evidence in row["evidence"]:
                if (evidence["source_unit_id"], evidence["quote"]) not in existing and len(first["evidence"]) < 8:
                    first["evidence"].append(copy.deepcopy(evidence))
            if row["note_zh"] and row["note_zh"] not in first["note_zh"]:
                first["note_zh"] = "；".join(value for value in (first["note_zh"], row["note_zh"]) if value)
        first["normalized_value"] = normalized
        merged.append(first)
    return merged


def materialize_field(field_name: str, facts: list[dict[str, Any]], decision: dict[str, Any] | None) -> dict[str, Any]:
    merged = merge_equal_facts(facts)
    if decision:
        decision_fact = {
            "fact_id": decision["event_id"], "origin": "profile_decision", "field": decision["field"],
            "value_zh": decision["value_zh"], "normalized_value": decision["normalized_value"],
            "status": "user_confirmed", "confidence": 1, "chapter_ids": [], "source_unit_ids": [],
            "evidence": [], "note_zh": decision["note"],
        }
        return {"value_zh": decision["value_zh"], "status": "user_confirmed", "fact_ids": [decision["event_id"]], "facts": merged + [decision_fact]}
    if not merged:
        return {"value_zh": None, "status": "unknown", "fact_ids": [], "facts": []}
    if len(merged) > 1 and field_name not in MULTI_VALUE_FIELDS:
        return {
            "value_zh": " / ".join(item["value_zh"] for item in merged), "status": "conflict",
            "fact_ids": [item["fact_id"] for item in merged], "facts": merged,
        }
    if len(merged) > 1:
        return {
            "value_zh": "；".join(item["value_zh"] for item in merged),
            "status": min((item["status"] for item in merged), key=lambda value: STATUS_RANK[value]),
            "fact_ids": [item["fact_id"] for item in merged], "facts": merged,
        }
    fact = merged[0]
    return {"value_zh": fact["value_zh"], "status": fact["status"], "fact_ids": [fact["fact_id"]], "facts": merged}


def compose_basic_info(character: dict[str, Any], fields: dict[str, dict[str, Any]], decision: dict[str, Any] | None) -> str:
    if decision:
        return decision["value_zh"]
    parts: list[str] = []
    for field in ("story_role", "race", "identity"):
        item = fields[field]
        if item["status"] not in {"unknown", "conflict"} and item["value_zh"]:
            parts.append(item["value_zh"])
    if not parts:
        importance = "主要角色候选" if character["importance"] == "candidate_major" else "角色信息待完整剧本复盘"
        return importance
    if any(fields[field]["status"] != "user_confirmed" for field in ("story_role", "race", "identity") if fields[field]["value_zh"]):
        parts.append("部分信息待完整剧本复盘确认")
    return "，".join(dict.fromkeys(parts))


def clue_rows(field: dict[str, Any], translations: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for fact in field["facts"]:
        for evidence in fact["evidence"]:
            unit_id = evidence["source_unit_id"]
            row = {"source_unit_id": unit_id, "quote_en": evidence["quote"], "translation_zh": translations.get(unit_id, "译文待补充")}
            if row not in rows:
                rows.append(row)
            if len(rows) >= 4:
                return rows
    return rows


def build_profile_data(project_dir: Path) -> tuple[dict[str, Any], str, str]:
    project_dir = project_dir.resolve()
    registry_path = project_dir / "data" / "registries" / "entities.json"
    if not registry_path.exists():
        build_registries(project_dir)
    errors = validate_registries(project_dir)
    if errors:
        raise ValueError("entity registry validation failed: " + "; ".join(errors))
    registry_data = registry_path.read_bytes()
    registry = json.loads(registry_data.decode("utf-8"))
    loaded = load_analyses(project_dir)
    analyses = [item[0] for item in loaded]
    translations = translations_by_unit(analyses)
    events, event_data = load_events(project_dir)
    active, retracted = active_events(events)

    characters = {item["character_id"]: item for item in registry["characters"]}
    facts: dict[tuple[str, str], list[dict[str, Any]]] = {}
    unresolved_observations: list[str] = []
    for analysis in analyses:
        for observation in analysis.get("profile_observations", []):
            character_id = observation_character_id(observation, registry)
            if not character_id or character_id not in characters:
                unresolved_observations.append(observation["profile_observation_id"])
                continue
            facts.setdefault((character_id, observation["field"]), []).append(chapter_fact(observation))

    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    for event in active:
        reference = event["character_ref"]
        if re.fullmatch(r"CHAR-[0-9]{4}", reference):
            character_id = resolve_character_id(registry.get("character_id_redirects", {}), reference)
        else:
            matches = registry["character_name_index"].get(normalize_character_name(reference), [])
            if len(matches) != 1:
                raise ValueError(f"{event['event_id']}: character reference is not uniquely resolvable: {reference}")
            character_id = matches[0]
        if character_id not in characters:
            raise ValueError(f"{event['event_id']}: character is not registered")
        decisions[(character_id, event["field"])] = event

    profiles: list[dict[str, Any]] = []
    recap_tasks: list[dict[str, Any]] = []
    for character_id in sorted(characters, key=character_number):
        character = characters[character_id]
        fields = {
            field: materialize_field(field, facts.get((character_id, field), []), decisions.get((character_id, field)))
            for field in REQUIRED_FIELDS
        }
        profile = {
            "schema_version": "2.0.0", "character_id": character_id,
            "canonical_name": character["canonical_name"], "chinese_name": character["chinese_name"],
            "basic_info_summary_zh": compose_basic_info(character, fields, decisions.get((character_id, "basic_info_summary"))),
            "fields": fields,
        }
        profiles.append(profile)
        pending_fields: list[dict[str, Any]] = []
        for field_name, field in fields.items():
            if field["status"] not in {"unknown", "conflict"} and not (field_name == "story_role" and field["status"] == "inferred"):
                continue
            reason = "inference_needs_confirmation" if field["status"] == "inferred" else field["status"]
            pending_fields.append({"field": field_name, "reason": reason, "current_value_zh": field["value_zh"]})
        if pending_fields:
            context_clues: list[dict[str, str]] = []
            for field in fields.values():
                append_unique(context_clues, clue_rows(field, translations))
                if len(context_clues) >= 8:
                    context_clues = context_clues[:8]
                    break
            recap_tasks.append({
                "task_id": f"RECAP-{len(recap_tasks) + 1:04d}", "character_id": character_id,
                "fields": pending_fields, "context_clues": context_clues,
            })

    bundle = {
        "schema_version": "2.0.0", "profile_version": "character-profile-v1",
        "registry_input": {"path": registry_path.relative_to(project_dir).as_posix(), "sha256": sha256(registry_data)},
        "analysis_inputs": [
            {"chapter_id": analysis["chapter_id"], "path": path.relative_to(project_dir).as_posix(), "sha256": sha256(data)}
            for analysis, path, data in loaded
        ],
        "decision_log": {
            "path": EVENT_PATH, "sha256": sha256(event_data),
            "active_event_ids": [event["event_id"] for event in active], "retracted_event_ids": retracted,
        },
        "required_fields": REQUIRED_FIELDS, "profiles": profiles, "recap_tasks": recap_tasks,
        "unresolved_profile_observation_ids": unresolved_observations,
    }
    # Keep unresolved observations visible to validators without changing the public bundle schema.
    if not unresolved_observations:
        bundle.pop("unresolved_profile_observation_ids")
    return bundle, render_profiles(bundle), render_recap(bundle)


def render_profiles(bundle: dict[str, Any]) -> str:
    lines = ["# 角色基础信息档案", "", "本档案按角色永久编号跨章累积；没有原文依据的项目统一保留为“待确认”。", ""]
    for profile in bundle["profiles"]:
        name = (f"{profile['chinese_name']} / " if profile["chinese_name"] else "") + profile["canonical_name"]
        lines.extend([f"## {name}", "", f"**基本信息**：{profile['basic_info_summary_zh']}", "", "| 参数 | 当前结论 | 状态 | 依据章节 |", "|---|---|---|---|"])
        for field_name in REQUIRED_FIELDS:
            field = profile["fields"][field_name]
            chapters: list[str] = []
            for fact in field["facts"]:
                append_unique(chapters, fact["chapter_ids"])
            lines.append(f"| {FIELD_LABELS[field_name]} | {field['value_zh'] or '待确认'} | {STATUS_LABELS[field['status']]} | {'、'.join(chapters) or '—'} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_recap(bundle: dict[str, Any]) -> str:
    profiles = {item["character_id"]: item for item in bundle["profiles"]}
    lines = ["# 全书角色资料复盘清单", "", "这里只列出仍需处理的项目。复盘确认会追加到全局记录，不回写已经完成的章节。", ""]
    if not bundle["recap_tasks"]:
        lines.extend(["当前没有待复盘项目。", ""])
    for task in bundle["recap_tasks"]:
        profile = profiles[task["character_id"]]
        name = profile["chinese_name"] or profile["canonical_name"]
        lines.extend(["---", "", f"## {task['task_id']}｜{name}", "", "| 待复盘参数 | 当前结论 | 原因 |", "|---|---|---|"])
        for field in task["fields"]:
            reason = {"unknown": "当前无结论", "conflict": "证据冲突", "inference_needs_confirmation": "推断待确认"}[field["reason"]]
            lines.append(f"| {FIELD_LABELS[field['field']]} | {field['current_value_zh'] or '待确认'} | {reason} |")
        lines.append("")
        if task["context_clues"]:
            lines.extend(["**本角色已累积的原文线索与翻译**：", ""])
            for clue in task["context_clues"]:
                lines.extend([f"> 英文：{clue['quote_en']}", ">", f"> 中文：{clue['translation_zh']}", ""])
        else:
            lines.extend(["**本角色已累积的原文线索与翻译**：当前章节尚无可用于资料判断的直接线索，等待后续章节累积。", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_profiles(project_dir: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    bundle, profile_md, recap_md = build_profile_data(project_dir)
    data_path = project_dir / "data" / "profiles" / "profiles.json"
    profile_path = project_dir / "deliverables" / "角色基础信息档案.md"
    recap_path = project_dir / "work" / "recap" / "角色资料复盘清单.md"
    atomic_write(data_path, json_bytes(bundle))
    atomic_write(profile_path, profile_md.encode("utf-8"))
    atomic_write(recap_path, recap_md.encode("utf-8"))
    return data_path, profile_path, recap_path, bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="跨章累积人物资料并生成全书复盘清单")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    try:
        data_path, profile_path, recap_path, bundle = build_profiles(args.project_dir)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"PROFILE BUILD FAILED: {exc}")
        return 1
    print(f"PROFILES: {len(bundle['profiles'])}; RECAP TASKS: {len(bundle['recap_tasks'])}")
    print(data_path)
    print(profile_path)
    print(recap_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
