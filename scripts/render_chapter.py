#!/usr/bin/env python3
"""Render one validated V3 chapter and its human review sheet."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from preprod import quote_markdown, sha256, write_json
from review_analysis import load_current_analysis
from validate_analysis import validate_record

TIME_ZH = {
    "DAY": "日", "NIGHT": "夜", "DAWN": "黎明", "DUSK": "黄昏",
    "CONTINUOUS": "连续", "UNKNOWN": "时间待确认",
}
INT_EXT_ZH = {"INT": "内景", "EXT": "外景", "MIXED": "内外景", "UNKNOWN": "内外景待确认"}
KIND_ZH = {
    "heading": "题头", "narration": "叙述", "action": "动作",
    "dialogue": "对白", "thought": "内心", "separator": "原文分隔",
}


def _speaker_label(ref: str, characters: dict[str, dict[str, Any]]) -> str:
    if ref == "unknown":
        return "说话人待确认"
    item = characters.get(ref)
    if not item:
        return ref
    zh = item.get("name_zh")
    en = item.get("canonical_name_en")
    return f"{zh} / {en}" if zh else en


def _render_block(block: dict[str, Any], characters: dict[str, dict[str, Any]]) -> list[str]:
    if block["kind"] == "dialogue":
        label = _speaker_label(block["speaker_ref"], characters)
        lines = [f"**{label}（对白）**", "", "原文："]
    else:
        lines = [f"**{KIND_ZH[block['kind']]}**", "", "原文："]
    lines.extend(quote_markdown(block["text_en"]))
    if block["text_zh"]:
        lines.extend(["", "译文：", *quote_markdown(block["text_zh"])])
    lines.append("")
    return lines


def render_markdown(record: dict[str, Any]) -> str:
    chapter_id = record["chapter_id"]
    title = record.get("title_zh") or record.get("title_en") or chapter_id
    lines = [f"# {chapter_id}｜{title}", ""]
    if record.get("title_en"):
        lines.extend([f"**英文标题**：{record['title_en']}", ""])
    if record.get("pov"):
        lines.extend([f"**叙事视角**：{record['pov']}", ""])
    blocks = {item["block_id"]: item for item in record["blocks"]}
    characters = {item["ref"]: item for item in record["characters"]}
    if record["front_matter_block_ids"]:
        lines.extend(["## 章节题头", ""])
        for block_id in record["front_matter_block_ids"]:
            lines.extend(_render_block(blocks[block_id], characters))
    for scene in record["scenes"]:
        heading = f"{INT_EXT_ZH[scene['int_ext']]}·{scene['name_zh']}｜{TIME_ZH[scene['time']]}"
        lines.extend([
            f"## {scene['scene_id']}｜{heading}", "",
            f"**本场概括**：{scene['summary_zh']}", "",
        ])
        for beat in scene["beats"]:
            lines.extend([
                f"### {beat['beat_id']}｜{beat['title_zh']}", "",
                f"**情节变化**：{beat['summary_zh']}", "",
            ])
            for block_id in beat["block_ids"]:
                lines.extend(_render_block(blocks[block_id], characters))
    return "\n".join(lines).rstrip() + "\n"


def render_review(record: dict[str, Any]) -> str:
    chapter_id = record["chapter_id"]
    blocks = {item["block_id"]: item for item in record["blocks"]}
    pending = [item for item in record["issues"] if item["status"] == "pending"]
    lines = [f"# {chapter_id} 人工验收问题", "", "只需按问题编号回复；可直接补充或修改本章、某个 Scene 或某个 Beat。", ""]
    if not pending:
        lines.extend(["本章当前没有待确认问题。", ""])
        return "\n".join(lines)
    for issue in pending:
        lines.extend([
            "---", "", f"## {issue['issue_id']}", "",
            f"**位置**：{issue['scope_id']}", "",
            f"**需要确认**：{issue['question_zh']}", "",
            "**原文线索**：", "",
        ])
        for block_id in issue["evidence_block_ids"]:
            block = blocks[block_id]
            lines.extend([f"- {block_id}", "", *quote_markdown(block["text_en"]), "", "  中文参考：", "", *quote_markdown(block["text_zh"]), ""])
        lines.extend(["**用户答复**：", "", "（请填写）", ""])
    return "\n".join(lines).rstrip() + "\n"


def render_chapter(project_dir: Path, chapter_id: str) -> tuple[Path, Path, Path]:
    project_dir = project_dir.resolve()
    record, current_path, current_data = load_current_analysis(project_dir, chapter_id)
    errors = validate_record(project_dir, chapter_id, record)
    if errors:
        raise ValueError("chapter cannot be rendered: " + "; ".join(errors))
    script_path = project_dir / "deliverables" / "scripts_bilingual" / f"{chapter_id.lower()}.md"
    review_path = project_dir / "work" / "review" / f"{chapter_id.lower()}.review.md"
    script_data = render_markdown(record).encode("utf-8")
    review_data = render_review(record).encode("utf-8")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_bytes(script_data)
    review_path.write_bytes(review_data)
    manifest = {
        "schema_version": "3.0.0", "chapter_id": chapter_id,
        "chapter_input": {"path": current_path.relative_to(project_dir).as_posix(), "sha256": sha256(current_data)},
        "outputs": [
            {"path": script_path.relative_to(project_dir).as_posix(), "sha256": sha256(script_data)},
            {"path": review_path.relative_to(project_dir).as_posix(), "sha256": sha256(review_data)},
        ],
    }
    manifest_path = project_dir / "data" / "render" / f"{chapter_id.lower()}.json"
    write_json(manifest_path, manifest)
    return script_path, review_path, manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成单章中英标准剧本和用户验收问题")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--chapter", required=True)
    args = parser.parse_args()
    try:
        script, review, manifest = render_chapter(args.project_dir, args.chapter)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"RENDER FAILED: {exc}")
        return 1
    print(f"SCRIPT: {script}")
    print(f"REVIEW: {review}")
    print(f"MANIFEST: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
