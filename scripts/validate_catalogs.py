#!/usr/bin/env python3
"""Validate V3 global catalogs and user-facing derived files."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from build_catalogs import (
    _alias_index,
    evidence_lookup,
    render_appearance_matrix,
    render_assets,
    render_character_library,
    render_issues,
    render_location_library,
    render_major_appearances,
)


def validate_catalogs(project_dir: Path) -> list[str]:
    project_dir = project_dir.resolve()
    try:
        catalog = json.loads((project_dir / "data" / "catalogs" / "catalog.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"catalog unreadable: {exc}"]
    errors: list[str] = []
    if catalog.get("schema_version") != "3.0.0":
        errors.append("catalog schema_version must be 3.0.0")
    char_ids = [item.get("character_id") for item in catalog.get("characters", [])]
    loc_ids = [item.get("location_id") for item in catalog.get("locations", [])]
    asset_ids = [item.get("asset_id") for item in catalog.get("assets", [])]
    if len(char_ids) != len(set(char_ids)) or any(not re.fullmatch(r"CHAR-[0-9]{4}", str(value)) for value in char_ids):
        errors.append("character IDs are invalid or duplicated")
    if len(loc_ids) != len(set(loc_ids)) or any(not re.fullmatch(r"LOC-[0-9]{4}", str(value)) for value in loc_ids):
        errors.append("location IDs are invalid or duplicated")
    if len(asset_ids) != len(set(asset_ids)) or any(not re.fullmatch(r"ASSET-[0-9]{4}", str(value)) for value in asset_ids):
        errors.append("asset IDs are invalid or duplicated")
    for old_id, target_id in catalog.get("character_redirects", {}).items():
        if target_id not in char_ids or old_id == target_id:
            errors.append(f"invalid character redirect: {old_id} -> {target_id}")
    for old_id, target_id in catalog.get("location_redirects", {}).items():
        if target_id not in loc_ids or old_id == target_id:
            errors.append(f"invalid location redirect: {old_id} -> {target_id}")
    active_asset_ids = {item["asset_id"] for item in catalog.get("assets", []) if not item.get("merged_into")}
    for old_id, target_id in catalog.get("asset_redirects", {}).items():
        if target_id not in active_asset_ids or old_id == target_id:
            errors.append(f"invalid asset redirect: {old_id} -> {target_id}")
    char_index, ambiguous_char = _alias_index(catalog.get("characters", []), "character_id", ["canonical_name_en", "name_zh", "aliases_en", "aliases_zh"])
    loc_index, ambiguous_loc = _alias_index(catalog.get("locations", []), "location_id", ["name_en", "name_zh", "aliases_en", "aliases_zh"])
    if catalog.get("character_alias_index") != char_index or catalog.get("ambiguous_character_aliases") != ambiguous_char:
        errors.append("character alias index is stale")
    if catalog.get("location_alias_index") != loc_index or catalog.get("ambiguous_location_aliases") != ambiguous_loc:
        errors.append("location alias index is stale")
    expected = {
        "角色信息库.md": render_character_library(catalog),
        "场景信息库.md": render_location_library(catalog),
        "全角色章节出镜表.md": render_appearance_matrix(catalog, sorted(catalog.get("chapter_inputs", {}), key=lambda value: int(value[1:]))),
        "主要角色场景统计.md": render_major_appearances(catalog),
        "全文美术资产候选清单.md": render_assets(catalog),
        "待确认问题汇总.md": render_issues(catalog, evidence_lookup(project_dir, sorted(catalog.get("chapter_inputs", {}), key=lambda value: int(value[1:])))),
    }
    for filename, content in expected.items():
        path = project_dir / "deliverables" / filename
        try:
            actual = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"{filename} unreadable: {exc}")
            continue
        if actual != content:
            errors.append(f"{filename} is stale or modified")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验全剧角色、场景、出镜和资产清单")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    errors = validate_catalogs(args.project_dir)
    if errors:
        print("CATALOG VALIDATION FAILED:")
        for error in errors:
            print(f" - {error}")
        return 1
    print("CATALOG VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
