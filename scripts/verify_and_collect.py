#!/usr/bin/env python3
"""Verify bilingual chapter Markdown and build compact cross-chapter memory.

Commands:
    verify PROJECT PNN   Verify one output, checkpoint it, rebuild memory.
    collect PROJECT      Recheck every existing output and rebuild memory.
    status PROJECT       Print the current user-readable progress file.

The checks are deterministic. They reject omissions and obvious summary-like
translations, but they do not claim to judge literary translation quality.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


SPAN_RE = re.compile(r"<span\b[^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL)
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
LABEL_RE = re.compile(r"(?:⭐)?【(VOF|VOM|VO\?|CVF|CVM|CV\?)·(旁白|对白)】")
SCENE_RE = re.compile(r"^## 场(\d+)\s*·\s*(.+?)\s*→\s*(.+?)（(.+?) POV）\s*$")
LOCATION_RE = re.compile(r"^> 地点：.+·(?:日|夜|待确认)·(?:内|外|待确认)\s*｜\s*【视角】.+")
CHAPTER_ID_RE = re.compile(r"^P\d{2,3}$", re.IGNORECASE)
TABLE_HEADERS = {
    "角色-出场场合对照": ["角色", "出场场合", "所在场", "备注"],
    "场景表": ["场次", "具体地点", "时间", "出场角色", "情节概括"],
    "新增线索与待确认": ["编号", "对象", "类型", "信息或问题", "原文线索 EN", "中文参考", "状态"],
}
LEGACY_TERMS = ["该场合需设计资产", "服装编号总索引", "A/B/C 定位", "character_glossary.md"]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def normalize_english(text: str) -> str:
    value = html.unescape(text)
    value = unicodedata.normalize("NFC", value)
    return re.sub(r"\s+", "", value)


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def parse_spans(markdown: str) -> list[dict[str, str]]:
    results = []
    previous_end = 0
    for match in SPAN_RE.finditer(markdown):
        parts = BR_RE.split(match.group(1), maxsplit=1)
        if len(parts) != 2:
            results.append({"en": parts[0], "zh": "", "label": "", "kind": ""})
            previous_end = match.end()
            continue
        prefix = markdown[previous_end : match.start()]
        labels = list(LABEL_RE.finditer(prefix))
        label = labels[-1].group(1) if labels else ""
        kind = labels[-1].group(2) if labels else ""
        results.append({"en": parts[0], "zh": parts[1], "label": label, "kind": kind})
        previous_end = match.end()
    return results


def quoted_passages(text: str) -> list[str]:
    passages = []
    pattern = re.compile(r"“([^”]+)”|\"([^\"\r\n]+)\"", re.DOTALL)
    for match in pattern.finditer(text):
        passage = match.group(1) if match.group(1) is not None else match.group(2)
        if passage and re.search(r"[A-Za-z]", passage):
            passages.append(passage)
    return passages


def first_difference(expected: str, actual: str) -> str:
    matcher = difflib.SequenceMatcher(a=expected, b=actual, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            left = expected[max(0, i1 - 35) : min(len(expected), i2 + 35)]
            right = actual[max(0, j1 - 35) : min(len(actual), j2 + 35)]
            return f"首个差异 {tag}；源文≈{left!r}；输出≈{right!r}"
    return "长度或编码不一致"


def parse_table(markdown: str, heading: str) -> tuple[list[str], list[list[str]]]:
    lines = markdown.splitlines()
    start = None
    target = f"### {heading}"
    for index, line in enumerate(lines):
        if line.strip() == target:
            start = index + 1
            break
    if start is None:
        return [], []

    table_lines = []
    started = False
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("#") and started:
            break
        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines.append(stripped)
            started = True
        elif started and stripped:
            break
    if len(table_lines) < 2:
        return [], []

    def cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip("|").split("|")]

    header = cells(table_lines[0])
    rows = []
    for line in table_lines[1:]:
        row = cells(line)
        if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in row):
            continue
        rows.append(row)
    return header, rows


def validate_tables(markdown: str, scene_numbers: set[int]) -> list[str]:
    errors = []
    for heading, expected_header in TABLE_HEADERS.items():
        header, rows = parse_table(markdown, heading)
        if not header:
            errors.append(f"缺少或无法解析章末表：{heading}")
            continue
        if header != expected_header:
            errors.append(f"{heading} 表头应为：{' | '.join(expected_header)}")
        for row_number, row in enumerate(rows, start=1):
            if len(row) != len(expected_header):
                errors.append(f"{heading} 第{row_number}行列数错误")
                continue
            if heading == "角色-出场场合对照" and row[2] not in {"—", "-"}:
                numbers = [int(value) for value in re.findall(r"场(\d+)", row[2])]
                if not numbers or any(number not in scene_numbers for number in numbers):
                    errors.append(f"角色表引用不存在的场次：{row[2]}")
                if any(separator in row[1] for separator in ("；", ";")):
                    errors.append(f"角色跨多个出场场合时必须分行记录：{row[1]}")
            if heading == "场景表":
                numbers = [int(value) for value in re.findall(r"场(\d+)", row[0])]
                if not numbers or any(number not in scene_numbers for number in numbers):
                    errors.append(f"场景表引用不存在的场次：{row[0]}")
            if heading == "新增线索与待确认" and row[-1] not in {"已确认", "待确认"}:
                errors.append(f"线索状态只能是“已确认”或“待确认”：{row[-1]}")
    return errors


def verify_chapter(project: Path, chapter_id: str) -> tuple[list[str], dict[str, object]]:
    source_path = project / "source" / "chapters" / f"{chapter_id}.txt"
    output_path = project / "deliverables" / "chapters" / f"{chapter_id}.md"
    errors: list[str] = []
    details: dict[str, object] = {"chapter": chapter_id}

    if not source_path.is_file():
        return [f"找不到章节源文件：{source_path}"], details
    if not output_path.is_file():
        return [f"找不到章节输出：{output_path}"], details

    source = source_path.read_text(encoding="utf-8")
    markdown = output_path.read_text(encoding="utf-8")
    spans = parse_spans(markdown)
    details.update(
        {
            "source_sha256": sha256_text(source),
            "output_sha256": sha256_text(markdown),
            "span_count": len(spans),
        }
    )

    for term in LEGACY_TERMS:
        if term in markdown:
            errors.append(f"出现已删除的旧要求：{term}")

    if not spans:
        errors.append("没有找到任何 EN<br>中文 的 span 双语块")
        return errors, details

    output_english = "".join(span["en"] for span in spans)
    expected_normalized = normalize_english(source)
    actual_normalized = normalize_english(output_english)
    if expected_normalized != actual_normalized:
        errors.append("英文未完整按序覆盖原文；" + first_difference(expected_normalized, actual_normalized))

    nonempty_source_paragraphs = [line for line in re.split(r"\r\n|\n|\r", source) if line.strip()]
    if len(spans) < len(nonempty_source_paragraphs):
        errors.append(
            f"双语块数量过少：源文非空段落{len(nonempty_source_paragraphs)}，输出span仅{len(spans)}；"
            "疑似合并段落或摘要化"
        )

    total_words = len(re.findall(r"[A-Za-z]+(?:['’.-][A-Za-z]+)*", source))
    total_cjk = sum(len(re.findall(r"[\u3400-\u9fff]", TAG_RE.sub("", span["zh"]))) for span in spans)
    details.update({"source_words": total_words, "translated_cjk": total_cjk})
    if total_words >= 40 and total_cjk < total_words * 0.35:
        errors.append(
            f"中文信息量异常偏低：英文约{total_words}词，中文汉字{total_cjk}；疑似摘要代替翻译"
        )

    for index, span in enumerate(spans, start=1):
        en = TAG_RE.sub("", html.unescape(span["en"])).strip()
        zh = TAG_RE.sub("", html.unescape(span["zh"])).strip()
        if not zh:
            errors.append(f"第{index}个双语块缺少中文")
        if re.search(r"[A-Za-z]", en) and not span["label"]:
            errors.append(f"第{index}个英文内容块缺少声轨标签")
        words = len(re.findall(r"[A-Za-z]+(?:['’.-][A-Za-z]+)*", en))
        cjk = len(re.findall(r"[\u3400-\u9fff]", zh))
        if words >= 20 and cjk < words * 0.2:
            errors.append(f"第{index}个双语块中文明显过短，疑似局部摘要")
        if re.search(r"[A-Za-z]", en) and not contains_cjk(zh):
            errors.append(f"第{index}个英文内容块没有可识别的中文译文")

    quotes = quoted_passages(source)
    cv_english = normalize_english("".join(span["en"] for span in spans if span["kind"] == "对白"))
    missing_quotes = [quote for quote in quotes if normalize_english(quote) not in cv_english]
    details.update({"quoted_passages": len(quotes), "uncovered_dialogue": len(missing_quotes)})
    if missing_quotes:
        samples = "；".join(re.sub(r"\s+", " ", item)[:50] for item in missing_quotes[:3])
        errors.append(f"有{len(missing_quotes)}段原文引号对白未进入CV对白声轨，例如：{samples}")

    lines = markdown.splitlines()
    scene_numbers = []
    for index, line in enumerate(lines):
        if not line.startswith("## 场"):
            continue
        match = SCENE_RE.match(line)
        if not match:
            errors.append(f"分场头格式错误：{line}")
            continue
        scene_numbers.append(int(match.group(1)))
        next_nonempty = ""
        for candidate in lines[index + 1 :]:
            if candidate.strip():
                next_nonempty = candidate.strip()
                break
        if not LOCATION_RE.match(next_nonempty):
            errors.append(f"分场头下缺少合法地点行：{line}")

    if chapter_id != "P00" and not scene_numbers:
        errors.append("没有找到有效分场")
    if scene_numbers and scene_numbers != list(range(1, len(scene_numbers) + 1)):
        errors.append(f"场号必须从1连续递增，当前为：{scene_numbers}")

    errors.extend(validate_tables(markdown, set(scene_numbers)))
    return errors, details


def load_project(project: Path) -> tuple[dict, dict, list[str]]:
    manifest_path = project / "source" / "manifest.json"
    state_path = project / "work" / "state.json"
    if not manifest_path.is_file() or not state_path.is_file():
        raise FileNotFoundError("项目缺少 source/manifest.json 或 work/state.json")
    manifest = read_json(manifest_path)
    state = read_json(state_path)
    chapter_ids = [str(chapter["id"]) for chapter in manifest["chapters"]]
    return manifest, state, chapter_ids


def markdown_table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def rebuild_derived(project: Path, manifest: dict, state: dict, chapter_ids: list[str]) -> None:
    verified = []
    stale = []
    for chapter_id in chapter_ids:
        entry = state["chapters"][chapter_id]
        output_path = project / "deliverables" / "chapters" / f"{chapter_id}.md"
        if entry.get("status") != "verified" or not output_path.is_file():
            continue
        current_hash = sha256_text(output_path.read_text(encoding="utf-8"))
        if current_hash != entry.get("output_sha256"):
            entry["status"] = "changed-needs-verification"
            stale.append(chapter_id)
        else:
            verified.append(chapter_id)

    pending = [chapter_id for chapter_id in chapter_ids if chapter_id not in verified]
    next_chapter = pending[0] if pending else None

    progress = [
        "# 制作进度",
        "",
        f"**项目**：{manifest['title']}",
        f"**章节数**：{len(chapter_ids)}",
        f"**已通过**：{len(verified)}",
        f"**下一章**：{next_chapter or '全部章节已通过，进入全文复盘'}",
        "",
        "## 章节状态",
        "",
    ]
    for chapter_id in chapter_ids:
        status = state["chapters"][chapter_id].get("status", "pending")
        mark = "x" if chapter_id in verified else " "
        label = {
            "verified": "已通过",
            "changed-needs-verification": "文件已修改，需重新校验",
            "failed": "校验失败",
            "pending": "待处理",
        }.get(status, status)
        progress.append(f"- [{mark}] {chapter_id}：{label}")
    progress.append("")
    write_text(project / "work" / "制作进度.md", "\n".join(progress))

    role_names: list[str] = []
    locations: list[str] = []
    pending_clues: list[tuple[str, list[str]]] = []
    full_story = [
        "# 全文复盘输入",
        "",
        "> 由已通过章节的章末索引自动收集。全文复盘优先读取本文件，疑点再回查对应章节。",
        "",
    ]

    for chapter_id in verified:
        markdown = (project / "deliverables" / "chapters" / f"{chapter_id}.md").read_text(encoding="utf-8")
        full_story.extend([f"## {chapter_id}", ""])
        for heading in TABLE_HEADERS:
            header, rows = parse_table(markdown, heading)
            full_story.extend([f"### {heading}", "", markdown_table(header, rows), ""])
            if heading == "角色-出场场合对照":
                role_names.extend(row[0] for row in rows if row and row[0])
                locations.extend(row[1] for row in rows if len(row) > 1 and row[1] not in {"仅被提及", "—", "-"})
            elif heading == "场景表":
                locations.extend(row[1] for row in rows if len(row) > 1 and row[1])
            elif heading == "新增线索与待确认":
                pending_clues.extend((chapter_id, row) for row in rows if len(row) == 7 and row[-1] == "待确认")

    unique_roles = list(dict.fromkeys(role_names))
    unique_locations = list(dict.fromkeys(locations))
    memory = [
        "# 项目记忆",
        "",
        "> 由已通过章节的章末索引自动生成。用于下一章保持译名和场景术语一致；不是全文最终结论。",
        "",
        f"**已通过章节**：{', '.join(verified) if verified else '无'}",
        "",
        "## 已使用角色名称",
        "",
    ]
    memory.extend(f"- {name}" for name in unique_roles)
    if not unique_roles:
        memory.append("- 暂无")
    memory.extend(["", "## 已使用具体场景名称", ""])
    memory.extend(f"- {name}" for name in unique_locations)
    if not unique_locations:
        memory.append("- 暂无")
    memory.extend(["", "## 待确认线索", ""])
    for chapter_id, row in pending_clues:
        memory.append(f"- {row[0]}（{chapter_id}）：{row[1]}｜{row[3]}")
    if not pending_clues:
        memory.append("- 暂无")
    memory.append("")

    if verified:
        write_text(project / "work" / "全文复盘输入.md", "\n".join(full_story))
    else:
        write_text(project / "work" / "全文复盘输入.md", "# 全文复盘输入\n\n> 尚无通过校验的章节。\n")
    write_text(project / "work" / "项目记忆.md", "\n".join(memory))
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(project / "work" / "state.json", state)


def verify_command(project: Path, chapter_id: str) -> int:
    chapter_id = chapter_id.upper()
    if not CHAPTER_ID_RE.match(chapter_id):
        print(f"ERROR: 非法章节编号：{chapter_id}", file=sys.stderr)
        return 2
    manifest, state, chapter_ids = load_project(project)
    if chapter_id not in chapter_ids:
        print(f"ERROR: 项目中不存在章节：{chapter_id}", file=sys.stderr)
        return 2

    errors, details = verify_chapter(project, chapter_id)
    entry = state["chapters"][chapter_id]
    if errors:
        entry["status"] = "failed"
        entry["last_errors"] = errors
        entry["output_sha256"] = details.get("output_sha256")
        rebuild_derived(project, manifest, state, chapter_ids)
        print(f"VALIDATION FAILED: {chapter_id}")
        for error in errors:
            print(f" - {error}")
        return 1

    entry["status"] = "verified"
    entry["last_errors"] = []
    entry["output_sha256"] = details["output_sha256"]
    entry["verified_at"] = datetime.now(timezone.utc).isoformat()
    rebuild_derived(project, manifest, state, chapter_ids)
    print(f"VALIDATION PASSED: {chapter_id}")
    print(f"SPANS={details['span_count']}")
    print(f"DIALOGUE_QUOTES={details['quoted_passages']}")
    print(f"OUTPUT_SHA256={details['output_sha256']}")
    return 0


def collect_command(project: Path) -> int:
    manifest, state, chapter_ids = load_project(project)
    failures = 0
    for chapter_id in chapter_ids:
        output = project / "deliverables" / "chapters" / f"{chapter_id}.md"
        if not output.is_file():
            continue
        errors, details = verify_chapter(project, chapter_id)
        entry = state["chapters"][chapter_id]
        if errors:
            failures += 1
            entry["status"] = "failed"
            entry["last_errors"] = errors
            entry["output_sha256"] = details.get("output_sha256")
            print(f"FAILED {chapter_id}: {len(errors)} issue(s)")
        else:
            entry["status"] = "verified"
            entry["last_errors"] = []
            entry["output_sha256"] = details["output_sha256"]
            entry["verified_at"] = datetime.now(timezone.utc).isoformat()
            print(f"PASSED {chapter_id}")
    rebuild_derived(project, manifest, state, chapter_ids)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="校验双语章节并收集全文复盘索引。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify", help="校验一个章节并形成检查点")
    verify_parser.add_argument("project", type=Path)
    verify_parser.add_argument("chapter")

    collect_parser = subparsers.add_parser("collect", help="重新校验已有章节并重建索引")
    collect_parser.add_argument("project", type=Path)

    status_parser = subparsers.add_parser("status", help="显示制作进度")
    status_parser.add_argument("project", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "verify":
            return verify_command(args.project.resolve(), args.chapter)
        if args.command == "collect":
            return collect_command(args.project.resolve())
        progress_path = args.project.resolve() / "work" / "制作进度.md"
        print(progress_path.read_text(encoding="utf-8"))
        return 0
    except Exception as exc:  # concise CLI boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
