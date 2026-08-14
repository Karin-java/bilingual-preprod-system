#!/usr/bin/env python3
"""Validate one or every prepared extension for a project."""
from __future__ import annotations

import argparse
from pathlib import Path

from run_extension import discover, validate_extension


def main() -> int:
    parser = argparse.ArgumentParser(description="校验可选扩展的清单、核心输入指纹和独立产物")
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--module")
    args = parser.parse_args()
    modules = [args.module] if args.module else [item["module_id"] for item in discover()]
    failed = False
    for module_id in modules:
        errors = validate_extension(args.project_dir, module_id)
        if errors:
            failed = True
            print(f"{module_id}:")
            for error in errors:
                print(f" - {error}")
        else:
            print(f"{module_id}: PASSED")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
