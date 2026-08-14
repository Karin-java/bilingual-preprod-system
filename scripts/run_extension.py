#!/usr/bin/env python3
"""Discover, prepare and validate optional pre-production extensions."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from build_registries import atomic_write, json_bytes, sha256

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS_ROOT = ROOT / "extensions"
TOKEN_METHOD = "mixed-character-heuristic-v1"
CORE_INPUT_PREFIXES = ("source/", "data/", "project.json")


def estimate_tokens(data: bytes) -> tuple[int, int]:
    text = data.decode("utf-8")
    cjk = sum("\u3400" <= char <= "\u9fff" for char in text)
    other = len(text) - cjk
    return max(1, int(cjk * 0.8 + other / 5)), max(1, int(cjk * 1.25 + other / 3.2) + 1)


def manifest_path(module_id: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", module_id):
        raise ValueError(f"invalid extension module ID: {module_id}")
    return EXTENSIONS_ROOT / module_id / "extension.json"


def load_manifest(module_id: str) -> dict[str, Any]:
    path = manifest_path(module_id)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read extension manifest for {module_id}: {exc}") from exc
    required = {
        "manifest_version", "module_id", "module_version", "display_name", "execution_mode",
        "consumes", "produces", "output_namespace", "delivery_path_patterns", "entrypoint",
    }
    if not required.issubset(manifest) or manifest.get("module_id") != module_id:
        raise ValueError(f"invalid extension manifest for {module_id}")
    if manifest["execution_mode"] not in {"deterministic", "agent_task"}:
        raise ValueError(f"invalid execution mode for {module_id}")
    if manifest["output_namespace"] != f"extensions/{module_id}/":
        raise ValueError(f"extension namespace does not match module ID: {module_id}")
    return manifest


def discover() -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in sorted(EXTENSIONS_ROOT.glob("*/extension.json")):
        manifests.append(load_manifest(path.parent.name))
    return manifests


def load_entrypoint(manifest: dict[str, Any]) -> Callable[[Path, dict[str, Any], str | None], dict[str, Any]]:
    relative_path, function_name = manifest["entrypoint"].split(":", 1)
    path = (ROOT / relative_path).resolve()
    if ROOT not in path.parents or not path.is_file() or function_name != "prepare":
        raise ValueError(f"unsafe or missing extension entrypoint: {manifest['entrypoint']}")
    spec = importlib.util.spec_from_file_location(f"preprod_extension_{manifest['module_id']}", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load extension entrypoint: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, function_name, None)
    if not callable(function):
        raise ValueError(f"extension entrypoint is not callable: {manifest['entrypoint']}")
    return function


def normalized_path(project_dir: Path, value: str) -> Path:
    path = (project_dir / value).resolve()
    if path != project_dir and project_dir not in path.parents:
        raise ValueError(f"extension path escapes project: {value}")
    return path


def input_fingerprint(manifest: dict[str, Any], target: Any, inputs: list[dict[str, Any]]) -> str:
    payload = {
        "module_id": manifest["module_id"], "module_version": manifest["module_version"],
        "target": target, "inputs": inputs,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def output_allowed(manifest: dict[str, Any], path: str) -> bool:
    if path.startswith(manifest["output_namespace"]):
        return True
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in manifest["delivery_path_patterns"])


def validate_result(project_dir: Path, manifest: dict[str, Any], result: dict[str, Any]) -> None:
    if not isinstance(result.get("inputs"), list) or not result["inputs"]:
        raise ValueError("extension returned no input descriptors")
    consumed: dict[str, list[str]] = {}
    for descriptor in result["inputs"]:
        path = descriptor.get("path", "")
        if not path.startswith(CORE_INPUT_PREFIXES):
            raise ValueError(f"extension declared a non-core input: {path}")
        source = normalized_path(project_dir, path)
        if not source.is_file() or sha256(source.read_bytes()) != descriptor.get("sha256"):
            raise ValueError(f"extension input is missing or stale: {path}")
        data_type = descriptor.get("data_type")
        version = descriptor.get("schema_version")
        if not isinstance(data_type, str) or not isinstance(version, str) or not re.fullmatch(r"[0-9]+[.][0-9]+[.][0-9]+", version):
            raise ValueError(f"extension input lacks data type or schema version: {path}")
        consumed.setdefault(data_type, []).append(version)
    for requirement in manifest["consumes"]:
        versions = consumed.get(requirement["data_type"], [])
        if requirement.get("required", True) and not versions:
            raise ValueError(f"extension is missing required input type: {requirement['data_type']}")
        major_match = re.fullmatch(r"([0-9]+)[.]x", requirement["version_range"])
        if not major_match:
            raise ValueError(f"unsupported extension version range: {requirement['version_range']}")
        expected_major = major_match.group(1)
        if any(version.split(".")[0] != expected_major for version in versions):
            raise ValueError(f"extension input version is incompatible: {requirement['data_type']}")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("extension returned no artifact")
    for artifact in artifacts:
        path = artifact.get("path", "")
        if not output_allowed(manifest, path):
            raise ValueError(f"extension output was not declared by its manifest: {path}")
        output = normalized_path(project_dir, path)
        if not output.is_file() or sha256(output.read_bytes()) != artifact.get("sha256"):
            raise ValueError(f"extension artifact is missing or stale: {path}")
    task = result.get("task")
    if manifest["execution_mode"] == "agent_task":
        if not isinstance(task, dict) or not output_allowed(manifest, task.get("output_path", "")):
            raise ValueError("agent extension did not declare a valid output task")
    elif task is not None:
        raise ValueError("deterministic extension unexpectedly returned an Agent task")


def state_path(project_dir: Path, manifest: dict[str, Any]) -> Path:
    return project_dir / manifest["output_namespace"] / "run.json"


def prepare_extension(project_dir: Path, module_id: str, target: str | None = None) -> tuple[dict[str, Any], bool]:
    project_dir = project_dir.resolve()
    project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    manifest = load_manifest(module_id)
    result = load_entrypoint(manifest)(project_dir, manifest, target)
    validate_result(project_dir, manifest, result)
    fingerprint = input_fingerprint(manifest, result.get("target"), result["inputs"])
    old_state: dict[str, Any] | None = None
    path = state_path(project_dir, manifest)
    if path.exists():
        try:
            old_state = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            old_state = None
    reused = bool(old_state and old_state.get("input_fingerprint") == fingerprint and old_state.get("artifacts") == result["artifacts"] and old_state.get("task") == result.get("task"))
    generated_at = old_state["generated_at"] if reused else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state = {
        "schema_version": "1.0.0", "project_id": project["project_id"],
        "module_id": module_id, "module_version": manifest["module_version"],
        "execution_mode": manifest["execution_mode"], "generated_at": generated_at,
        "status": "ready_for_agent" if result.get("task") else "complete",
        "target": result.get("target"), "input_fingerprint": fingerprint,
        "inputs": result["inputs"], "artifacts": result["artifacts"],
        "task": result.get("task"), "summary_zh": result["summary_zh"],
    }
    atomic_write(path, json_bytes(state))
    return state, reused


def validate_extension(project_dir: Path, module_id: str) -> list[str]:
    project_dir = project_dir.resolve()
    errors: list[str] = []
    try:
        manifest = load_manifest(module_id)
        path = state_path(project_dir, manifest)
        state = json.loads(path.read_text(encoding="utf-8"))
        project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [str(exc)]
    if state.get("schema_version") != "1.0.0" or state.get("project_id") != project.get("project_id"):
        errors.append("extension state version or project ID is invalid")
    if state.get("module_id") != module_id or state.get("module_version") != manifest.get("module_version"):
        errors.append("extension state does not match its manifest")
    expected_status = "ready_for_agent" if state.get("task") else "complete"
    if state.get("status") != expected_status:
        errors.append("extension status does not match its task")
    try:
        validate_result(project_dir, manifest, state)
    except ValueError as exc:
        errors.append(str(exc))
    if input_fingerprint(manifest, state.get("target"), state.get("inputs", [])) != state.get("input_fingerprint"):
        errors.append("extension input fingerprint is invalid")
    return errors


def print_state(state: dict[str, Any], reused: bool | None = None) -> None:
    suffix = "; REUSED" if reused else "" if reused is not None else ""
    print(f"EXTENSION: {state['module_id']}; STATUS: {state['status']}{suffix}")
    print(f"SUMMARY: {state['summary_zh']}")
    if state.get("task"):
        task = state["task"]
        print(f"NEXT TASK: {task['target_id']}; {task['estimated_input_tokens_min']}-{task['estimated_input_tokens_max']} estimated input tokens")
        print(f"TASK INPUT: {task['input_path']}")
        print(f"EXPECTED OUTPUT: {task['output_path']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行按需前筹扩展，不修改核心事实层")
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("list", help="列出可用扩展")
    prepare = subs.add_parser("prepare", help="生成扩展产物或一个有界 Agent 任务")
    prepare.add_argument("project_dir", type=Path)
    prepare.add_argument("--module", required=True)
    prepare.add_argument("--target")
    status = subs.add_parser("status", help="查看扩展状态")
    status.add_argument("project_dir", type=Path)
    status.add_argument("--module", required=True)
    validate = subs.add_parser("validate", help="校验扩展输入指纹和产物")
    validate.add_argument("project_dir", type=Path)
    validate.add_argument("--module", required=True)
    args = parser.parse_args()
    try:
        if args.command == "list":
            for manifest in discover():
                print(f"{manifest['module_id']}\t{manifest['execution_mode']}\t{manifest['display_name']}")
            return 0
        manifest = load_manifest(args.module)
        if args.command == "prepare":
            state, reused = prepare_extension(args.project_dir, args.module, args.target)
            print_state(state, reused)
            return 0
        path = state_path(args.project_dir.resolve(), manifest)
        state = json.loads(path.read_text(encoding="utf-8"))
        if args.command == "status":
            print_state(state)
            return 0
        errors = validate_extension(args.project_dir, args.module)
        if errors:
            print("EXTENSION VALIDATION FAILED:")
            for error in errors:
                print(f" - {error}")
            return 1
        print(f"EXTENSION VALIDATION PASSED: {args.module}")
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"EXTENSION FAILED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
