#!/usr/bin/env python3
"""Append auditable character identity decisions and rebuild the unified registry."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_registries import (
    IDENTITY_EVENT_PATH,
    atomic_write,
    build_registries,
    json_bytes,
    load_identity_events,
    resolve_character_id,
)


def next_event_id(events: list[dict[str, Any]]) -> str:
    numbers = [int(event["event_id"].split("-")[1]) for event in events]
    return f"IDENT-{max(numbers, default=0) + 1:06d}"


def append_identity_event(project_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    registry_path = project_dir / "data" / "registries" / "entities.json"
    if not registry_path.exists():
        build_registries(project_dir)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    events, event_data = load_identity_events(project_dir)
    operation = payload.get("operation")
    if operation == "merge_characters":
        redirects = registry.get("character_id_redirects", {})
        known_ids = {item["character_id"] for item in registry["characters"]} | set(redirects)
        source_raw = str(payload.get("source_character_id"))
        target_raw = str(payload.get("target_character_id"))
        if source_raw not in known_ids or target_raw not in known_ids:
            raise ValueError("merge refers to an unknown character ID")
        source = resolve_character_id(redirects, source_raw)
        target = resolve_character_id(redirects, target_raw)
        if source == target:
            raise ValueError("characters already resolve to the same identity")
        event_fields = {
            "source_character_id": source,
            "target_character_id": target,
            "canonical_name": payload.get("canonical_name"),
            "chinese_name": payload.get("chinese_name"),
            "aliases": list(dict.fromkeys(payload.get("aliases", []))),
            "chinese_aliases": list(dict.fromkeys(payload.get("chinese_aliases", []))),
            "source_observation_ids": list(dict.fromkeys(payload.get("source_observation_ids", []))),
        }
    elif operation == "retract_event":
        target_event_id = payload.get("target_event_id")
        by_id = {event["event_id"]: event for event in events}
        if target_event_id not in by_id or by_id[target_event_id]["operation"] != "merge_characters":
            raise ValueError("retraction must target an existing merge event")
        if any(event.get("target_event_id") == target_event_id for event in events if event["operation"] == "retract_event"):
            raise ValueError("merge event is already retracted")
        event_fields = {"target_event_id": target_event_id}
    else:
        raise ValueError("unsupported identity operation")

    actor = payload.get("actor", {"type": "user", "label": "用户验收"})
    if actor.get("type") not in {"user", "agent", "system"} or not str(actor.get("label", "")).strip():
        raise ValueError("actor is invalid")
    note = str(payload.get("note", "")).strip()
    if not note:
        raise ValueError("identity decision note is required")
    event = {
        "schema_version": "2.0.0",
        "event_id": next_event_id(events),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": actor,
        "operation": operation,
        **event_fields,
        "note": note,
    }
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    event_path = project_dir / IDENTITY_EVENT_PATH
    atomic_write(event_path, event_data + line)
    try:
        build_registries(project_dir)
    except Exception:
        if event_data:
            atomic_write(event_path, event_data)
        else:
            event_path.unlink(missing_ok=True)
        raise
    return event


def main() -> int:
    parser = argparse.ArgumentParser(description="确认或撤销角色身份归并；用户无需直接编辑 JSONL")
    subparsers = parser.add_subparsers(dest="command", required=True)
    merge = subparsers.add_parser("merge", help="将两个角色索引归入同一角色实体")
    merge.add_argument("project_dir", type=Path)
    merge.add_argument("--source", required=True, dest="source_character_id")
    merge.add_argument("--target", required=True, dest="target_character_id")
    merge.add_argument("--canonical-name")
    merge.add_argument("--chinese-name")
    merge.add_argument("--alias", action="append", default=[], dest="aliases")
    merge.add_argument("--chinese-alias", action="append", default=[], dest="chinese_aliases")
    merge.add_argument("--observation", action="append", default=[], dest="source_observation_ids")
    merge.add_argument("--note", required=True)
    merge.add_argument("--actor-label", default="用户验收")
    retract = subparsers.add_parser("retract", help="撤销一次已确认的角色归并")
    retract.add_argument("project_dir", type=Path)
    retract.add_argument("--event", required=True, dest="target_event_id")
    retract.add_argument("--note", required=True)
    retract.add_argument("--actor-label", default="用户验收")
    args = parser.parse_args()
    payload = vars(args)
    project_dir = payload.pop("project_dir")
    command = payload.pop("command")
    actor_label = payload.pop("actor_label")
    payload["operation"] = "merge_characters" if command == "merge" else "retract_event"
    payload["actor"] = {"type": "user", "label": actor_label}
    try:
        event = append_identity_event(project_dir, payload)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"IDENTITY UPDATE FAILED: {exc}")
        return 1
    print(f"IDENTITY EVENT: {event['event_id']}")
    print("ENTITY REGISTRY REBUILT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
