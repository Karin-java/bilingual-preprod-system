#!/usr/bin/env python3
"""Prepare a deterministic, single-chapter semantic analysis packet."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from validate_ingest import validate_project


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def registry_context(project_dir: Path, units: list[dict], chapter_id: str) -> dict:
    registry_path = project_dir / "data" / "registries" / "entities.json"
    if not registry_path.exists():
        return {"registry_version": None, "registry_sha256": None, "selection_strategy": "lexical-major-recent-v1", "total_characters": 0, "total_locations": 0, "characters": [], "locations": []}
    data = registry_path.read_bytes()
    registry = json.loads(data.decode("utf-8"))
    source = "\n".join(unit["source_text"] for unit in units).casefold()
    current_number = int(chapter_id[1:])
    prior_numbers = sorted({int(value[1:]) for item in registry["characters"] for value in item["chapter_ids"] if int(value[1:]) < current_number})
    recent_chapter = f"P{prior_numbers[-1]:02d}" if prior_numbers else None
    ignored_terms = {"i", "he", "she", "him", "her", "they", "them", "his", "their"}

    def source_matches(values: list[str]) -> bool:
        return any(value.casefold() not in ignored_terms and len(value.strip()) > 1 and value.casefold() in source for value in values)

    selected_characters = [
        item for item in registry["characters"]
        if item["importance"] == "candidate_major"
        or (recent_chapter is not None and recent_chapter in item["chapter_ids"])
        or source_matches([item["canonical_name"], *item["aliases"]])
    ]
    selected_locations = [
        item for item in registry["locations"]
        if (recent_chapter is not None and recent_chapter in item["chapter_ids"])
        or source_matches([item["canonical_name"], *item["aliases"]])
    ]
    return {
        "registry_version": registry["registry_version"],
        "registry_sha256": hashlib.sha256(data).hexdigest(),
        "selection_strategy": "lexical-major-recent-v1",
        "total_characters": len(registry["characters"]),
        "total_locations": len(registry["locations"]),
        "characters": [
            {key: item[key] for key in ("character_id", "entity_keys", "canonical_name", "chinese_name", "aliases", "chinese_aliases")}
            for item in selected_characters
        ],
        "locations": [
            {key: item[key] for key in ("location_id", "entity_keys", "canonical_name", "aliases", "parent_names", "sub_locations")}
            for item in selected_locations
        ],
    }


def prepare_packet(project_dir: Path, chapter_id: str, replace: bool = False) -> tuple[Path, str]:
    project_dir = project_dir.resolve()
    errors = validate_project(project_dir)
    if errors:
        raise ValueError("ingest validation failed: " + "; ".join(errors))
    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    manifest = json.loads((project_dir / project["source_manifest"]).read_text(encoding="utf-8"))
    chapter = next((item for item in manifest["chapters"] if item["chapter_id"] == chapter_id), None)
    if chapter is None:
        available = ", ".join(item["chapter_id"] for item in manifest["chapters"])
        raise ValueError(f"unknown chapter {chapter_id}; available: {available}")
    unit_lines = (project_dir / chapter["units_path"]).read_text(encoding="utf-8").splitlines()
    units = [json.loads(line) for line in unit_lines]
    basename = chapter_id.lower()
    packet = {
        "packet_version": "1.1.0",
        "project_id": project["project_id"],
        "chapter_id": chapter_id,
        "source_sha256": chapter["sha256"],
        "source_unit_ids": [unit["unit_id"] for unit in units],
        "rules_ref": "references/analysis_rules.md",
        "output_schema_ref": "schemas/chapter-analysis.schema.json",
        "output_path": f"data/analysis/{basename}.analysis.json",
        "registry_context": registry_context(project_dir, units, chapter_id),
        "units": [{"unit_id": unit["unit_id"], "source_text": unit["source_text"]} for unit in units],
    }
    data = json_bytes(packet)
    packet_hash = hashlib.sha256(data).hexdigest()
    target = project_dir / "work" / "analysis" / f"{basename}.packet.json"
    hash_target = target.with_suffix(".sha256")
    if target.exists() and target.read_bytes() != data and not replace:
        raise ValueError(f"packet differs and already exists: {target}; use --replace after review")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    hash_target.write_text(packet_hash + "\n", encoding="ascii", newline="\n")
    return target, packet_hash


def main() -> int:
    parser = argparse.ArgumentParser(description="生成单章、最小上下文的语义分析工作包")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True, help="chapter ID, e.g. P01")
    parser.add_argument("--replace", action="store_true", help="replace a stale packet")
    args = parser.parse_args()
    try:
        path, packet_hash = prepare_packet(args.project_dir, args.chapter, args.replace)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"PACKET PREPARATION FAILED: {exc}")
        return 1
    print(f"PACKET PREPARATION PASSED: {path}")
    print(f"PACKET SHA256: {packet_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
