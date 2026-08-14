#!/usr/bin/env python3
"""Deterministically render one bilingual chapter and its human review sheet."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from validate_analysis import validate_analysis

TYPE_LABELS = {
    "scene_heading": "场景标题",
    "action": "动作",
    "narration": "叙述",
    "dialogue": "对白",
    "inner_thought": "内心独白",
    "voice_over": "画外音",
    "letter": "信件",
    "on_screen_text": "画面文字",
    "transition": "转场",
    "other": "正文",
}
INT_EXT_LABELS = {"INT": "内景", "EXT": "外景", "INT_EXT": "内外景", "unknown": "内外景待确认"}
TIME_LABELS = {
    "day": "日", "night": "夜", "dawn": "黎明", "morning": "早晨", "noon": "正午",
    "afternoon": "下午", "dusk": "黄昏", "continuous": "连续", "unknown": "时间待确认",
}
REALITY_LABELS = {"present": "现实", "flashback": "回忆", "dream": "梦境", "imagined": "想象", "unknown": "现实层级待确认"}


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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_current_analysis(project_dir: Path, chapter_id: str) -> tuple[dict[str, Any], Path, bytes]:
    errors = validate_analysis(project_dir, chapter_id)
    if errors:
        raise ValueError("base analysis validation failed: " + "; ".join(errors))
    base_path = project_dir / "data" / "analysis" / f"{chapter_id.lower()}.analysis.json"
    base_data = base_path.read_bytes()
    resolved_path = project_dir / "data" / "views" / "analysis" / f"{chapter_id.lower()}.resolved.json"
    manifest_path = resolved_path.with_name(f"{chapter_id.lower()}.resolved.manifest.json")
    if not resolved_path.exists():
        return json.loads(base_data.decode("utf-8")), base_path, base_data
    if not manifest_path.exists():
        raise ValueError("resolved analysis exists without its manifest")
    manifest = load_json(manifest_path)
    events_path = project_dir / "data" / "reviews" / f"{chapter_id.lower()}.events.jsonl"
    events_data = events_path.read_bytes() if events_path.exists() else b""
    resolved_data = resolved_path.read_bytes()
    if manifest.get("base_analysis_sha256") != sha256(base_data):
        raise ValueError("resolved analysis is stale: base analysis hash changed")
    if manifest.get("events_sha256") != sha256(events_data):
        raise ValueError("resolved analysis is stale: review event hash changed")
    if manifest.get("resolved_analysis_sha256") != sha256(resolved_data):
        raise ValueError("resolved analysis hash mismatch")
    analysis = json.loads(resolved_data.decode("utf-8"))
    if analysis.get("chapter_id") != chapter_id:
        raise ValueError("resolved analysis belongs to another chapter")
    return analysis, resolved_path, resolved_data


def load_source_units(project_dir: Path, chapter_id: str) -> dict[str, str]:
    project = load_json(project_dir / "project.json")
    manifest = load_json(project_dir / project["source_manifest"])
    chapter = next((item for item in manifest["chapters"] if item["chapter_id"] == chapter_id), None)
    if chapter is None:
        raise ValueError(f"unknown chapter {chapter_id}")
    rows = (project_dir / chapter["units_path"]).read_text(encoding="utf-8").splitlines()
    return {row["unit_id"]: row["source_text"] for row in (json.loads(line) for line in rows)}


def segment_index(analysis: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_unit: dict[str, list[dict[str, Any]]] = {}
    for unit in analysis["unit_analyses"]:
        by_unit[unit["unit_id"]] = unit["segments"]
        for segment in unit["segments"]:
            by_id[segment["segment_id"]] = segment
    return by_id, by_unit


def quote_lines(text: str, prefix: str) -> list[str]:
    parts = text.splitlines() or [""]
    return [f"> **{prefix}**　{parts[0]}"] + [f"> {part}" for part in parts[1:]]


def render_segment(segment: dict[str, Any]) -> list[str]:
    kind = segment["text_type"]
    if kind == "chapter_heading":
        return []
    label = TYPE_LABELS[kind]
    speaker = segment.get("speaker")
    if speaker:
        en = speaker.get("canonical_label") or "Unknown speaker"
        zh = speaker.get("chinese_label") or "说话人待确认"
        label = f"{label}｜{en} / {zh}"
    source = segment["source_text"]
    translation = segment["translation"]["text_zh"]
    lines = [f"<!-- {segment['segment_id']} -->", f"**{label}**", ""]
    lines.extend(quote_lines(source, "EN"))
    if translation != source:
        lines.append(">")
        lines.extend(quote_lines(translation, "中"))
    lines.append("")
    return lines


def scene_heading(scene: dict[str, Any]) -> str:
    int_ext = INT_EXT_LABELS[scene["int_ext"]["value"]]
    location = scene["location"].get("standardized_name") or scene["location"].get("parent_location") or "地点待确认"
    time = TIME_LABELS[scene["time_of_day"]["value"]]
    parts = [scene["scene_id"], int_ext, location, time]
    reality = scene["reality_layer"]["value"]
    if reality != "present":
        parts.append(REALITY_LABELS[reality])
    return "｜".join(parts)


def render_script(analysis: dict[str, Any]) -> str:
    _, by_unit = segment_index(analysis)
    headings = [segment for unit in analysis["unit_analyses"] for segment in unit["segments"] if segment["text_type"] == "chapter_heading"]
    lines: list[str] = []
    if headings:
        first = headings[0]
        lines.extend([f"<!-- {first['segment_id']} -->", f"# {first['source_text']}", "", f"**{first['translation']['text_zh']}**", ""])
        for heading in headings[1:]:
            lines.extend([f"<!-- {heading['segment_id']} -->", f"**{heading['source_text']} / {heading['translation']['text_zh']}**", ""])
    else:
        lines.extend([f"# {analysis['chapter_id']}", ""])

    for scene in analysis["scenes"]:
        lines.extend([f"## {scene_heading(scene)}", "", scene["summary_zh"], ""])
        for beat in scene["beats"]:
            lines.extend([f"### {beat['beat_id']}｜{beat['summary_zh']}", ""])
            for unit_id in beat["source_unit_ids"]:
                for segment in by_unit.get(unit_id, []):
                    lines.extend(render_segment(segment))
    return "\n".join(lines).rstrip() + "\n"


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
    for observation in analysis["character_observations"]:
        result[("character_observation", observation["observation_id"])] = observation
    return result


def issue_evidence(issue: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    field = issue.get("field_path") or ""
    if issue["scope_type"] == "scene":
        root = field.split(".", 1)[0]
        container = target.get(root)
        if isinstance(container, dict):
            return container.get("evidence", [])
        return target.get("boundary_evidence", [])
    if issue["scope_type"] == "beat":
        return target.get("evidence", [])
    if issue["scope_type"] == "segment":
        root = field.split(".", 1)[0]
        container = target.get(root)
        if isinstance(container, dict):
            return container.get("evidence", [])
        return target.get("classification", {}).get("evidence", [])
    if issue["scope_type"] == "character_observation":
        return target.get("evidence", [])
    return []


def translated_context(unit_id: str, quote: str, by_unit: dict[str, list[dict[str, Any]]]) -> tuple[str, str]:
    for segment in by_unit.get(unit_id, []):
        if quote in segment["source_text"]:
            return segment["source_text"], segment["translation"]["text_zh"]
    segments = by_unit.get(unit_id, [])
    if not segments:
        return quote, "（该单元没有可用译文）"
    return " ".join(item["source_text"] for item in segments), " ".join(item["translation"]["text_zh"] for item in segments)


def render_review(analysis: dict[str, Any], source_units: dict[str, str]) -> str:
    del source_units  # source integrity is already verified; segment text supplies the bilingual context.
    _, by_unit = segment_index(analysis)
    targets = objects_by_scope(analysis)
    issues = analysis["review"]["issues"]
    lines = [
        f"# {analysis['chapter_id']} 人工验收清单",
        "",
        "请直接按问题编号回复“修改、补充、接受未知、暂不处理或撤销”。中文仅用于理解；系统会在内部将证据绑定到英文原文。",
        "",
    ]
    if not issues:
        lines.extend(["本章当前没有待确认事项。", ""])
        return "\n".join(lines)

    for issue in issues:
        target = targets[(issue["scope_type"], issue["scope_id"])]
        lines.extend([
            "---",
            "",
            f"## {issue['issue_id']}",
            "",
            f"**问题**：{issue['message']}",
            "",
            "### 原文线索（中英对照）",
            "",
        ])
        evidence = issue_evidence(issue, target)
        if not evidence:
            evidence = [{"source_unit_id": unit_id, "quote": by_unit.get(unit_id, [{}])[0].get("source_text", "")} for unit_id in issue["source_unit_ids"]]
        seen: set[tuple[str, str]] = set()
        clue_number = 0
        for item in evidence:
            unit_id, quote = item["source_unit_id"], item["quote"]
            key = (unit_id, quote)
            if key in seen:
                continue
            seen.add(key)
            clue_number += 1
            context_en, context_zh = translated_context(unit_id, quote, by_unit)
            lines.extend([f"#### 线索 {clue_number}", "", f"**英文原文**：{quote}", ""])
            if context_en != quote:
                lines.extend([f"**相关上下文**：{context_en}", ""])
            lines.extend([f"**中文参考**：{context_zh}", ""])
        lines.extend([
            "### 用户处理",
            "",
            "- 处理方式：",
            "- 确认值或补充内容：",
            "- 说明：",
            "",
        ])
    lines.extend(["---", ""])
    return "\n".join(lines).rstrip() + "\n"


def render_chapter(project_dir: Path, chapter_id: str) -> tuple[Path, Path, Path, dict[str, Any]]:
    project_dir = project_dir.resolve()
    analysis, analysis_path, analysis_data = load_current_analysis(project_dir, chapter_id)
    source_units = load_source_units(project_dir, chapter_id)
    script_data = render_script(analysis).encode("utf-8")
    review_data = render_review(analysis, source_units).encode("utf-8")
    script_path = project_dir / "deliverables" / "scripts_bilingual" / f"{chapter_id.lower()}.md"
    review_path = project_dir / "work" / "review" / f"{chapter_id.lower()}.review.md"
    manifest_path = project_dir / "data" / "render" / f"{chapter_id.lower()}.render-manifest.json"
    atomic_write(script_path, script_data)
    atomic_write(review_path, review_data)
    manifest = {
        "schema_version": "1.0.0",
        "renderer_version": "bilingual-markdown-v1",
        "chapter_id": chapter_id,
        "analysis_path": analysis_path.relative_to(project_dir).as_posix(),
        "analysis_sha256": sha256(analysis_data),
        "source_sha256": analysis["source_sha256"],
        "script_path": script_path.relative_to(project_dir).as_posix(),
        "script_sha256": sha256(script_data),
        "review_path": review_path.relative_to(project_dir).as_posix(),
        "review_sha256": sha256(review_data),
        "pending_issue_ids": [item["issue_id"] for item in analysis["review"]["issues"]],
    }
    atomic_write(manifest_path, json_bytes(manifest))
    return script_path, review_path, manifest_path, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="确定性生成单章中英 Markdown 剧本与双语验收清单")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True)
    args = parser.parse_args()
    try:
        script_path, review_path, manifest_path, manifest = render_chapter(args.project_dir, args.chapter)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"RENDER FAILED: {exc}")
        return 1
    print(f"BILINGUAL SCRIPT: {script_path}")
    print(f"REVIEW SHEET: {review_path}")
    print(f"PENDING ISSUES: {len(manifest['pending_issue_ids'])}")
    print(f"MANIFEST: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
