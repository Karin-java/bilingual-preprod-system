#!/usr/bin/env python3
"""Run the lightweight V3 coordinator; semantic work remains one bounded Agent task."""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

from build_catalogs import build_catalogs
from ingest_source import ingest_source
from prepare_analysis import prepare_packet
from preprod import PIPELINE_VERSION, find_chapter, load_project, project_relative, sha256, write_json
from render_chapter import render_chapter
from review_analysis import load_current_analysis
from validate_analysis import validate_analysis
from validate_catalogs import validate_catalogs
from validate_ingest import validate_project
from validate_render import validate_render

STATE_PATH = "data/pipeline/state.json"
TASK_PATH = "work/pipeline/next-task.json"
PROGRESS_PATH = "deliverables/制作进度.md"
CJK_RE = re.compile(r"[\u3400-\u9fff]")


def estimate_tokens(data: bytes) -> tuple[int, int]:
    text = data.decode("utf-8", errors="replace")
    cjk = len(CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return math.ceil(cjk + other / 4.5), math.ceil(cjk * 1.5 + other / 2.5)


def _task(project_dir: Path, chapter: dict[str, Any], packet_path: Path, packet_hash: str, task_type: str, errors: list[str] | None = None) -> dict[str, Any]:
    source_bytes = (project_dir / chapter["source_path"]).read_bytes()
    packet_bytes = packet_path.read_bytes()
    chapter_packet = project_dir / "work" / "chapters" / f"{chapter['chapter_id'].lower()}.packet.json"
    chapter_input_hash = sha256(chapter_packet.read_bytes()) if chapter_packet.exists() else packet_hash
    if task_type == "repair_chapter":
        failed_output = project_dir / "data" / "chapters" / f"{chapter['chapter_id'].lower()}.json"
        effective_bytes = packet_bytes + chapter_packet.read_bytes() + failed_output.read_bytes()
    else:
        effective_bytes = packet_bytes
    output_limit = len(source_bytes) * 4 + 12_288
    minimum, maximum = estimate_tokens(effective_bytes)
    return {
        "schema_version": "3.0.0", "task_type": task_type, "chapter_id": chapter["chapter_id"],
        "input_path": project_relative(project_dir, packet_path), "input_sha256": packet_hash,
        "chapter_input_sha256": chapter_input_hash,
        "output_path": f"data/chapters/{chapter['chapter_id'].lower()}.json",
        "schema_path": "schemas/chapter.schema.json",
        "instructions": ("只处理这一章；直接写入 output_path。分析任务把 chapter_input_sha256 写入章节结果；修复任务保留该值不变。不得自行创建替代流程、批量请求脚本或要求用户配置外部模型 API。"),
        "validation_command": f"python scripts/validate_analysis.py \"{project_dir}\" --chapter {chapter['chapter_id']}",
        "errors": errors or [],
        "budget": {
            "source_utf8_bytes": len(source_bytes), "packet_utf8_bytes": len(packet_bytes), "effective_input_utf8_bytes": len(effective_bytes),
            "rule_and_context_overhead_bytes": len(packet_bytes) - len(source_bytes),
            "output_utf8_bytes_max": output_limit,
            "estimated_input_tokens_min": minimum, "estimated_input_tokens_max": maximum,
        },
    }


def _repair_packet(project_dir: Path, chapter: dict[str, Any], errors: list[str]) -> tuple[Path, str]:
    chapter_id = chapter["chapter_id"]
    original_packet = project_dir / "work" / "chapters" / f"{chapter_id.lower()}.packet.json"
    output_path = project_dir / "data" / "chapters" / f"{chapter_id.lower()}.json"
    payload = {
        "packet_version": "3.0.0", "task_type": "repair_chapter", "chapter_id": chapter_id,
        "rule": "只修复 errors 及其直接依赖；英文 text_en 仍须逐字符重组原章。",
        "errors": errors,
        "source_packet": {"path": project_relative(project_dir, original_packet), "sha256": sha256(original_packet.read_bytes())},
        "failed_output": {"path": project_relative(project_dir, output_path), "sha256": sha256(output_path.read_bytes())},
    }
    path = project_dir / "work" / "chapters" / f"{chapter_id.lower()}.repair.json"
    data = write_json(path, payload, compact=True)
    return path, sha256(data)


def _render_progress(state: dict[str, Any]) -> str:
    ready = sum(item["status"] == "ready" for item in state["chapters"])
    labels = {"waiting": "等待分析", "repair": "需要定向修复", "ready": "已完成", "blocked": "输出不可读"}
    lines = [
        "# 制作进度", "",
        f"**章节进度**：{ready}/{len(state['chapters'])}", "",
        f"**当前状态**：{state['status']}", "",
        "| 章节 | 状态 | 待确认问题 |", "|---|---|---|",
    ]
    for item in state["chapters"]:
        lines.append(f"| {item['chapter_id']} | {labels[item['status']]} | {item['pending_issue_count']} |")
    lines.extend(["", "## 下一步", ""])
    task = state.get("next_task")
    if task:
        action = "完成本章分析" if task["task_type"] == "analyze_chapter" else "按错误清单定向修复本章"
        lines.extend([
            f"{action}：{task['chapter_id']}。本任务只读取当前章，不需要用户配置外部模型 API。", "",
            f"- 输入规模：{task['budget']['packet_utf8_bytes']} 字节",
            f"- 规则与上下文开销：{task['budget']['rule_and_context_overhead_bytes']} 字节",
            f"- 预计输入 Token：{task['budget']['estimated_input_tokens_min']}–{task['budget']['estimated_input_tokens_max']}",
            f"- 输出上限：{task['budget']['output_utf8_bytes_max']} 字节", "",
        ])
    elif state["status"] == "ready_for_review":
        lines.extend(["全部章节已处理。请审核待确认问题和全文美术资产候选清单。", ""])
    else:
        lines.extend(["全部章节和人工决策均已完成，可进入制作。", ""])
    metrics = state.get("catalog_metrics", {})
    lines.extend([
        "## 本次本地处理", "",
        f"- 复用章节事实缓存：{metrics.get('reused_chapters', 0)}",
        f"- 更新章节事实缓存：{metrics.get('updated_chapters', 0)}",
        "- 全局合并、表格和 Markdown 均由本地确定性程序生成，不调用模型。", "",
    ])
    return "\n".join(lines)


def _set_project_state(project_dir: Path, status: str) -> None:
    path = project_dir / "project.json"
    project = json.loads(path.read_text(encoding="utf-8"))
    project["pipeline_version"] = PIPELINE_VERSION
    project["state"] = "validated" if status == "complete" else "structured" if status == "ready_for_review" else "ingested"
    write_json(path, project)


def run_pipeline(project_dir: Path) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    ingest_errors = validate_project(project_dir)
    if ingest_errors:
        raise ValueError("ingest validation failed: " + "; ".join(ingest_errors))
    _, manifest = load_project(project_dir)
    chapter_states: list[dict[str, Any]] = []
    next_task = None
    catalog_metrics = {"reused_chapters": 0, "updated_chapters": 0}
    catalog = None
    ready_ids: list[str] = []
    stopped = False
    for chapter in manifest["chapters"]:
        chapter_id = chapter["chapter_id"]
        output = project_dir / "data" / "chapters" / f"{chapter_id.lower()}.json"
        if stopped:
            chapter_states.append({"chapter_id": chapter_id, "status": "waiting", "pending_issue_count": 0, "errors": []})
            continue
        if not output.exists():
            if ready_ids:
                catalog, catalog_metrics = build_catalogs(project_dir)
            packet_path, packet_hash = prepare_packet(project_dir, chapter_id, replace=True)
            next_task = _task(project_dir, chapter, packet_path, packet_hash, "analyze_chapter")
            chapter_states.append({"chapter_id": chapter_id, "status": "waiting", "pending_issue_count": 0, "errors": []})
            stopped = True
            continue
        original_packet = project_dir / "work" / "chapters" / f"{chapter_id.lower()}.packet.json"
        if not original_packet.exists():
            prepare_packet(project_dir, chapter_id, replace=True)
        errors = validate_analysis(project_dir, chapter_id)
        if errors:
            repair_path, repair_hash = _repair_packet(project_dir, chapter, errors)
            next_task = _task(project_dir, chapter, repair_path, repair_hash, "repair_chapter", errors)
            chapter_states.append({"chapter_id": chapter_id, "status": "repair", "pending_issue_count": 0, "errors": errors})
            stopped = True
            continue
        record, _, _ = load_current_analysis(project_dir, chapter_id)
        if validate_render(project_dir, chapter_id):
            render_chapter(project_dir, chapter_id)
        render_errors = validate_render(project_dir, chapter_id)
        if render_errors:
            raise ValueError("render failed: " + "; ".join(render_errors))
        pending = sum(issue["status"] == "pending" for issue in record["issues"])
        chapter_states.append({"chapter_id": chapter_id, "status": "ready", "pending_issue_count": pending, "errors": []})
        ready_ids.append(chapter_id)

    if ready_ids:
        if catalog is None:
            catalog, catalog_metrics = build_catalogs(project_dir)
        catalog_errors = validate_catalogs(project_dir)
        if catalog_errors:
            raise ValueError("catalog validation failed: " + "; ".join(catalog_errors))
    if next_task:
        status = "waiting_for_agent" if next_task["task_type"] == "analyze_chapter" else "needs_repair"
    else:
        pending_issues = len(catalog["pending_issues"]) if catalog else 0
        pending_assets = sum(not item.get("merged_into") and item["status"] in {"pending", "information_pending"} for item in (catalog or {}).get("assets", []))
        status = "complete" if pending_issues == 0 and pending_assets == 0 else "ready_for_review"
    state = {
        "schema_version": "3.0.0", "pipeline_version": PIPELINE_VERSION,
        "status": status, "chapters": chapter_states, "next_task": next_task,
        "catalog_metrics": catalog_metrics,
        "review_summary": {
            "pending_issues": len(catalog["pending_issues"]) if catalog else 0,
            "pending_assets": sum(not item.get("merged_into") and item["status"] in {"pending", "information_pending"} for item in (catalog or {}).get("assets", [])),
        },
    }
    write_json(project_dir / STATE_PATH, state)
    task_path = project_dir / TASK_PATH
    if next_task:
        write_json(task_path, next_task)
    else:
        task_path.unlink(missing_ok=True)
    (project_dir / "deliverables").mkdir(parents=True, exist_ok=True)
    (project_dir / PROGRESS_PATH).write_text(_render_progress(state), encoding="utf-8", newline="\n")
    _set_project_state(project_dir, status)
    return state


def print_status(state: dict[str, Any]) -> None:
    ready = sum(item["status"] == "ready" for item in state["chapters"])
    print(f"PIPELINE STATUS: {state['status']}; CHAPTERS: {ready}/{len(state['chapters'])}")
    task = state.get("next_task")
    if task:
        print(f"NEXT TASK: {task['task_type']} {task['chapter_id']}")
        print(f"TASK INPUT: {task['input_path']}")
        print(f"INPUT SIZE: {task['budget']['packet_utf8_bytes']} bytes; OVERHEAD: {task['budget']['rule_and_context_overhead_bytes']} bytes")
    else:
        print("NEXT TASK: none")
    print(f"REVIEW: {state['review_summary']['pending_issues']} issues; {state['review_summary']['pending_assets']} asset decisions")


def main() -> int:
    parser = argparse.ArgumentParser(description="启动或继续 V3 轻量前筹流程")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("source", type=Path)
    start.add_argument("--project-dir", type=Path, required=True)
    start.add_argument("--title")
    resume = sub.add_parser("resume")
    resume.add_argument("project_dir", type=Path)
    status = sub.add_parser("status")
    status.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "start":
            project_dir = ingest_source(args.source, args.project_dir, args.title)
            state = run_pipeline(project_dir)
        elif args.command == "resume":
            state = run_pipeline(args.project_dir)
        else:
            state = json.loads((args.project_dir.resolve() / STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"PIPELINE FAILED: {exc}")
        return 1
    print_status(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
