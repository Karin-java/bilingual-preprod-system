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
        "packet_version": "1.0.0",
        "project_id": project["project_id"],
        "chapter_id": chapter_id,
        "source_sha256": chapter["sha256"],
        "source_unit_ids": [unit["unit_id"] for unit in units],
        "rules_ref": "references/analysis_rules.md",
        "output_schema_ref": "schemas/chapter-analysis.schema.json",
        "output_path": f"data/analysis/{basename}.analysis.json",
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
