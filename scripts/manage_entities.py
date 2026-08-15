#!/usr/bin/env python3
"""Record reusable character/location merge and fact decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from build_catalogs import CHARACTER_FIELDS, build_catalogs
from preprod import SCHEMA_VERSION, append_jsonl, atomic_write, next_event_id, read_json, read_jsonl, utc_now

EVENT_PATH = "data/reviews/entities.events.jsonl"
CHARACTER_SET_FIELDS = {"canonical_name_en", "name_zh", "importance", "story_role_zh", *CHARACTER_FIELDS}
LOCATION_SET_FIELDS = {"name_en", "name_zh", "parent_zh", "int_ext"}


def _resolve(catalog: dict[str, Any], entity_id: str, entity_type: str) -> str:
    redirects = catalog["character_redirects"] if entity_type == "character" else catalog["location_redirects"]
    while entity_id in redirects:
        entity_id = redirects[entity_id]
    return entity_id


def _records(catalog: dict[str, Any], entity_type: str) -> dict[str, dict[str, Any]]:
    key, id_field = ("characters", "character_id") if entity_type == "character" else ("locations", "location_id")
    return {item[id_field]: item for item in catalog[key]}


def record_event(project_dir: Path, operation: str, entity_type: str, *, source_id: str | None = None, target_id: str | None = None, field: str | None = None, value: Any = None, note: str, actor: str, target_event_id: str | None = None) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    catalog_path = project_dir / "data" / "catalogs" / "catalog.json"
    if not catalog_path.exists():
        build_catalogs(project_dir)
    catalog = read_json(catalog_path)
    path = project_dir / EVENT_PATH
    events = read_jsonl(path)
    if operation in {"merge", "separate"}:
        if source_id is None or target_id is None or source_id == target_id:
            raise ValueError(f"{operation} requires two different entity IDs")
        source_id = _resolve(catalog, source_id, entity_type)
        target_id = _resolve(catalog, target_id, entity_type)
        records = _records(catalog, entity_type)
        if source_id not in records or target_id not in records:
            raise ValueError("entity decision target does not exist in the current catalog")
        field = None
        value = None
    elif operation == "set":
        if target_id is None or field is None:
            raise ValueError("set requires target ID and field")
        target_id = _resolve(catalog, target_id, entity_type)
        if target_id not in _records(catalog, entity_type):
            raise ValueError("entity does not exist in the current catalog")
        allowed = CHARACTER_SET_FIELDS if entity_type == "character" else LOCATION_SET_FIELDS
        if field not in allowed:
            raise ValueError(f"field cannot be set on {entity_type}: {field}")
        source_id = None
    elif operation == "retract":
        existing = next((event for event in events if event.get("event_id") == target_event_id and event.get("operation") != "retract"), None)
        if existing is None:
            raise ValueError("retraction target is not an existing decision")
        if any(event.get("operation") == "retract" and event.get("target_event_id") == target_event_id for event in events):
            raise ValueError("decision was already retracted")
        entity_type = existing["entity_type"]
        source_id = target_id = field = value = None
    else:
        raise ValueError(f"unsupported operation: {operation}")
    event = {
        "schema_version": SCHEMA_VERSION, "event_id": next_event_id(events, "ENT"), "recorded_at": utc_now(),
        "actor": actor, "operation": operation, "entity_type": entity_type,
        "source_id": source_id, "target_id": target_id, "field": field, "value": value,
        "note": note, "target_event_id": target_event_id,
    }
    old_data = path.read_bytes() if path.exists() else b""
    append_jsonl(path, event)
    try:
        build_catalogs(project_dir)
    except Exception:
        atomic_write(path, old_data)
        raise
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="归并角色/地点身份，或确认全局资料")
    sub = parser.add_subparsers(dest="command", required=True)
    merge = sub.add_parser("merge")
    separate = sub.add_parser("separate")
    set_value = sub.add_parser("set")
    retract = sub.add_parser("retract")
    for item in (merge, separate, set_value, retract):
        item.add_argument("project_dir", type=Path)
        item.add_argument("--note", required=True)
        item.add_argument("--actor", default="user")
    merge.add_argument("--type", choices=["character", "location"], required=True)
    merge.add_argument("--source", required=True)
    merge.add_argument("--target", required=True)
    separate.add_argument("--type", choices=["character", "location"], required=True)
    separate.add_argument("--source", required=True)
    separate.add_argument("--target", required=True)
    set_value.add_argument("--type", choices=["character", "location"], required=True)
    set_value.add_argument("--target", required=True)
    set_value.add_argument("--field", required=True)
    set_value.add_argument("--value-json", required=True)
    retract.add_argument("--event", required=True)
    args = parser.parse_args()
    try:
        if args.command == "merge":
            event = record_event(args.project_dir, "merge", args.type, source_id=args.source, target_id=args.target, note=args.note, actor=args.actor)
        elif args.command == "separate":
            event = record_event(args.project_dir, "separate", args.type, source_id=args.source, target_id=args.target, note=args.note, actor=args.actor)
        elif args.command == "set":
            event = record_event(args.project_dir, "set", args.type, target_id=args.target, field=args.field, value=json.loads(args.value_json), note=args.note, actor=args.actor)
        else:
            event = record_event(args.project_dir, "retract", "character", target_event_id=args.event, note=args.note, actor=args.actor)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"ENTITY DECISION FAILED: {exc}")
        return 1
    print(f"ENTITY DECISION RECORDED: {event['event_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
