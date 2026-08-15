#!/usr/bin/env python3
"""Prepare one compact, self-contained V3 chapter task."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from preprod import find_chapter, load_project, project_relative, sha256, write_json
from validate_ingest import validate_project

RULES = [
    "blocks 的 text_en 按顺序拼接必须逐字符等于 source_text；禁止改写、校正或遗漏英文原文。",
    "每个英文块只出现一次；中文翻译放在同一块 text_zh，证据只引用 block_id，不复制原文。",
    "区分 heading、narration、action、dialogue、thought、separator；dialogue 必须填写 speaker_ref，无法确认时写 unknown 并建立 issue。",
    "地点、内外景、时间线或现实层级改变时新建 Scene；同一时空内目标、冲突、信息或重大情绪变化时新建 Beat。",
    "场景名必须具体到可制片空间，例如‘王宫·维克多的书房’，不能使用‘日常卧室’等泛称；不明确项用 UNKNOWN 并建立 issue。",
    "只登记有姓名、有台词、稳定身份或高频群众角色；只被提及时 appearance.presence=mentioned，不算实际出镜。",
    "人物与地点事实只记录原文明示内容；不要补写审美、色彩、镜头、光线、建筑风格或构图。",
    "人物/地点可复用 context 中已有 ID；尚未确认同一身份时保留两个 ref，通过 identity_links 记录 same 或 possible_same。",
    "asset_candidates 只列本章明确需要单独设计的服装、道具或视觉状态变体；普通角色与地点资产由系统自动汇总。",
    "所有无法确认但会影响制片或归档的信息进入 issues；不得为了填满字段而猜测。",
]

OUTPUT_KEYS = [
    "schema_version", "chapter_id", "source_sha256", "input_sha256", "title_en", "title_zh", "pov",
    "blocks", "front_matter_block_ids", "scenes", "characters", "character_facts", "locations",
    "appearances", "identity_links", "asset_candidates", "issues",
]


def _names(record: dict[str, Any], *fields: str) -> list[str]:
    values: list[str] = []
    for field in fields:
        value = record.get(field)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return [value for value in values if len(value.strip()) > 1]


def compact_context(project_dir: Path, source_text: str) -> dict[str, Any]:
    path = project_dir / "data" / "catalogs" / "catalog.json"
    if not path.exists():
        return {"characters": [], "locations": []}
    catalog = json.loads(path.read_text(encoding="utf-8"))
    folded = source_text.casefold()
    characters = []
    for item in catalog.get("characters", []):
        names = _names(item, "canonical_name_en", "name_zh", "aliases_en", "aliases_zh")
        if item.get("importance") == "major" or any(name.casefold() in folded for name in names):
            characters.append({
                "id": item["character_id"], "name_en": item["canonical_name_en"],
                "name_zh": item.get("name_zh"), "aliases": names,
            })
    locations = []
    for item in catalog.get("locations", []):
        names = _names(item, "name_en", "name_zh", "aliases_en", "aliases_zh")
        if any(name.casefold() in folded for name in names):
            locations.append({
                "id": item["location_id"], "name_en": item.get("name_en"),
                "name_zh": item["name_zh"], "aliases": names,
            })
    return {"characters": characters[:24], "locations": locations[:24]}


def prepare_packet(project_dir: Path, chapter_id: str, replace: bool = False) -> tuple[Path, str]:
    project_dir = project_dir.resolve()
    errors = validate_project(project_dir)
    if errors:
        raise ValueError("ingest validation failed: " + "; ".join(errors))
    project, manifest = load_project(project_dir)
    chapter = find_chapter(manifest, chapter_id)
    source_path = project_dir / chapter["source_path"]
    source_text = source_path.read_text(encoding="utf-8")
    known_entities = compact_context(project_dir, source_text)
    packet = {
        "packet_version": "3.0.0",
        "project_id": project["project_id"],
        "chapter_id": chapter_id,
        "source_sha256": chapter["sha256"],
        "source_text": source_text,
        "known_entities": known_entities,
        "rules": RULES,
        "output_contract": {
            "schema": "schemas/chapter.schema.json",
            "required_root_keys": OUTPUT_KEYS,
            "output_path": f"data/chapters/{chapter_id.lower()}.json",
            "id_examples": {
                "block": f"{chapter_id}-B0001", "scene": f"{chapter_id}-S001",
                "beat": f"{chapter_id}-S001-B001", "issue": f"ISS-{chapter_id}-0001",
            },
        },
    }
    target = project_dir / "work" / "chapters" / f"{chapter_id.lower()}.packet.json"
    data = (json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    source_size = len(source_text.encode("utf-8"))
    while len(data) - source_size > 8192 and (known_entities["characters"] or known_entities["locations"]):
        target = "locations" if len(known_entities["locations"]) >= len(known_entities["characters"]) else "characters"
        known_entities[target].pop()
        data = (json.dumps(packet, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) - source_size > 8192:
        raise ValueError("fixed chapter rules exceed the 8 KB overhead budget")
    if target.exists() and target.read_bytes() != data and not replace:
        raise ValueError(f"packet differs and already exists: {target}; use --replace after review")
    write_json(target, packet, compact=True)
    return target, sha256(data)


def main() -> int:
    parser = argparse.ArgumentParser(description="准备一个轻量、单章语义任务")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        path, packet_hash = prepare_packet(args.project_dir, args.chapter, args.replace)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"PACKET PREPARATION FAILED: {exc}")
        return 1
    print(f"PACKET PREPARATION PASSED: {project_relative(args.project_dir.resolve(), path)}")
    print(f"PACKET SHA256: {packet_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
