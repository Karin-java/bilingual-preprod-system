#!/usr/bin/env python3
"""Shared deterministic utilities for the V3 pre-production pipeline."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "3.0.0"
PIPELINE_VERSION = "3.0.0"
CHAPTER_RE = re.compile(r"^P[0-9]{2,}$")
BLOCK_RE = re.compile(r"^(P[0-9]{2,})-B([0-9]{4})$")
SCENE_RE = re.compile(r"^(P[0-9]{2,})-S([0-9]{3})$")
BEAT_RE = re.compile(r"^(P[0-9]{2,}-S[0-9]{3})-B([0-9]{3})$")


def json_bytes(value: Any, *, compact: bool = False) -> bytes:
    if compact:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    return (text + "\n").encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes())


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any, *, compact: bool = False) -> bytes:
    data = json_bytes(value, compact=compact)
    atomic_write(path, data)
    return data


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_key(value: str) -> str:
    value = value.casefold().strip()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^\w\-\u3400-\u9fff]+", "", value)
    return value.strip("-") or "unknown"


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def project_relative(project_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(project_dir.resolve()).as_posix()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: JSONL record must be an object")
        records.append(value)
    return records


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    old = path.read_bytes() if path.exists() else b""
    atomic_write(path, old + json_bytes(value, compact=True))


def active_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    retracted = {
        event.get("target_event_id")
        for event in events
        if event.get("operation") == "retract"
    }
    return [
        event for event in events
        if event.get("operation") != "retract" and event.get("event_id") not in retracted
    ]


def next_event_id(events: list[dict[str, Any]], prefix: str) -> str:
    largest = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-([0-9]{{6}})$")
    for event in events:
        match = pattern.fullmatch(str(event.get("event_id", "")))
        if match:
            largest = max(largest, int(match.group(1)))
    return f"{prefix}-{largest + 1:06d}"


def load_project(project_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    project_dir = project_dir.resolve()
    project = read_json(project_dir / "project.json")
    manifest = read_json(project_dir / project["source_manifest"])
    return project, manifest


def find_chapter(manifest: dict[str, Any], chapter_id: str) -> dict[str, Any]:
    chapter = next((item for item in manifest["chapters"] if item["chapter_id"] == chapter_id), None)
    if chapter is None:
        available = ", ".join(item["chapter_id"] for item in manifest["chapters"])
        raise ValueError(f"unknown chapter {chapter_id}; available: {available}")
    return chapter


def quote_markdown(value: str) -> list[str]:
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return [f"> {line}" if line else ">" for line in lines]


def table_cell(value: Any) -> str:
    if value is None:
        return "待确认"
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")
    return text or "待确认"
