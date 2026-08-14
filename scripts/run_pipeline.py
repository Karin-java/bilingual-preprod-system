#!/usr/bin/env python3
"""Run or resume the deterministic parts of the pre-production pipeline."""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_appearance_reports import build_appearance_reports
from build_profiles import build_profiles
from build_registries import atomic_write, build_registries, json_bytes, sha256
from ingest_source import ingest_source
from prepare_analysis import prepare_packet
from render_chapter import load_current_analysis, render_chapter
from review_analysis import materialize
from validate_analysis import validate_analysis
from validate_appearance_reports import validate_appearance_reports
from validate_ingest import validate_project
from validate_profiles import validate_profiles
from validate_registries import validate_registries
from validate_render import validate_render

PIPELINE_VERSION = "preprod-pipeline-v1"
TOKEN_METHOD = "character-class-range-v1; actual usage depends on model tokenizer"
STATE_PATH = "data/pipeline/state.json"
TASK_PATH = "work/pipeline/next-task.json"
PROGRESS_PATH = "deliverables/制作进度.md"
RECAP_EVENT_PATH = "data/pipeline/recap-review.events.jsonl"
RECAP_EVENT_RE = re.compile(r"^RECAPREV-([0-9]{6})$")
CJK_RE = re.compile(r"[\u3400-\u9fff]")


def relative(project_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(project_dir.resolve()).as_posix()


def file_input(project_dir: Path, path: Path) -> dict[str, str]:
    data = path.read_bytes()
    return {"path": relative(project_dir, path), "sha256": sha256(data)}


def optional_output(project_dir: Path, path: Path, status: str) -> dict[str, Any]:
    if not path.exists():
        return {"status": "not_available", "path": None, "sha256": None}
    return {"status": status, "path": relative(project_dir, path), "sha256": sha256(path.read_bytes())}


def estimate_tokens(data: bytes) -> tuple[int, int]:
    text = data.decode("utf-8", errors="replace")
    cjk = len(CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    minimum = math.ceil(cjk + other / 4.5)
    maximum = math.ceil(cjk * 1.5 + other / 2.5)
    return minimum, maximum


def task_with_hash(payload: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(payload)
    result["task_sha256"] = sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return result


def write_task_packet(path: Path, payload: dict[str, Any]) -> bytes:
    data = json_bytes(payload)
    atomic_write(path, data)
    return data


def load_recap_events(project_dir: Path) -> tuple[list[dict[str, Any]], bytes]:
    path = project_dir / RECAP_EVENT_PATH
    if not path.exists():
        return [], b""
    data = path.read_bytes()
    events: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    retracted: set[str] = set()
    last = 0
    required = {"schema_version", "event_id", "recorded_at", "actor", "operation", "character_id", "task_sha256", "note", "target_event_id"}
    for line_number, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid recap review event at line {line_number}: {exc}") from exc
        match = RECAP_EVENT_RE.fullmatch(str(event.get("event_id")))
        if not isinstance(event, dict) or set(event) != required or event.get("schema_version") != "1.0.0" or not match:
            raise ValueError(f"invalid recap review event at line {line_number}")
        number = int(match.group(1))
        if number <= last:
            raise ValueError("recap review event IDs must be increasing")
        last = number
        actor = event.get("actor")
        if not isinstance(actor, dict) or set(actor) != {"type", "label"} or actor.get("type") not in {"user", "agent"} or not actor.get("label"):
            raise ValueError(f"{event['event_id']}: invalid actor")
        if not isinstance(event.get("note"), str) or not event["note"].strip():
            raise ValueError(f"{event['event_id']}: note is required")
        if event.get("operation") == "accept_task":
            if not re.fullmatch(r"CHAR-[0-9]{4}", str(event.get("character_id"))) or not re.fullmatch(r"[a-f0-9]{64}", str(event.get("task_sha256"))) or event.get("target_event_id") is not None:
                raise ValueError(f"{event['event_id']}: invalid accepted task")
        elif event.get("operation") == "retract_event":
            target = event.get("target_event_id")
            if target not in seen or seen[target]["operation"] != "accept_task" or target in retracted:
                raise ValueError(f"{event['event_id']}: invalid retraction target")
            if event.get("character_id") is not None or event.get("task_sha256") is not None:
                raise ValueError(f"{event['event_id']}: retraction payload must be null")
            retracted.add(target)
        else:
            raise ValueError(f"{event['event_id']}: unsupported operation")
        events.append(event)
        seen[event["event_id"]] = event
    return events, data


def active_recap_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    retracted = {event["target_event_id"] for event in events if event["operation"] == "retract_event"}
    active = [event for event in events if event["operation"] == "accept_task" and event["event_id"] not in retracted]
    return active, sorted(retracted, key=lambda value: int(value.split("-")[1]))


def recap_task_hash(task: dict[str, Any]) -> str:
    stable = {key: task[key] for key in ("character_id", "fields", "context_clues")}
    return sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def chapter_record(project_dir: Path, chapter: dict[str, Any]) -> tuple[dict[str, Any], bool, bool]:
    chapter_id = chapter["chapter_id"]
    basename = chapter_id.lower()
    packet_path = project_dir / "work" / "analysis" / f"{basename}.packet.json"
    analysis_path = project_dir / "data" / "analysis" / f"{basename}.analysis.json"
    resolved_path = project_dir / "data" / "views" / "analysis" / f"{basename}.resolved.json"
    render_manifest = project_dir / "data" / "render" / f"{basename}.render-manifest.json"
    base = {
        "chapter_id": chapter_id, "source_sha256": chapter["sha256"], "status": "waiting_analysis",
        "packet": file_input(project_dir, packet_path) if packet_path.exists() else None,
        "analysis": file_input(project_dir, analysis_path) if analysis_path.exists() else None,
        "resolved_view": None, "render": None, "pending_issue_count": 0, "errors": [],
    }
    if not analysis_path.exists():
        return base, False, False
    rebuilt = False
    if not packet_path.exists():
        prepare_packet(project_dir, chapter_id, replace=True)
        base["packet"] = file_input(project_dir, packet_path)
        rebuilt = True
    errors = validate_analysis(project_dir, chapter_id)
    if errors:
        base["status"] = "needs_repair"
        base["errors"] = errors
        return base, False, rebuilt
    try:
        _, current_path, _ = load_current_analysis(project_dir, chapter_id)
        if current_path != resolved_path:
            materialize(project_dir, chapter_id)
            rebuilt = True
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        try:
            materialize(project_dir, chapter_id)
            rebuilt = True
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            base["status"] = "needs_repair"
            base["errors"] = [f"resolved view cannot be rebuilt: {exc}"]
            return base, False, rebuilt
    render_errors = validate_render(project_dir, chapter_id)
    if render_errors:
        try:
            render_chapter(project_dir, chapter_id)
            rebuilt = True
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            base["status"] = "needs_repair"
            base["errors"] = [f"chapter render cannot be rebuilt: {exc}"]
            return base, False, rebuilt
        render_errors = validate_render(project_dir, chapter_id)
        if render_errors:
            base["status"] = "needs_repair"
            base["errors"] = render_errors
            return base, False, rebuilt
    analysis, current_path, current_data = load_current_analysis(project_dir, chapter_id)
    base.update({
        "status": "ready", "packet": file_input(project_dir, packet_path),
        "analysis": file_input(project_dir, analysis_path),
        "resolved_view": {"path": relative(project_dir, current_path), "sha256": sha256(current_data)},
        "render": file_input(project_dir, render_manifest),
        "pending_issue_count": len(analysis["review"]["issues"]), "errors": [],
    })
    return base, True, rebuilt


def ensure_globals(project_dir: Path) -> tuple[dict[str, Any], int, int]:
    reused = 0
    rebuilt = 0
    registry_path = project_dir / "data" / "registries" / "entities.json"
    if registry_path.exists() and not validate_registries(project_dir):
        registry_status = "reused"
        reused += 1
    else:
        build_registries(project_dir)
        errors = validate_registries(project_dir)
        if errors:
            raise ValueError("registry rebuild failed: " + "; ".join(errors))
        registry_status = "rebuilt"
        rebuilt += 1

    profile_path = project_dir / "data" / "profiles" / "profiles.json"
    if profile_path.exists() and not validate_profiles(project_dir):
        profile_status = "reused"
        reused += 1
    else:
        build_profiles(project_dir)
        errors = validate_profiles(project_dir)
        if errors:
            raise ValueError("profile rebuild failed: " + "; ".join(errors))
        profile_status = "rebuilt"
        rebuilt += 1

    appearance_path = project_dir / "data" / "appearances" / "appearances.json"
    if appearance_path.exists() and not validate_appearance_reports(project_dir):
        appearance_status = "reused"
        reused += 1
    else:
        build_appearance_reports(project_dir)
        errors = validate_appearance_reports(project_dir)
        if errors:
            raise ValueError("appearance rebuild failed: " + "; ".join(errors))
        appearance_status = "rebuilt"
        rebuilt += 1
    return {
        "registries": optional_output(project_dir, registry_path, registry_status),
        "profiles": optional_output(project_dir, profile_path, profile_status),
        "appearances": optional_output(project_dir, appearance_path, appearance_status),
    }, reused, rebuilt


def analyze_task(project_dir: Path, chapter: dict[str, Any], packet_path: Path) -> dict[str, Any]:
    data = packet_path.read_bytes()
    minimum, maximum = estimate_tokens(data)
    payload = {
        "schema_version": "1.0.0", "task_type": "analyze_chapter", "chapter_id": chapter["chapter_id"], "character_id": None, "target_label": chapter["chapter_id"],
        "input_path": relative(project_dir, packet_path), "input_sha256": sha256(data),
        "output_path": f"data/analysis/{chapter['chapter_id'].lower()}.analysis.json",
        "instructions": ["references/analysis_rules.md", "schemas/chapter-analysis.schema.json"],
        "reason": ["本章尚未生成语义分析；只处理当前单章工作包。"],
        "input_utf8_bytes": len(data), "estimated_input_tokens_min": minimum, "estimated_input_tokens_max": maximum,
        "token_estimate_method": TOKEN_METHOD, "review_scope_sha256": None,
    }
    return task_with_hash(payload)


def repair_task(project_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    chapter_id = record["chapter_id"]
    packet_path = project_dir / "work" / "analysis" / f"{chapter_id.lower()}.packet.json"
    analysis_path = project_dir / "data" / "analysis" / f"{chapter_id.lower()}.analysis.json"
    if not packet_path.exists():
        prepare_packet(project_dir, chapter_id, replace=True)
    repair_path = project_dir / "work" / "pipeline" / f"repair-{chapter_id.lower()}.packet.json"
    packet = {
        "schema_version": "1.0.0", "task_type": "repair_chapter", "chapter_id": chapter_id,
        "analysis_path": relative(project_dir, analysis_path), "analysis_sha256": sha256(analysis_path.read_bytes()),
        "source_packet_path": relative(project_dir, packet_path), "source_packet_sha256": sha256(packet_path.read_bytes()),
        "errors": record["errors"], "rule": "只修复列出的错误及其直接依赖，不重做已通过章节。",
    }
    repair_data = write_task_packet(repair_path, packet)
    referenced_data = repair_data + packet_path.read_bytes() + analysis_path.read_bytes()
    minimum, maximum = estimate_tokens(referenced_data)
    payload = {
        "schema_version": "1.0.0", "task_type": "repair_chapter", "chapter_id": chapter_id, "character_id": None, "target_label": chapter_id,
        "input_path": relative(project_dir, repair_path), "input_sha256": sha256(repair_data),
        "output_path": relative(project_dir, analysis_path),
        "instructions": ["references/analysis_rules.md", "scripts/validate_analysis.py"], "reason": record["errors"],
        "input_utf8_bytes": len(referenced_data), "estimated_input_tokens_min": minimum, "estimated_input_tokens_max": maximum,
        "token_estimate_method": TOKEN_METHOD, "review_scope_sha256": None,
    }
    return task_with_hash(payload)


def recap_task_packet(project_dir: Path, recap_task: dict[str, Any], profile: dict[str, Any]) -> tuple[Path, bytes]:
    path = project_dir / "work" / "pipeline" / f"recap-{recap_task['character_id'].lower()}.packet.json"
    payload = {
        "schema_version": "1.0.0", "task_type": "review_character_profile",
        "character": {
            "character_id": profile["character_id"], "canonical_name": profile["canonical_name"],
            "chinese_name": profile["chinese_name"], "basic_info_summary_zh": profile["basic_info_summary_zh"],
        },
        "pending_fields": recap_task["fields"], "context_clues": recap_task["context_clues"],
        "rules": [
            "只使用本包已累积事实进行跨章复盘，不重新读取整本原文。",
            "能确认时追加 profile decision；仍无法确认时保留 unknown 并验收本角色复盘包。",
        ],
    }
    return path, write_task_packet(path, payload)


def profile_review_task(project_dir: Path, recap_task: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    path, data = recap_task_packet(project_dir, recap_task, profile)
    minimum, maximum = estimate_tokens(data)
    payload = {
        "schema_version": "1.0.0", "task_type": "review_character_profile", "chapter_id": None,
        "character_id": recap_task["character_id"],
        "target_label": (f"{profile['chinese_name']} / " if profile["chinese_name"] else "") + profile["canonical_name"],
        "input_path": relative(project_dir, path), "input_sha256": sha256(data),
        "output_path": "data/profiles/profile-decisions.jsonl",
        "instructions": ["references/review_workflow.md", "scripts/manage_profile_decisions.py", "scripts/run_pipeline.py ack-recap"],
        "reason": [f"{len(recap_task['fields'])} 项人物参数仍需全书复盘。"],
        "input_utf8_bytes": len(data), "estimated_input_tokens_min": minimum, "estimated_input_tokens_max": maximum,
        "token_estimate_method": TOKEN_METHOD, "review_scope_sha256": recap_task_hash(recap_task),
    }
    return task_with_hash(payload)


def render_progress(state: dict[str, Any]) -> str:
    ready = sum(item["status"] == "ready" for item in state["chapters"])
    lines = [
        "# 制作进度", "", f"**总体进度**：{ready}/{len(state['chapters'])} 章已完成结构化与中英渲染。", "",
        "| 章节 | 当前状态 | 待确认问题 |", "|---|---|---|",
    ]
    labels = {"waiting_analysis": "等待逐章分析", "needs_repair": "需要定向修复", "ready": "已完成"}
    for item in state["chapters"]:
        lines.append(f"| {item['chapter_id']} | {labels[item['status']]} | {item['pending_issue_count']} |")
    lines.extend(["", "## 下一步", ""])
    task = state["next_task"]
    if task is None:
        lines.extend(["全部章节与全书人物复盘均已完成。", ""])
    elif task["task_type"] == "analyze_chapter":
        lines.extend([f"分析 {task['chapter_id']}。系统只会加载这一章的工作包。", ""])
    elif task["task_type"] == "repair_chapter":
        lines.extend([f"定向修复 {task['chapter_id']} 的校验问题，不重跑其他章节。", ""])
    else:
        lines.extend([f"复盘角色 {task['target_label']} 的未决人物资料；只加载该角色的压缩线索包。", ""])
    metrics = state["last_run"]
    lines.extend([
        "## 本次运行的复用与成本控制", "",
        f"- 已直接复用章节：{metrics['reused_chapters']}；本次重建章节：{metrics['rebuilt_chapters']}。",
        f"- 已直接复用全局产物：{metrics['reused_global_outputs']}；本次重建全局产物：{metrics['rebuilt_global_outputs']}。",
    ])
    if task:
        lines.extend([
            f"- 下一份 Agent 输入：{metrics['agent_input_utf8_bytes']} UTF-8 字节。",
            f"- 粗略输入 Token 区间：{metrics['estimated_input_tokens_min']}–{metrics['estimated_input_tokens_max']}；实际值以所用模型分词器为准。",
        ])
    else:
        lines.append("- 当前没有新的 Agent 输入任务。")
    lines.append("")
    return "\n".join(lines)


def set_project_state(project_dir: Path, pipeline_status: str, ready_count: int, total: int) -> None:
    path = project_dir / "project.json"
    project = json.loads(path.read_text(encoding="utf-8"))
    if pipeline_status == "complete":
        value = "validated"
    elif ready_count == total and total:
        value = "rendered"
    elif ready_count:
        value = "structured"
    else:
        value = "ingested"
    if project.get("state") != value:
        project["state"] = value
        atomic_write(path, json_bytes(project))


def run_pipeline(project_dir: Path) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    ingest_errors = validate_project(project_dir)
    if ingest_errors:
        raise ValueError("ingest validation failed: " + "; ".join(ingest_errors))
    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    manifest_path = project_dir / project["source_manifest"]
    manifest_data = manifest_path.read_bytes()
    manifest = json.loads(manifest_data.decode("utf-8"))
    chapter_records: list[dict[str, Any]] = []
    ready_count = 0
    reused_chapters = 0
    rebuilt_chapters = 0
    invalid_records: list[dict[str, Any]] = []
    missing_records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for chapter in manifest["chapters"]:
        record, ready, rebuilt = chapter_record(project_dir, chapter)
        chapter_records.append(record)
        if ready:
            ready_count += 1
            rebuilt_chapters += int(rebuilt)
            reused_chapters += int(not rebuilt)
        elif record["status"] == "needs_repair":
            rebuilt_chapters += int(rebuilt)
            invalid_records.append(record)
        else:
            missing_records.append((record, chapter))

    global_outputs = {
        "registries": {"status": "not_available", "path": None, "sha256": None},
        "profiles": {"status": "not_available", "path": None, "sha256": None},
        "appearances": {"status": "not_available", "path": None, "sha256": None},
    }
    reused_globals = 0
    rebuilt_globals = 0
    if ready_count and not invalid_records:
        global_outputs, reused_globals, rebuilt_globals = ensure_globals(project_dir)

    next_task: dict[str, Any] | None
    if invalid_records:
        next_task = repair_task(project_dir, invalid_records[0])
        status = "chapter_needs_repair"
    elif missing_records:
        record, chapter = missing_records[0]
        packet_path = project_dir / "work" / "analysis" / f"{chapter['chapter_id'].lower()}.packet.json"
        before = packet_path.read_bytes() if packet_path.exists() else None
        prepare_packet(project_dir, chapter["chapter_id"], replace=True)
        after = packet_path.read_bytes()
        record["packet"] = file_input(project_dir, packet_path)
        if before != after:
            rebuilt_chapters += 1
        next_task = analyze_task(project_dir, chapter, packet_path)
        status = "waiting_for_chapter_analysis"
    else:
        profile_path = project_dir / "data" / "profiles" / "profiles.json"
        profile_bundle = json.loads(profile_path.read_text(encoding="utf-8"))
        profiles = {item["character_id"]: item for item in profile_bundle["profiles"]}
        events, _ = load_recap_events(project_dir)
        active, _ = active_recap_events(events)
        accepted = {(event["character_id"], event["task_sha256"]) for event in active}
        pending = [task for task in profile_bundle["recap_tasks"] if (task["character_id"], recap_task_hash(task)) not in accepted]
        if pending:
            next_task = profile_review_task(project_dir, pending[0], profiles[pending[0]["character_id"]])
            status = "waiting_for_profile_recap"
        else:
            next_task = None
            status = "complete"

    events, event_data = load_recap_events(project_dir)
    active, retracted = active_recap_events(events)
    task_bytes = next_task["input_utf8_bytes"] if next_task else 0
    task_min = next_task["estimated_input_tokens_min"] if next_task else 0
    task_max = next_task["estimated_input_tokens_max"] if next_task else 0
    state = {
        "schema_version": "1.0.0", "pipeline_version": PIPELINE_VERSION, "project_id": project["project_id"],
        "source_manifest": {"path": project["source_manifest"], "sha256": sha256(manifest_data)},
        "status": status, "chapters": chapter_records, "global_outputs": global_outputs,
        "recap_review_log": {
            "path": RECAP_EVENT_PATH, "sha256": sha256(event_data),
            "active_event_ids": [event["event_id"] for event in active], "retracted_event_ids": retracted,
        },
        "next_task": next_task,
        "last_run": {
            "reused_chapters": reused_chapters, "rebuilt_chapters": rebuilt_chapters,
            "reused_global_outputs": reused_globals, "rebuilt_global_outputs": rebuilt_globals,
            "agent_input_utf8_bytes": task_bytes, "estimated_input_tokens_min": task_min,
            "estimated_input_tokens_max": task_max, "token_estimate_method": TOKEN_METHOD,
        },
    }
    atomic_write(project_dir / STATE_PATH, json_bytes(state))
    if next_task:
        atomic_write(project_dir / TASK_PATH, json_bytes(next_task))
    else:
        task_path = project_dir / TASK_PATH
        if task_path.exists():
            task_path.unlink()
    atomic_write(project_dir / PROGRESS_PATH, render_progress(state).encode("utf-8"))
    set_project_state(project_dir, status, ready_count, len(chapter_records))
    return state


def append_recap_event(project_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    events, old_data = load_recap_events(project_dir)
    event = {
        "schema_version": "1.0.0", "event_id": f"RECAPREV-{len(events) + 1:06d}",
        "recorded_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "actor": {"type": "user", "label": payload.get("actor", "user")},
        "operation": payload["operation"], "character_id": payload.get("character_id"),
        "task_sha256": payload.get("task_sha256"), "note": payload["note"],
        "target_event_id": payload.get("target_event_id"),
    }
    path = project_dir / RECAP_EVENT_PATH
    atomic_write(path, old_data + json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
    try:
        load_recap_events(project_dir)
    except Exception:
        atomic_write(path, old_data)
        raise
    return event


def acknowledge_recap(project_dir: Path, character_id: str, note: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    state = run_pipeline(project_dir)
    task = state["next_task"]
    if not task or task["task_type"] != "review_character_profile" or task["character_id"] != character_id:
        raise ValueError(f"{character_id} is not the current profile recap task")
    event = append_recap_event(project_dir, {
        "operation": "accept_task", "character_id": character_id, "task_sha256": task["review_scope_sha256"],
        "note": note, "actor": actor,
    })
    return event, run_pipeline(project_dir)


def retract_recap(project_dir: Path, event_id: str, note: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    events, _ = load_recap_events(project_dir)
    target = next((event for event in events if event["event_id"] == event_id), None)
    if target is None or target["operation"] != "accept_task":
        raise ValueError(f"unknown accepted recap event: {event_id}")
    event = append_recap_event(project_dir, {"operation": "retract_event", "target_event_id": event_id, "note": note, "actor": actor})
    return event, run_pipeline(project_dir)


def print_status(state: dict[str, Any]) -> None:
    ready = sum(item["status"] == "ready" for item in state["chapters"])
    print(f"PIPELINE STATUS: {state['status']}; CHAPTERS: {ready}/{len(state['chapters'])}")
    task = state["next_task"]
    if task:
        target = task["target_label"]
        print(f"NEXT TASK: {task['task_type']} {target}")
        print(f"TASK INPUT: {task['input_path']}")
        print(f"ESTIMATED INPUT TOKENS: {task['estimated_input_tokens_min']}-{task['estimated_input_tokens_max']}")
    else:
        print("NEXT TASK: none")
    print(f"PROGRESS: {PROGRESS_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="启动、续跑和查看解说剧前筹总管线")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start", help="接收原文并生成第一份单章任务")
    start_parser.add_argument("source", type=Path)
    start_parser.add_argument("--project-dir", type=Path, required=True)
    start_parser.add_argument("--title")
    resume_parser = subparsers.add_parser("resume", help="从最近一个有效阶段继续")
    resume_parser.add_argument("project_dir", type=Path)
    status_parser = subparsers.add_parser("status", help="读取最近一次进度快照")
    status_parser.add_argument("project_dir", type=Path)
    ack_parser = subparsers.add_parser("ack-recap", help="验收当前单角色复盘包中的剩余未知项")
    ack_parser.add_argument("project_dir", type=Path)
    ack_parser.add_argument("--character", required=True)
    ack_parser.add_argument("--note", required=True)
    ack_parser.add_argument("--actor", default="user")
    retract_parser = subparsers.add_parser("retract-recap", help="撤销一次角色复盘验收")
    retract_parser.add_argument("project_dir", type=Path)
    retract_parser.add_argument("--event", required=True)
    retract_parser.add_argument("--note", required=True)
    retract_parser.add_argument("--actor", default="user")
    args = parser.parse_args()
    try:
        if args.command == "start":
            project_dir = ingest_source(args.source, args.project_dir, args.title)
            state = run_pipeline(project_dir)
            event = None
        elif args.command == "resume":
            state = run_pipeline(args.project_dir)
            event = None
        elif args.command == "status":
            state_path = args.project_dir.resolve() / STATE_PATH
            if not state_path.exists():
                raise ValueError("pipeline state is missing; run resume first")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            event = None
        elif args.command == "ack-recap":
            event, state = acknowledge_recap(args.project_dir.resolve(), args.character, args.note, args.actor)
        else:
            event, state = retract_recap(args.project_dir.resolve(), args.event, args.note, args.actor)
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"PIPELINE FAILED: {exc}")
        return 1
    if event:
        print(f"PIPELINE EVENT RECORDED: {event['event_id']}")
    print_status(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
