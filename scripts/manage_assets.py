#!/usr/bin/env python3
"""Record human decisions for the art-asset candidate inventory."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from build_catalogs import build_catalogs
from preprod import SCHEMA_VERSION, append_jsonl, atomic_write, next_event_id, read_json, read_jsonl, utc_now

EVENT_PATH = "data/reviews/assets.events.jsonl"
STATUSES = {"pending", "approved", "excluded", "information_pending"}


def _resolve(catalog: dict[str, Any], asset_id: str) -> str:
    redirects = catalog.get("asset_redirects", {})
    while asset_id in redirects:
        asset_id = redirects[asset_id]
    return asset_id


def record_event(project_dir: Path, operation: str, *, asset_id: str | None = None, target_asset_id: str | None = None, status: str | None = None, name_zh: str | None = None, scene_ids: list[str] | None = None, note: str, actor: str, target_event_id: str | None = None) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    catalog_path = project_dir / "data" / "catalogs" / "catalog.json"
    if not catalog_path.exists():
        build_catalogs(project_dir)
    catalog = read_json(catalog_path)
    current = {item["asset_id"]: item for item in catalog["assets"]}
    path = project_dir / EVENT_PATH
    events = read_jsonl(path)
    if operation in {"decide", "merge", "split"}:
        if asset_id is None:
            raise ValueError(f"{operation} requires an asset ID")
        asset_id = _resolve(catalog, asset_id)
        if asset_id not in current:
            raise ValueError("asset does not exist in the current inventory")
    if operation == "decide":
        if status not in STATUSES:
            raise ValueError("invalid asset decision status")
        target_asset_id = name_zh = None
        scene_ids = None
    elif operation == "merge":
        if target_asset_id is None:
            raise ValueError("merge requires target asset ID")
        target_asset_id = _resolve(catalog, target_asset_id)
        if target_asset_id not in current or target_asset_id == asset_id:
            raise ValueError("invalid asset merge target")
        status = name_zh = None
        scene_ids = None
    elif operation == "split":
        if not name_zh or not scene_ids:
            raise ValueError("split requires a new name and at least one Scene ID")
        unknown = set(scene_ids) - set(current[asset_id]["scene_ids"])
        if unknown:
            raise ValueError(f"split references scenes outside the asset: {sorted(unknown)}")
        target_asset_id = status = None
    elif operation == "retract":
        existing = next((event for event in events if event.get("event_id") == target_event_id and event.get("operation") != "retract"), None)
        if existing is None:
            raise ValueError("retraction target is not an existing decision")
        if any(event.get("operation") == "retract" and event.get("target_event_id") == target_event_id for event in events):
            raise ValueError("decision was already retracted")
        asset_id = target_asset_id = status = name_zh = scene_ids = None
    else:
        raise ValueError(f"unsupported operation: {operation}")
    event = {
        "schema_version": SCHEMA_VERSION, "event_id": next_event_id(events, "ASREV"), "recorded_at": utc_now(),
        "actor": actor, "operation": operation, "asset_id": asset_id, "target_asset_id": target_asset_id,
        "status": status, "name_zh": name_zh, "scene_ids": scene_ids, "note": note,
        "target_event_id": target_event_id,
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
    parser = argparse.ArgumentParser(description="审核全文美术资产候选清单")
    sub = parser.add_subparsers(dest="command", required=True)
    decide = sub.add_parser("decide")
    merge = sub.add_parser("merge")
    split = sub.add_parser("split")
    retract = sub.add_parser("retract")
    for item in (decide, merge, split, retract):
        item.add_argument("project_dir", type=Path)
        item.add_argument("--note", required=True)
        item.add_argument("--actor", default="user")
    decide.add_argument("--asset", required=True)
    decide.add_argument("--status", choices=sorted(STATUSES), required=True)
    merge.add_argument("--asset", required=True)
    merge.add_argument("--target", required=True)
    split.add_argument("--asset", required=True)
    split.add_argument("--name", required=True)
    split.add_argument("--scene", action="append", required=True)
    retract.add_argument("--event", required=True)
    args = parser.parse_args()
    try:
        if args.command == "decide":
            event = record_event(args.project_dir, "decide", asset_id=args.asset, status=args.status, note=args.note, actor=args.actor)
        elif args.command == "merge":
            event = record_event(args.project_dir, "merge", asset_id=args.asset, target_asset_id=args.target, note=args.note, actor=args.actor)
        elif args.command == "split":
            event = record_event(args.project_dir, "split", asset_id=args.asset, name_zh=args.name, scene_ids=args.scene, note=args.note, actor=args.actor)
        else:
            event = record_event(args.project_dir, "retract", target_event_id=args.event, note=args.note, actor=args.actor)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ASSET DECISION FAILED: {exc}")
        return 1
    print(f"ASSET DECISION RECORDED: {event['event_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
