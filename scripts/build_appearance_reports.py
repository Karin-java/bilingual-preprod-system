#!/usr/bin/env python3
"""Build canonical appearance facts and the two user-facing Markdown reports."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from build_registries import atomic_write, build_registries, json_bytes, load_analyses, sha256
from build_profiles import build_profile_data, build_profiles
from validate_registries import validate_registries

ON_SCREEN_TYPES = {"physical", "dream", "flashback"}
TYPE_ORDER = {"physical": 0, "dream": 1, "flashback": 2, "mentioned": 3, "unknown": 4}
TYPE_LABELS = {"physical": "现实出镜", "dream": "梦境出镜", "flashback": "回忆出镜", "mentioned": "被提及", "unknown": "待确认"}
ROW_STATUS_LABELS = {
    "on_screen": "实际出镜",
    "on_screen_and_mentioned": "出镜并被提及",
    "mentioned_only": "仅被提及",
    "unknown": "待确认",
}
IMPORTANCE_LABELS = {"major": "主要角色", "candidate_major": "主要角色候选"}
TIME_LABELS = {
    "day": "日", "night": "夜", "dawn": "黎明", "morning": "晨", "noon": "午",
    "afternoon": "午后", "dusk": "黄昏", "continuous": "连续", "unknown": "待确认",
}
INT_EXT_LABELS = {"INT": "内", "EXT": "外", "INT_EXT": "内外", "unknown": "待确认"}


def appearance_id(observation_id: str) -> str:
    _, chapter_id, ordinal = observation_id.split("-")
    return f"APP-{chapter_id}-{ordinal}"


def chapter_number(chapter_id: str) -> int:
    return int(chapter_id[1:])


def character_number(character_id: str) -> int:
    return int(character_id.split("-")[1])


def scene_number(scene_id: str) -> int:
    return int(scene_id.split("-S")[1])


def scene_label(scene: dict[str, Any]) -> str:
    location = scene["location"]
    name = location.get("standardized_name") or "地点待确认"
    if location["status"] in {"unknown", "conflict"}:
        name += "（地点待确认）" if name != "地点待确认" else ""
    return f"场 {scene['ordinal']}｜{name}"


def scene_time_label(scene: dict[str, Any]) -> str:
    return f"{TIME_LABELS[scene['time_of_day']['value']]}·{INT_EXT_LABELS[scene['int_ext']['value']]}"


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def build_appearance_data(project_dir: Path) -> tuple[dict[str, Any], str, str]:
    project_dir = project_dir.resolve()
    registry_path = project_dir / "data" / "registries" / "entities.json"
    if not registry_path.exists():
        build_registries(project_dir)
    registry_errors = validate_registries(project_dir)
    if registry_errors:
        raise ValueError("entity registry validation failed: " + "; ".join(registry_errors))
    registry_data = registry_path.read_bytes()
    registry = json.loads(registry_data.decode("utf-8"))
    profile_path = project_dir / "data" / "profiles" / "profiles.json"
    if not profile_path.exists():
        build_profiles(project_dir)
    profile_data = profile_path.read_bytes()
    profile_bundle = json.loads(profile_data.decode("utf-8"))
    expected_profiles, _, _ = build_profile_data(project_dir)
    if profile_bundle != expected_profiles:
        raise ValueError("character profiles are stale; rebuild profiles before appearance reports")
    profiles = {item["character_id"]: item for item in profile_bundle["profiles"]}
    loaded = load_analyses(project_dir)
    analyses = [item[0] for item in loaded]
    character_by_id = {item["character_id"]: item for item in registry["characters"]}
    scene_by_id = {scene["scene_id"]: scene for analysis in analyses for scene in analysis["scenes"]}

    appearances: list[dict[str, Any]] = []
    unregistered: list[str] = []
    for analysis in analyses:
        for observation in analysis["character_observations"]:
            character_id = registry["character_key_index"].get(observation["entity_key"])
            if not character_id or character_id not in character_by_id:
                unregistered.append(observation["observation_id"])
                continue
            appearances.append({
                "schema_version": "2.0.0",
                "appearance_id": appearance_id(observation["observation_id"]),
                "source_observation_id": observation["observation_id"],
                "character_id": character_id,
                "chapter_id": analysis["chapter_id"],
                "scene_id": observation["scene_id"],
                "appearance_type": observation["presence_type"],
                "has_dialogue": observation["has_dialogue"],
                "speaker_segment_ids": copy.deepcopy(observation["speaker_segment_ids"]),
                "summary_zh": observation.get("summary_zh") or (scene_by_id[observation["scene_id"]]["summary_zh"] if observation["scene_id"] else f"本章提及{observation['chinese_label'] or observation['canonical_label']}。"),
                "source_unit_ids": copy.deepcopy(observation["source_unit_ids"]),
                "status": observation["status"],
                "confidence": observation["confidence"],
                "evidence": copy.deepcopy(observation["evidence"]),
            })
    appearances.sort(key=lambda item: (chapter_number(item["chapter_id"]), int(item["appearance_id"].split("-")[-1])))

    grouped_chapters: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in appearances:
        grouped_chapters.setdefault((item["character_id"], item["chapter_id"]), []).append(item)
    chapter_rows: list[dict[str, Any]] = []
    for character_id, chapter_id in sorted(grouped_chapters, key=lambda value: (character_number(value[0]), chapter_number(value[1]))):
        rows = grouped_chapters[(character_id, chapter_id)]
        on_screen = {item["scene_id"] for item in rows if item["appearance_type"] in ON_SCREEN_TYPES and item["scene_id"]}
        mentioned = {item["scene_id"] for item in rows if item["appearance_type"] == "mentioned" and item["scene_id"]}
        unknown = {item["scene_id"] for item in rows if item["appearance_type"] == "unknown" and item["scene_id"]}
        dialogue = {item["scene_id"] for item in rows if item["has_dialogue"] and item["scene_id"]}
        if on_screen and mentioned:
            status = "on_screen_and_mentioned"
        elif on_screen:
            status = "on_screen"
        elif mentioned:
            status = "mentioned_only"
        else:
            status = "unknown"
        chapter_rows.append({
            "character_id": character_id,
            "chapter_id": chapter_id,
            "appearance_ids": [item["appearance_id"] for item in rows],
            "status": status,
            "appearance_types": sorted({item["appearance_type"] for item in rows}, key=lambda value: TYPE_ORDER[value]),
            "on_screen_scene_ids": sorted(on_screen, key=scene_number),
            "dialogue_scene_ids": sorted(dialogue, key=scene_number),
            "mentioned_only_scene_ids": sorted(mentioned - on_screen, key=scene_number),
            "unknown_scene_ids": sorted(unknown, key=scene_number),
        })

    major_ids = {item["character_id"] for item in registry["characters"] if item["importance"] in {"major", "candidate_major"}}
    grouped_scenes: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in appearances:
        if item["character_id"] in major_ids and item["scene_id"] and item["appearance_type"] in ON_SCREEN_TYPES:
            grouped_scenes.setdefault((item["character_id"], item["scene_id"]), []).append(item)
    major_scenes: list[dict[str, Any]] = []
    for character_id, scene_id in sorted(grouped_scenes, key=lambda value: (character_number(value[0]), chapter_number(value[1].split("-")[0]), scene_number(value[1]))):
        rows = grouped_scenes[(character_id, scene_id)]
        scene = scene_by_id[scene_id]
        major_scenes.append({
            "character_id": character_id,
            "chapter_id": scene_id.split("-")[0],
            "scene_id": scene_id,
            "scene_ordinal": scene["ordinal"],
            "appearance_ids": [item["appearance_id"] for item in rows],
            "appearance_types": sorted({item["appearance_type"] for item in rows}, key=lambda value: TYPE_ORDER[value]),
            "has_dialogue": any(item["has_dialogue"] for item in rows),
            "location_name": scene["location"].get("standardized_name"),
            "location_status": scene["location"]["status"],
            "summary_zh": "；".join(dict.fromkeys(item["summary_zh"].rstrip("。") for item in rows)) + "。",
            "source_unit_ids": copy.deepcopy(scene["source_unit_ids"]),
        })

    bundle = {
        "schema_version": "2.0.0",
        "appearance_version": "appearance-registry-v1",
        "registry_input": {"path": registry_path.relative_to(project_dir).as_posix(), "sha256": sha256(registry_data)},
        "profile_input": {"path": profile_path.relative_to(project_dir).as_posix(), "sha256": sha256(profile_data)},
        "analysis_inputs": [
            {"chapter_id": analysis["chapter_id"], "path": path.relative_to(project_dir).as_posix(), "sha256": sha256(data)}
            for analysis, path, data in loaded
        ],
        "major_selection": {
            "strategy": "registry-importance-v1",
            "included_importance": ["major", "candidate_major"],
            "provisional": any(item["importance"] == "candidate_major" for item in registry["characters"] if item["character_id"] in major_ids),
        },
        "appearances": appearances,
        "unregistered_observation_ids": unregistered,
        "chapter_rows": chapter_rows,
        "major_character_scenes": major_scenes,
    }
    return bundle, render_chapter_table(bundle, registry, scene_by_id), render_major_scenes(bundle, registry, scene_by_id, profiles)


def render_chapter_table(bundle: dict[str, Any], registry: dict[str, Any], scene_by_id: dict[str, dict[str, Any]]) -> str:
    characters = {item["character_id"]: item for item in registry["characters"]}
    chapter_ids = [item["chapter_id"] for item in bundle["analysis_inputs"]]
    rows = {(item["character_id"], item["chapter_id"]): item for item in bundle["chapter_rows"]}
    appearances: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in bundle["appearances"]:
        appearances.setdefault((item["character_id"], item["chapter_id"]), []).append(item)

    def scene_ordinals(ids: set[str]) -> str:
        return "、".join(f"场{scene_by_id[scene_id]['ordinal']}" for scene_id in sorted(ids, key=scene_number))

    def cell(character_id: str, chapter_id: str) -> str:
        row = rows.get((character_id, chapter_id))
        if row is None:
            return "—"
        items = appearances[(character_id, chapter_id)]
        parts: list[str] = []
        for appearance_type, label in (("physical", "现实"), ("dream", "梦境"), ("flashback", "回忆")):
            ids = {item["scene_id"] for item in items if item["appearance_type"] == appearance_type and item["scene_id"]}
            if ids:
                parts.append(f"{label}：{scene_ordinals(ids)}")
        dialogue = set(row["dialogue_scene_ids"])
        mentioned = set(row["mentioned_only_scene_ids"])
        unknown = set(row["unknown_scene_ids"])
        if dialogue:
            parts.append(f"台词：{scene_ordinals(dialogue)}")
        if mentioned:
            parts.append(f"仅提及：{scene_ordinals(mentioned)}")
        if unknown:
            parts.append(f"待确认：{scene_ordinals(unknown)}")
        return "；".join(parts) or ROW_STATUS_LABELS[row["status"]]

    included_character_ids = sorted({row["character_id"] for row in bundle["chapter_rows"]}, key=character_number)
    lines = [
        "# 全角色章节出镜表", "",
        "第一列为统一角色，后续每一列对应一个 P 编号章节；“实际出镜”包括现实、梦境和回忆画面，仅在台词或叙述中出现姓名时标记为“仅提及”。", "",
        "| " + " | ".join(["角色", *chapter_ids]) + " |",
        "|" + "|".join(["---", *(["---"] * len(chapter_ids))]) + "|",
    ]
    for character_id in included_character_ids:
        character = characters[character_id]
        name = character["canonical_name"] + (f" / {character['chinese_name']}" if character["chinese_name"] else "")
        lines.append("| " + " | ".join([markdown_escape(name), *(markdown_escape(cell(character_id, chapter_id)) for chapter_id in chapter_ids)]) + " |")
    if not included_character_ids:
        lines.append("| 暂无已登记角色 | " + " | ".join("—" for _ in chapter_ids) + " |")
    return "\n".join(lines).rstrip() + "\n"


def render_major_scenes(bundle: dict[str, Any], registry: dict[str, Any], scene_by_id: dict[str, dict[str, Any]], profiles: dict[str, dict[str, Any]]) -> str:
    characters = {item["character_id"]: item for item in registry["characters"]}
    by_character: dict[str, list[dict[str, Any]]] = {}
    for item in bundle["major_character_scenes"]:
        by_character.setdefault(item["character_id"], []).append(item)
    lines = ["# 主要角色出镜场景统计", ""]
    if bundle["major_selection"]["provisional"]:
        lines.extend(["当前包含尚待全书复盘确认的主要角色候选；基本信息读取当前人物档案，正式定位确认后本文件可直接重建。", ""])
    for character_id in sorted(by_character, key=character_number):
        character = characters[character_id]
        name = (f"{character['chinese_name']} / " if character["chinese_name"] else "") + character["canonical_name"]
        profile = profiles.get(character_id)
        basic_info = profile["basic_info_summary_zh"] if profile else f"{IMPORTANCE_LABELS[character['importance']]}；详细身份待完整剧本复盘补充"
        lines.extend([
            f"## {name}", "", f"**基本信息**：{basic_info}", "",
            "| 出场章节 | 出场场景（原文位置） | 时间 | 备注 |",
            "|---|---|---|---|",
        ])
        for item in by_character[character_id]:
            scene = scene_by_id[item["scene_id"]]
            types = "、".join(TYPE_LABELS[value] for value in item["appearance_types"])
            if item["has_dialogue"]:
                types += "、有台词"
            location = scene["location"].get("standardized_name") or "地点待确认"
            if scene["location"]["status"] in {"unknown", "conflict"}:
                location += "（待确认）" if location != "地点待确认" else ""
            remarks = f"{types}｜{item['summary_zh']}"
            lines.append("| " + " | ".join([
                item["chapter_id"], markdown_escape(location), scene_time_label(scene), markdown_escape(remarks),
            ]) + " |")
        lines.append("")
    if not by_character:
        lines.extend(["暂无已确认或候选的主要角色出镜场景。", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_appearance_reports(project_dir: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    project_dir = project_dir.resolve()
    build_profiles(project_dir)
    data_path = project_dir / "data" / "appearances" / "appearances.json"
    chapter_report = project_dir / "deliverables" / "全角色章节出镜表.md"
    major_report = project_dir / "deliverables" / "主要角色场景统计.md"
    bundle, chapter_markdown, major_markdown = build_appearance_data(project_dir)
    atomic_write(data_path, json_bytes(bundle))
    atomic_write(chapter_report, chapter_markdown.encode("utf-8"))
    atomic_write(major_report, major_markdown.encode("utf-8"))
    return data_path, chapter_report, major_report, bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="从统一角色登记和章节解析生成出镜事实与 Markdown 统计")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    try:
        data_path, chapter_report, major_report, bundle = build_appearance_reports(args.project_dir)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"APPEARANCE BUILD FAILED: {exc}")
        return 1
    print(f"APPEARANCE DATA: {data_path}")
    print(f"CHAPTER TABLE: {chapter_report}")
    print(f"MAJOR SCENE REPORT: {major_report}")
    print(f"APPEARANCES: {len(bundle['appearances'])}; CHAPTER ROWS: {len(bundle['chapter_rows'])}; MAJOR SCENES: {len(bundle['major_character_scenes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
