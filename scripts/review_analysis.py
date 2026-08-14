#!/usr/bin/env python3
"""Record append-only human review events and materialize one resolved chapter view."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate_analysis import BEAT_TYPES, STATUSES, TEXT_TYPES, validate_analysis

CHAPTER_RE = re.compile(r"^P[0-9]{2,}$")
SCENE_RE = re.compile(r"^P[0-9]{2,}-S[0-9]{3}$")
BEAT_RE = re.compile(r"^P[0-9]{2,}-S[0-9]{3}-B[0-9]{3}$")
UNIT_RE = re.compile(r"^P[0-9]{2,}-U[0-9]{4}$")
SEGMENT_RE = re.compile(r"^P[0-9]{2,}-U[0-9]{4}-G[0-9]{3}$")

SCENE_FIELDS = {
    "summary_zh",
    "location.location_id",
    "location.standardized_name",
    "location.parent_location",
    "location.sub_location",
    "int_ext.value",
    "time_of_day.value",
    "reality_layer.value",
}
BEAT_FIELDS = {"summary_zh", "change_type"}
SEGMENT_FIELDS = {
    "text_type",
    "translation.text_zh",
    "speaker.kind",
    "speaker.canonical_label",
    "speaker.chinese_label",
    "speaker.character_id",
}
ALLOWED_FIELDS = {"scene": SCENE_FIELDS, "beat": BEAT_FIELDS, "segment": SEGMENT_FIELDS}


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def paths(project_dir: Path, chapter_id: str) -> tuple[Path, Path, Path, Path]:
    basename = chapter_id.lower()
    return (
        project_dir / "data" / "analysis" / f"{basename}.analysis.json",
        project_dir / "data" / "reviews" / f"{basename}.events.jsonl",
        project_dir / "data" / "views" / "analysis" / f"{basename}.resolved.json",
        project_dir / "data" / "views" / "analysis" / f"{basename}.resolved.manifest.json",
    )


def scope_type(scope_id: str) -> str:
    if BEAT_RE.fullmatch(scope_id):
        return "beat"
    if SCENE_RE.fullmatch(scope_id):
        return "scene"
    if SEGMENT_RE.fullmatch(scope_id):
        return "segment"
    if UNIT_RE.fullmatch(scope_id):
        return "source_unit"
    if CHAPTER_RE.fullmatch(scope_id):
        return "chapter"
    raise ValueError(f"invalid review scope ID: {scope_id}")


def chapter_from_scope(scope_id: str) -> str:
    return scope_id.split("-", 1)[0]


def load_base(project_dir: Path, chapter_id: str) -> tuple[dict[str, Any], bytes, str]:
    errors = validate_analysis(project_dir, chapter_id)
    if errors:
        raise ValueError("base analysis validation failed: " + "; ".join(errors))
    base_path, _, _, _ = paths(project_dir, chapter_id)
    data = base_path.read_bytes()
    return json.loads(data.decode("utf-8")), data, sha256(data)


def objects_by_scope(analysis: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {("chapter", analysis["chapter_id"]): analysis}
    for unit in analysis["unit_analyses"]:
        result[("source_unit", unit["unit_id"])] = unit
        for segment in unit["segments"]:
            result[("segment", segment["segment_id"])] = segment
    for scene in analysis["scenes"]:
        result[("scene", scene["scene_id"])] = scene
        for beat in scene["beats"]:
            result[("beat", beat["beat_id"])] = beat
    return result


def read_events(events_path: Path) -> tuple[list[dict[str, Any]], bytes]:
    if not events_path.exists():
        return [], b""
    data = events_path.read_bytes()
    try:
        lines = data.decode("utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"review event log is unreadable: {exc}") from exc
    return events, data


def validate_value(kind: str, field_path: str, value: Any) -> None:
    if field_path in {"summary_zh", "location.standardized_name", "location.parent_location", "location.sub_location", "translation.text_zh", "speaker.canonical_label", "speaker.chinese_label"}:
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{field_path} must be a non-empty string or null")
    elif field_path == "location.location_id":
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"LOC-[0-9]{4}", value)):
            raise ValueError("location.location_id must be LOC-NNNN or null")
    elif field_path == "int_ext.value" and value not in {"INT", "EXT", "INT_EXT", "unknown"}:
        raise ValueError("int_ext.value is invalid")
    elif field_path == "time_of_day.value" and value not in {"day", "night", "dawn", "morning", "noon", "afternoon", "dusk", "continuous", "unknown"}:
        raise ValueError("time_of_day.value is invalid")
    elif field_path == "reality_layer.value" and value not in {"present", "flashback", "dream", "imagined", "unknown"}:
        raise ValueError("reality_layer.value is invalid")
    elif field_path == "change_type" and value not in BEAT_TYPES:
        raise ValueError("change_type is invalid")
    elif field_path == "text_type" and value not in TEXT_TYPES:
        raise ValueError("text_type is invalid")
    elif field_path == "speaker.kind" and value not in {"named", "role", "crowd", "unknown"}:
        raise ValueError("speaker.kind is invalid")
    elif field_path == "speaker.character_id":
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r"CHAR-[0-9]{4}", value)):
            raise ValueError("speaker.character_id must be CHAR-NNNN or null")


def validate_events(events: list[dict[str, Any]], chapter_id: str, base_hash: str, analysis: dict[str, Any]) -> None:
    available = objects_by_scope(analysis)
    issue_ids = {issue["issue_id"] for issue in analysis["review"]["issues"]}
    prior_ids: set[str] = set()
    prior_operations: dict[str, str] = {}
    retracted_targets: set[str] = set()
    for sequence, event in enumerate(events, 1):
        context = f"review event {sequence}"
        expected_id = f"REV-{chapter_id}-{sequence:06d}"
        required = {
            "schema_version", "event_id", "chapter_id", "sequence", "recorded_at", "actor",
            "scope_type", "scope_id", "operation", "field_path", "value", "note",
            "resolves_issue_ids", "target_event_id", "base_analysis_sha256",
        }
        if not isinstance(event, dict) or set(event) != required:
            raise ValueError(f"{context} has invalid fields")
        if event["schema_version"] != "1.0.0" or event["event_id"] != expected_id or event["sequence"] != sequence:
            raise ValueError(f"{context} has unstable identity")
        if event["chapter_id"] != chapter_id or event["base_analysis_sha256"] != base_hash:
            raise ValueError(f"{context} is stale or belongs to another chapter")
        try:
            datetime.fromisoformat(event["recorded_at"].replace("Z", "+00:00"))
        except (AttributeError, ValueError) as exc:
            raise ValueError(f"{context} has invalid recorded_at") from exc
        actor = event["actor"]
        if not isinstance(actor, dict) or actor.get("type") not in {"user", "agent"} or not actor.get("label"):
            raise ValueError(f"{context} has invalid actor")
        kind, scope_id = event["scope_type"], event["scope_id"]
        if (kind, scope_id) not in available:
            raise ValueError(f"{context} references unknown scope {kind}:{scope_id}")
        if chapter_from_scope(scope_id) != chapter_id:
            raise ValueError(f"{context} scope is outside chapter")
        operation = event["operation"]
        resolves = event["resolves_issue_ids"]
        if not isinstance(resolves, list) or len(resolves) != len(set(resolves)) or any(item not in issue_ids for item in resolves):
            raise ValueError(f"{context} resolves unknown or duplicate issue IDs")
        if not isinstance(event["note"], str) or not event["note"].strip():
            raise ValueError(f"{context} requires a note")
        if operation == "set_field":
            field_path = event["field_path"]
            if kind not in ALLOWED_FIELDS or field_path not in ALLOWED_FIELDS[kind] or event["target_event_id"] is not None:
                raise ValueError(f"{context} cannot set {kind}.{field_path}")
            validate_value(kind, field_path, event["value"])
        elif operation == "add_note":
            if event["field_path"] is not None or event["target_event_id"] is not None:
                raise ValueError(f"{context} add_note fields are invalid")
        elif operation == "retract_event":
            target = event["target_event_id"]
            if (
                event["field_path"] is not None
                or resolves
                or target not in prior_ids
                or prior_operations.get(target) == "retract_event"
                or target in retracted_targets
            ):
                raise ValueError(f"{context} has invalid retraction target")
            retracted_targets.add(target)
        else:
            raise ValueError(f"{context} has invalid operation")
        prior_ids.add(event["event_id"])
        prior_operations[event["event_id"]] = operation


def append_event(project_dir: Path, chapter_id: str, event_data: dict[str, Any]) -> dict[str, Any]:
    analysis, _, base_hash = load_base(project_dir, chapter_id)
    _, events_path, _, _ = paths(project_dir, chapter_id)
    events, old_data = read_events(events_path)
    validate_events(events, chapter_id, base_hash, analysis)
    sequence = len(events) + 1
    event = {
        "schema_version": "1.0.0",
        "event_id": f"REV-{chapter_id}-{sequence:06d}",
        "chapter_id": chapter_id,
        "sequence": sequence,
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": event_data["actor"],
        "scope_type": event_data["scope_type"],
        "scope_id": event_data["scope_id"],
        "operation": event_data["operation"],
        "field_path": event_data.get("field_path"),
        "value": event_data.get("value"),
        "note": event_data["note"],
        "resolves_issue_ids": event_data.get("resolves_issue_ids", []),
        "target_event_id": event_data.get("target_event_id"),
        "base_analysis_sha256": base_hash,
    }
    validate_events(events + [event], chapter_id, base_hash, analysis)
    new_data = old_data + json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
    atomic_write(events_path, new_data)
    return event


def set_nested(target: dict[str, Any], field_path: str, value: Any) -> None:
    parts = field_path.split(".")
    current: Any = target
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current or not isinstance(current[part], dict):
            raise ValueError(f"field path does not exist: {field_path}")
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise ValueError(f"field path does not exist: {field_path}")
    current[parts[-1]] = value


def mark_user_confirmed(target: dict[str, Any], kind: str, field_path: str) -> None:
    container: dict[str, Any] | None = None
    if kind == "scene" and field_path.startswith("location."):
        container = target["location"]
    elif kind == "scene" and field_path.endswith(".value"):
        container = target[field_path.split(".", 1)[0]]
    elif kind == "beat":
        container = target
    elif kind == "segment" and field_path == "text_type":
        container = target["classification"]
    elif kind == "segment" and field_path == "translation.text_zh":
        container = target["translation"]
    elif kind == "segment" and field_path.startswith("speaker."):
        container = target.get("speaker")
    if isinstance(container, dict) and "status" in container:
        container["status"] = "user_confirmed"
        if "confidence" in container:
            container["confidence"] = 1


def materialize(project_dir: Path, chapter_id: str) -> tuple[Path, Path, dict[str, Any]]:
    analysis, base_data, base_hash = load_base(project_dir, chapter_id)
    base_path, events_path, resolved_path, manifest_path = paths(project_dir, chapter_id)
    events, events_data = read_events(events_path)
    validate_events(events, chapter_id, base_hash, analysis)

    retracted = {event["target_event_id"] for event in events if event["operation"] == "retract_event"}
    active = [event for event in events if event["operation"] != "retract_event" and event["event_id"] not in retracted]
    resolved = copy.deepcopy(analysis)
    targets = objects_by_scope(resolved)
    resolved_issue_ids: set[str] = set()
    notes: list[dict[str, str]] = []
    for event in active:
        target = targets[(event["scope_type"], event["scope_id"])]
        if event["operation"] == "set_field":
            set_nested(target, event["field_path"], event["value"])
            mark_user_confirmed(target, event["scope_type"], event["field_path"])
        else:
            notes.append({
                "event_id": event["event_id"],
                "scope_type": event["scope_type"],
                "scope_id": event["scope_id"],
                "note": event["note"],
            })
        resolved_issue_ids.update(event["resolves_issue_ids"])

    all_issues = analysis["review"]["issues"]
    resolved["review"]["issues"] = [issue for issue in all_issues if issue["issue_id"] not in resolved_issue_ids]
    resolved["review"]["status"] = "provisional" if resolved["review"]["issues"] else "ready"
    resolved_data = json_bytes(resolved)
    atomic_write(resolved_path, resolved_data)
    manifest = {
        "schema_version": "1.0.0",
        "chapter_id": chapter_id,
        "base_analysis_path": base_path.relative_to(project_dir).as_posix(),
        "base_analysis_sha256": sha256(base_data),
        "events_path": events_path.relative_to(project_dir).as_posix(),
        "events_sha256": sha256(events_data),
        "resolved_analysis_path": resolved_path.relative_to(project_dir).as_posix(),
        "resolved_analysis_sha256": sha256(resolved_data),
        "applied_event_ids": [event["event_id"] for event in active],
        "retracted_event_ids": sorted(retracted),
        "resolved_issue_ids": sorted(resolved_issue_ids),
        "open_issue_ids": [issue["issue_id"] for issue in resolved["review"]["issues"]],
        "review_notes": notes,
    }
    atomic_write(manifest_path, json_bytes(manifest))
    return resolved_path, manifest_path, manifest


def common_event_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--note", required=True)
    parser.add_argument("--actor", default="user")
    parser.add_argument("--resolve", action="append", default=[])


def main() -> int:
    parser = argparse.ArgumentParser(description="记录人工验收并只物化受影响章节的解析视图")
    subparsers = parser.add_subparsers(dest="command", required=True)
    set_parser = subparsers.add_parser("set", help="修订一个允许修改的语义字段")
    common_event_args(set_parser)
    set_parser.add_argument("--field", required=True)
    set_parser.add_argument("--value-json", required=True)
    note_parser = subparsers.add_parser("note", help="为章节、场景、Beat或原文单元补充说明")
    common_event_args(note_parser)
    retract_parser = subparsers.add_parser("retract", help="撤销一条既有修订事件")
    retract_parser.add_argument("project_dir", type=Path)
    retract_parser.add_argument("--chapter", required=True)
    retract_parser.add_argument("--event", required=True)
    retract_parser.add_argument("--note", required=True)
    retract_parser.add_argument("--actor", default="user")
    materialize_parser = subparsers.add_parser("materialize", help="重建当前章节解析视图")
    materialize_parser.add_argument("project_dir", type=Path)
    materialize_parser.add_argument("--chapter", required=True)
    args = parser.parse_args()

    try:
        project_dir = args.project_dir.resolve()
        if args.command == "materialize":
            event = None
        elif args.command == "retract":
            analysis, _, base_hash = load_base(project_dir, args.chapter)
            _, events_path, _, _ = paths(project_dir, args.chapter)
            events, _ = read_events(events_path)
            validate_events(events, args.chapter, base_hash, analysis)
            target = next((item for item in events if item["event_id"] == args.event), None)
            if target is None:
                raise ValueError(f"unknown review event: {args.event}")
            event = append_event(project_dir, args.chapter, {
                "actor": {"type": "user", "label": args.actor},
                "scope_type": target["scope_type"],
                "scope_id": target["scope_id"],
                "operation": "retract_event",
                "note": args.note,
                "target_event_id": args.event,
            })
        else:
            kind = scope_type(args.scope)
            if chapter_from_scope(args.scope) != args.chapter:
                raise ValueError("scope is outside the selected chapter")
            payload: dict[str, Any] = {
                "actor": {"type": "user", "label": args.actor},
                "scope_type": kind,
                "scope_id": args.scope,
                "operation": "set_field" if args.command == "set" else "add_note",
                "note": args.note,
                "resolves_issue_ids": args.resolve,
            }
            if args.command == "set":
                payload["field_path"] = args.field
                payload["value"] = json.loads(args.value_json)
            event = append_event(project_dir, args.chapter, payload)
        resolved_path, manifest_path, manifest = materialize(project_dir, args.chapter)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"REVIEW FAILED: {exc}")
        return 1

    if event:
        print(f"REVIEW EVENT RECORDED: {event['event_id']}")
    print(f"RESOLVED VIEW: {resolved_path}")
    print(f"OPEN ISSUES: {len(manifest['open_issue_ids'])}")
    print(f"MANIFEST: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
