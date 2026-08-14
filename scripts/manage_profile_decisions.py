#!/usr/bin/env python3
"""Append or retract global profile decisions, then rebuild derived profiles."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from build_profiles import EVENT_PATH, EVENT_RE, OBSERVATION_FIELDS, build_profile_data, build_profiles, load_events
from build_registries import atomic_write, build_registries, resolve_character_reference


def append_event(project_dir: Path, payload: dict) -> dict:
    path = project_dir / EVENT_PATH
    events, old_data = load_events(project_dir)
    character_ref = payload.get("character_ref")
    if payload["operation"] == "set_field":
        registry_path = project_dir / "data" / "registries" / "entities.json"
        if not registry_path.exists():
            _, _, registry = build_registries(project_dir)
        else:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        resolution = resolve_character_reference(registry, character_ref)
        if resolution.get("status") != "resolved":
            raise ValueError(f"character reference is not uniquely resolvable: {character_ref}")
        character_ref = resolution["character_id"]
    event = {
        "schema_version": "1.0.0",
        "event_id": f"PROFILE-{len(events) + 1:06d}",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": {"type": "user", "label": payload.get("actor", "user")},
        "operation": payload["operation"],
        "character_ref": character_ref,
        "field": payload.get("field"),
        "value_zh": payload.get("value_zh"),
        "normalized_value": payload.get("normalized_value"),
        "note": payload["note"],
        "target_event_id": payload.get("target_event_id"),
    }
    candidate = old_data + json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    atomic_write(path, candidate)
    try:
        load_events(project_dir)
        build_profile_data(project_dir)
    except Exception:
        atomic_write(path, old_data)
        raise
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="记录不回写旧章节的角色资料确认")
    subparsers = parser.add_subparsers(dest="command", required=True)
    set_parser = subparsers.add_parser("set", help="确认角色的一个资料字段")
    set_parser.add_argument("project_dir", type=Path)
    set_parser.add_argument("--character", required=True, help="角色编号或任一已登记名称")
    set_parser.add_argument("--field", required=True, choices=sorted(OBSERVATION_FIELDS))
    set_parser.add_argument("--value", required=True)
    set_parser.add_argument("--normalized", help="用于跨章去重；默认等于当前值")
    set_parser.add_argument("--note", required=True)
    set_parser.add_argument("--actor", default="user")
    retract_parser = subparsers.add_parser("retract", help="撤销一条角色资料确认")
    retract_parser.add_argument("project_dir", type=Path)
    retract_parser.add_argument("--event", required=True)
    retract_parser.add_argument("--note", required=True)
    retract_parser.add_argument("--actor", default="user")
    build_parser = subparsers.add_parser("materialize", help="重建当前角色档案")
    build_parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    project_dir = args.project_dir.resolve()
    try:
        event = None
        if args.command == "set":
            event = append_event(project_dir, {
                "operation": "set_field", "character_ref": args.character, "field": args.field,
                "value_zh": args.value, "normalized_value": args.normalized or args.value.strip().casefold(),
                "note": args.note, "actor": args.actor,
            })
        elif args.command == "retract":
            if not EVENT_RE.fullmatch(args.event):
                raise ValueError("event must be PROFILE-NNNNNN")
            event = append_event(project_dir, {
                "operation": "retract_event", "target_event_id": args.event,
                "note": args.note, "actor": args.actor,
            })
        data_path, profile_path, recap_path, bundle = build_profiles(project_dir)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"PROFILE DECISION FAILED: {exc}")
        return 1
    if event:
        print(f"PROFILE EVENT RECORDED: {event['event_id']}")
    print(f"PROFILES: {len(bundle['profiles'])}; RECAP TASKS: {len(bundle['recap_tasks'])}")
    print(data_path)
    print(profile_path)
    print(recap_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
