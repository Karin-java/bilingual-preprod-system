"""Deterministic navigation report for completed core pre-production facts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from build_registries import atomic_write, sha256


def descriptor(project_dir: Path, path: Path, data_type: str) -> dict[str, str]:
    value = load_json(path)
    return {
        "path": path.relative_to(project_dir).as_posix(), "sha256": sha256(path.read_bytes()),
        "data_type": data_type, "schema_version": value["schema_version"],
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def prepare(project_dir: Path, manifest: dict[str, Any], target: str | None) -> dict[str, Any]:
    if target is not None:
        raise ValueError("reports extension does not accept a target")
    paths = {
        "project": project_dir / "project.json",
        "manifest": project_dir / "source" / "manifest.json",
        "registry": project_dir / "data" / "registries" / "entities.json",
        "profiles": project_dir / "data" / "profiles" / "profiles.json",
        "appearances": project_dir / "data" / "appearances" / "appearances.json",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError(f"reports extension requires core output: {name}")
    project = load_json(paths["project"])
    source_manifest = load_json(paths["manifest"])
    registry = load_json(paths["registry"])
    profiles = load_json(paths["profiles"])
    appearances = load_json(paths["appearances"])
    input_types = {
        "project": "project", "manifest": "source-manifest", "registry": "entity-registry",
        "profiles": "profile-bundle", "appearances": "appearance-bundle",
    }
    inputs = [descriptor(project_dir, path, input_types[name]) for name, path in paths.items()]
    analyses: list[dict[str, Any]] = []
    for chapter in source_manifest["chapters"]:
        chapter_id = chapter["chapter_id"]
        resolved = project_dir / "data" / "views" / "analysis" / f"{chapter_id.lower()}.resolved.json"
        base = project_dir / "data" / "analysis" / f"{chapter_id.lower()}.analysis.json"
        analysis_path = resolved if resolved.is_file() else base
        if not analysis_path.is_file():
            raise ValueError(f"reports extension requires completed chapter analysis: {chapter_id}")
        analyses.append(load_json(analysis_path))
        inputs.append(descriptor(project_dir, analysis_path, "chapter-analysis"))
    pipeline_path = project_dir / "data" / "pipeline" / "state.json"
    pipeline = load_json(pipeline_path) if pipeline_path.is_file() else None
    if pipeline_path.is_file():
        inputs.append(descriptor(project_dir, pipeline_path, "pipeline-state"))

    profile_pending = sum(
        field["status"] in {"unknown", "conflict"}
        for profile in profiles["profiles"] for field in profile["fields"].values()
    )
    review_pending = sum(len(analysis.get("review", {}).get("issues", [])) for analysis in analyses)
    major_ids = {row["character_id"] for row in appearances.get("major_character_scenes", [])}
    lines = [
        "# 前筹总览", "",
        f"**项目**：{project['title']}", "",
        "本页只整理完成状态和交付入口，不复制已有剧本、角色表或人物档案。", "",
        "## 当前规模", "",
        "| 项目 | 数量 |", "|---|---:|",
        f"| 章节 | {len(source_manifest['chapters'])} |",
        f"| 场景 | {sum(len(item['scenes']) for item in analyses)} |",
        f"| 登记角色 | {len(registry['characters'])} |",
        f"| 主要角色候选 | {len(major_ids)} |",
        f"| 章节待确认问题 | {review_pending} |",
        f"| 人物参数待确认或冲突 | {profile_pending} |", "",
        "## 交付文件", "",
        "| 内容 | 文件 |", "|---|---|",
    ]
    for chapter in source_manifest["chapters"]:
        chapter_id = chapter["chapter_id"]
        script_path = project_dir / "deliverables" / "scripts_bilingual" / f"{chapter_id.lower()}.md"
        if script_path.is_file():
            lines.append(f"| {chapter_id} 中英标准剧本 | [打开](scripts_bilingual/{chapter_id.lower()}.md) |")
    fixed = [
        ("全角色章节出镜表", "全角色章节出镜表.md"),
        ("主要角色场景统计", "主要角色场景统计.md"),
        ("角色基础信息档案", "角色基础信息档案.md"),
        ("制作进度", "制作进度.md"),
    ]
    for label, relative in fixed:
        if (project_dir / "deliverables" / relative).is_file():
            lines.append(f"| {label} | [打开]({relative}) |")
    lines.extend(["", "## 状态说明", ""])
    if pipeline:
        status_label = "核心流程已完成" if pipeline.get("status") == "complete" else "核心流程仍有待办"
        lines.append(f"- {status_label}。")
    if review_pending or profile_pending:
        lines.append("- 待确认项仍保留在原有验收与复盘文件中，本页不替代人工结论。")
    else:
        lines.append("- 当前事实层没有未解决的章节问题或人物参数冲突。")
    lines.append("")
    output = project_dir / "deliverables" / "前筹总览.md"
    data = "\n".join(lines).encode("utf-8")
    atomic_write(output, data)
    return {
        "target": None,
        "inputs": inputs,
        "artifacts": [{
            "path": output.relative_to(project_dir).as_posix(), "sha256": sha256(data),
            "artifact_type": "preproduction-overview", "format": "markdown",
        }],
        "task": None,
        "summary_zh": f"已整理 {len(source_manifest['chapters'])} 章的交付入口；未复制既有内容。",
    }
