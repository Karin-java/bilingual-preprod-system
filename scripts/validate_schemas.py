#!/usr/bin/env python3
"""Validate bundled JSON Schemas and their local file references."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def collect_refs(value: Any) -> list[str]:
    if isinstance(value, dict):
        refs = [value["$ref"]] if isinstance(value.get("$ref"), str) else []
        for child in value.values():
            refs.extend(collect_refs(child))
        return refs
    if isinstance(value, list):
        refs: list[str] = []
        for child in value:
            refs.extend(collect_refs(child))
        return refs
    return []


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    schema_dir = root / "schemas"
    files = sorted(schema_dir.glob("*.json"))
    if not files:
        print("FAILED: no schema files found")
        return 1

    documents: dict[str, dict[str, Any]] = {}
    try:
        for path in files:
            documents[path.name] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAILED: {exc}")
        return 1

    missing: set[str] = set()
    for document in documents.values():
        for ref in collect_refs(document):
            local_file = ref.split("#", 1)[0]
            if local_file and local_file not in documents:
                missing.add(local_file)
    if missing:
        print("FAILED: missing schema references:", ", ".join(sorted(missing)))
        return 1

    try:
        import jsonschema
    except ImportError:
        print(f"PASSED: {len(files)} schemas are valid JSON; local references resolve.")
        print("NOTICE: jsonschema is unavailable; meta-schema validation was skipped.")
        return 0

    try:
        for document in documents.values():
            jsonschema.Draft202012Validator.check_schema(document)
    except jsonschema.SchemaError as exc:
        print(f"FAILED: invalid Draft 2020-12 schema: {exc.message}")
        return 1

    print(f"PASSED: {len(files)} schemas conform to JSON Schema Draft 2020-12; local references resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
