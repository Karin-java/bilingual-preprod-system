#!/usr/bin/env python3
"""Independently verify source, chapter and source-unit integrity."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_project(project_dir: Path) -> list[str]:
    project_dir = project_dir.resolve()
    errors: list[str] = []
    try:
        project = load_json(project_dir / "project.json")
        manifest_path = project_dir / project["source_manifest"]
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        return [f"project or manifest unreadable: {exc}"]

    if project.get("project_id") != manifest.get("project_id"):
        errors.append("project_id differs between project.json and source manifest")
    artifacts = {item.get("path"): item for item in project.get("artifacts", [])}
    manifest_artifact = artifacts.get("source/manifest.json")
    if not manifest_artifact or manifest_artifact.get("sha256") != digest(manifest_bytes):
        errors.append("source manifest artifact hash mismatch")

    source = manifest.get("source", {})
    canonical = manifest.get("canonical", {})
    try:
        source_bytes = (project_dir / source["copy_path"]).read_bytes()
        canonical_bytes = (project_dir / canonical["path"]).read_bytes()
        canonical_text = canonical_bytes.decode("utf-8")
    except (OSError, KeyError, UnicodeDecodeError) as exc:
        return errors + [f"source baseline unreadable: {exc}"]
    if len(source_bytes) != source.get("byte_length") or digest(source_bytes) != source.get("sha256"):
        errors.append("original source copy differs from manifest")
    if len(canonical_bytes) != canonical.get("byte_length") or digest(canonical_bytes) != canonical.get("sha256"):
        errors.append("canonical source differs from manifest")
    if len(canonical_text) != canonical.get("character_length"):
        errors.append("canonical character length mismatch")

    reconstructed_chapters: list[str] = []
    expected_start = 0
    seen_ids: set[str] = set()
    for chapter in manifest.get("chapters", []):
        chapter_id = chapter.get("chapter_id", "")
        if chapter_id in seen_ids:
            errors.append(f"duplicate chapter id: {chapter_id}")
        seen_ids.add(chapter_id)
        start, end = chapter.get("source_start"), chapter.get("source_end")
        if not isinstance(start, int) or not isinstance(end, int):
            errors.append(f"{chapter_id}: invalid source range")
            continue
        if start != expected_start or end <= start:
            errors.append(f"{chapter_id}: chapter ranges are not contiguous")
        expected_start = end
        try:
            chapter_bytes = (project_dir / chapter["source_path"]).read_bytes()
            chapter_text = chapter_bytes.decode("utf-8")
        except (OSError, KeyError, UnicodeDecodeError) as exc:
            errors.append(f"{chapter_id}: chapter unreadable: {exc}")
            continue
        if chapter_text != canonical_text[start:end]:
            errors.append(f"{chapter_id}: chapter is not the declared canonical slice")
        if digest(chapter_bytes) != chapter.get("sha256"):
            errors.append(f"{chapter_id}: chapter hash mismatch")
        if len(chapter_text) != chapter.get("character_length"):
            errors.append(f"{chapter_id}: chapter character length mismatch")
        reconstructed_chapters.append(chapter_text)

        unit_texts: list[str] = []
        try:
            unit_lines = (project_dir / chapter["units_path"]).read_text(encoding="utf-8").splitlines()
        except (OSError, KeyError, UnicodeDecodeError) as exc:
            errors.append(f"{chapter_id}: units unreadable: {exc}")
            continue
        if len(unit_lines) != chapter.get("unit_count"):
            errors.append(f"{chapter_id}: unit count mismatch")
        for ordinal, line in enumerate(unit_lines, 1):
            try:
                unit = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{chapter_id}: invalid JSONL row {ordinal}: {exc}")
                continue
            expected_id = f"{chapter_id}-U{ordinal:04d}"
            if unit.get("unit_id") != expected_id or unit.get("ordinal") != ordinal:
                errors.append(f"{chapter_id}: unstable unit identity at row {ordinal}")
            if unit.get("chapter_id") != chapter_id:
                errors.append(f"{chapter_id}: unit belongs to another chapter at row {ordinal}")
            text = unit.get("source_text")
            if not isinstance(text, str):
                errors.append(f"{chapter_id}: unit text is not a string at row {ordinal}")
                continue
            if digest(text.encode("utf-8")) != unit.get("source_sha256"):
                errors.append(f"{chapter_id}: unit hash mismatch at row {ordinal}")
            unit_texts.append(text)
        if "".join(unit_texts) != chapter_text:
            errors.append(f"{chapter_id}: units do not reconstruct the chapter")

    if expected_start != len(canonical_text):
        errors.append("chapter ranges do not cover the canonical source")
    if "".join(reconstructed_chapters) != canonical_text:
        errors.append("chapters do not reconstruct the canonical source")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验原文件、正文基线、拆章和原文单元完整性")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    errors = validate_project(args.project_dir)
    if errors:
        print("INGEST VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("INGEST VALIDATION PASSED: source, chapters and units reconstruct exactly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
