#!/usr/bin/env python3
"""Append targeted chapter review events and materialize a current V3 view."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from preprod import SCHEMA_VERSION, active_events, append_jsonl, json_bytes, next_event_id, read_jsonl, utc_now, write_json
from validate_analysis import validate_record

EVENT_PREFIX = "REV"
ALLOWED_FIELDS: dict[str, set[str]] = {
    "block": {"kind", "speaker_ref", "text_zh"},
    "scene": {"name_zh", "name_en", "location_ref", "parent_location_zh", "int_ext", "time", "summary_zh", "character_refs"},
    "beat": {"title_zh", "summary_zh"},
    "character": {"canonical_name_en", "name_zh", "aliases_en", "aliases_zh", "category", "importance", "story_role_zh"},
    "character_fact": {"subject_ref", "field", "value_zh", "source_block_ids"},
    "location": {"name_en", "name_zh", "aliases_en", "aliases_zh", "parent_zh", "int_ext"},
    "location_fact": {"field", "value_zh", "source_block_ids"},
    "appearance": {"character_ref", "scene_id", "presence", "summary_zh", "source_block_ids"},
    "identity_link": {"entity_type", "left_ref", "right_ref", "relationship", "note_zh", "source_block_ids"},
    "asset_candidate": {"type", "name_zh", "name_en", "subject_ref", "variant_zh", "scene_ids", "source_block_ids", "reason_zh"},
}


def event_path(project_dir: Path, chapter_id: str) -> Path:
    return project_dir / "data" / "reviews" / f"{chapter_id.lower()}.events.jsonl"


def base_path(project_dir: Path, chapter_id: str) -> Path:
    return project_dir / "data" / "chapters" / f"{chapter_id.lower()}.json"


def resolved_path(project_dir: Path, chapter_id: str) -> Path:
    return project_dir / "data" / "resolved" / f"{chapter_id.lower()}.json"


def _index(record: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    index: dict[str, tuple[str, dict[str, Any]]] = {}

    def add(key: str, kind: str, item: dict[str, Any]) -> None:
        if key in index:
            raise ValueError(f"ambiguous review target: {key}")
        index[key] = (kind, item)

    for block in record["blocks"]:
        add(block["block_id"], "block", block)
    for scene in record["scenes"]:
        add(scene["scene_id"], "scene", scene)
        for beat in scene["beats"]:
            add(beat["beat_id"], "beat", beat)
    for item in record["characters"]:
        add(f"character:{item['ref']}", "character", item)
    for item in record["character_facts"]:
        add(item["fact_id"], "character_fact", item)
    for item in record["locations"]:
        add(f"location:{item['ref']}", "location", item)
        for fact in item["facts"]:
            add(fact["fact_id"], "location_fact", fact)
    for item in record["appearances"]:
        add(item["appearance_id"], "appearance", item)
    for item in record["identity_links"]:
        add(item["link_id"], "identity_link", item)
    for item in record["asset_candidates"]:
        add(f"asset:{item['candidate_key']}", "asset_candidate", item)
    for item in record["issues"]:
        add(item["issue_id"], "issue", item)
    return index


def validate_events(chapter_id: str, events: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    retracted: set[str] = set()
    prefix = f"REV-{chapter_id}-"
    previous = 0
    for event in events:
        expected = {"schema_version", "event_id", "recorded_at", "actor", "operation", "target_id", "field", "value", "note", "target_event_id"}
        if set(event) != expected or event.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("invalid review event shape")
        event_id = str(event.get("event_id", ""))
        if not event_id.startswith(prefix) or not event_id[len(prefix):].isdigit():
            raise ValueError(f"invalid review event ID: {event_id}")
        number = int(event_id[len(prefix):])
        if number <= previous:
            raise ValueError("review event IDs must increase")
        previous = number
        if not isinstance(event.get("actor"), str) or not event["actor"].strip() or not isinstance(event.get("note"), str) or not event["note"].strip():
            raise ValueError(f"{event_id}: actor and note are required")
        operation = event.get("operation")
        if operation == "retract":
            target = event.get("target_event_id")
            if target not in seen or target in retracted:
                raise ValueError(f"{event_id}: invalid retraction target")
            retracted.add(target)
            if any(event.get(key) is not None for key in ("target_id", "field", "value")):
                raise ValueError(f"{event_id}: retraction payload must be null")
        elif operation == "set":
            if not event.get("target_id") or not event.get("field") or event.get("target_event_id") is not None:
                raise ValueError(f"{event_id}: set requires target and field")
        elif operation == "resolve_issue":
            if not event.get("target_id") or not isinstance(event.get("value"), str) or not event["value"].strip():
                raise ValueError(f"{event_id}: issue resolution text is required")
            if event.get("field") is not None or event.get("target_event_id") is not None:
                raise ValueError(f"{event_id}: invalid issue resolution payload")
        elif operation == "note":
            if event.get("field") is not None or event.get("value") is not None or event.get("target_event_id") is not None:
                raise ValueError(f"{event_id}: note has unexpected payload")
        else:
            raise ValueError(f"{event_id}: unsupported operation")
        seen.add(event_id)


def materialize(project_dir: Path, chapter_id: str) -> tuple[Path, dict[str, Any]]:
    project_dir = project_dir.resolve()
    record = json.loads(base_path(project_dir, chapter_id).read_text(encoding="utf-8"))
    events = read_jsonl(event_path(project_dir, chapter_id))
    validate_events(chapter_id, events)
    current = copy.deepcopy(record)
    for event in active_events(events):
        operation = event["operation"]
        if operation == "note":
            continue
        index = _index(current)
        target_id = event["target_id"]
        if target_id not in index:
            raise ValueError(f"{event['event_id']}: target not found: {target_id}")
        kind, target = index[target_id]
        if operation == "set":
            field = event["field"]
            if field not in ALLOWED_FIELDS.get(kind, set()):
                raise ValueError(f"{event['event_id']}: field {field} cannot be changed on {kind}")
            target[field] = copy.deepcopy(event["value"])
        elif operation == "resolve_issue":
            if kind != "issue":
                raise ValueError(f"{event['event_id']}: target is not an issue")
            target["status"] = "resolved"
            target["resolution_zh"] = event["value"]
    errors = validate_record(project_dir, chapter_id, current)
    if errors:
        raise ValueError("reviewed chapter is invalid: " + "; ".join(errors))
    path = resolved_path(project_dir, chapter_id)
    write_json(path, current)
    return path, current


def load_current_analysis(project_dir: Path, chapter_id: str) -> tuple[dict[str, Any], Path, bytes]:
    project_dir = project_dir.resolve()
    events = event_path(project_dir, chapter_id)
    resolved = resolved_path(project_dir, chapter_id)
    if events.exists() and events.stat().st_size:
        path, record = materialize(project_dir, chapter_id)
        return record, path, path.read_bytes()
    path = base_path(project_dir, chapter_id)
    data = path.read_bytes()
    return json.loads(data.decode("utf-8")), path, data


def append_event(project_dir: Path, chapter_id: str, operation: str, target_id: str | None, field: str | None, value: Any, note: str, actor: str, target_event_id: str | None = None) -> dict[str, Any]:
    path = event_path(project_dir.resolve(), chapter_id)
    events = read_jsonl(path)
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": next_event_id(events, f"REV-{chapter_id}"),
        "recorded_at": utc_now(), "actor": actor, "operation": operation,
        "target_id": target_id, "field": field, "value": value,
        "note": note, "target_event_id": target_event_id,
    }
    append_jsonl(path, event)
    try:
        materialize(project_dir, chapter_id)
        from render_chapter import render_chapter
        from build_catalogs import build_catalogs
        render_chapter(project_dir, chapter_id)
        build_catalogs(project_dir)
    except Exception:
        path.write_bytes(b"".join(json_bytes(item, compact=True) for item in events))
        if events:
            materialize(project_dir, chapter_id)
        else:
            resolved_path(project_dir, chapter_id).unlink(missing_ok=True)
        raise
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="记录单章或单场的定向人工验收")
    sub = parser.add_subparsers(dest="command", required=True)
    materialize_parser = sub.add_parser("materialize")
    set_parser = sub.add_parser("set")
    resolve_parser = sub.add_parser("resolve")
    note_parser = sub.add_parser("note")
    retract_parser = sub.add_parser("retract")
    for item in (materialize_parser, set_parser, resolve_parser, note_parser, retract_parser):
        item.add_argument("project_dir", type=Path)
        item.add_argument("--chapter", required=True)
    set_parser.add_argument("--target", required=True)
    set_parser.add_argument("--field", required=True)
    set_parser.add_argument("--value-json", required=True)
    set_parser.add_argument("--note", required=True)
    set_parser.add_argument("--actor", default="user")
    resolve_parser.add_argument("--issue", required=True)
    resolve_parser.add_argument("--resolution", required=True)
    resolve_parser.add_argument("--note", required=True)
    resolve_parser.add_argument("--actor", default="user")
    note_parser.add_argument("--target")
    note_parser.add_argument("--note", required=True)
    note_parser.add_argument("--actor", default="user")
    retract_parser.add_argument("--event", required=True)
    retract_parser.add_argument("--note", required=True)
    retract_parser.add_argument("--actor", default="user")
    args = parser.parse_args()
    try:
        if args.command == "materialize":
            path, _ = materialize(args.project_dir, args.chapter)
            event = None
        elif args.command == "set":
            value = json.loads(args.value_json)
            event = append_event(args.project_dir, args.chapter, "set", args.target, args.field, value, args.note, args.actor)
            path = resolved_path(args.project_dir.resolve(), args.chapter)
        elif args.command == "resolve":
            event = append_event(args.project_dir, args.chapter, "resolve_issue", args.issue, None, args.resolution, args.note, args.actor)
            path = resolved_path(args.project_dir.resolve(), args.chapter)
        elif args.command == "note":
            event = append_event(args.project_dir, args.chapter, "note", args.target, None, None, args.note, args.actor)
            path = resolved_path(args.project_dir.resolve(), args.chapter)
        else:
            event = append_event(args.project_dir, args.chapter, "retract", None, None, None, args.note, args.actor, args.event)
            path = resolved_path(args.project_dir.resolve(), args.chapter)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"REVIEW FAILED: {exc}")
        return 1
    if event:
        print(f"REVIEW RECORDED: {event['event_id']}")
    print(f"CURRENT CHAPTER VIEW: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
