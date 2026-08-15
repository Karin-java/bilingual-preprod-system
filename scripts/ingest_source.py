#!/usr/bin/env python3
"""Create an immutable, losslessly verifiable source baseline."""
from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

SCHEMA_VERSION = "3.0.0"
PIPELINE_VERSION = "3.0.0"
SUPPORTED_SUFFIXES = {".txt": "txt", ".md": "md", ".docx": "docx"}
CHAPTER_RE = re.compile(r"^\s*chapter\s+(?P<label>[0-9]+|[ivxlcdm]+|[a-z]+(?:-[a-z]+)*)\b.*$", re.I)
SPECIAL_RE = re.compile(r"^\s*(?P<label>prologue|epilogue)(?:\s*[:.\-—].*)?\s*$", re.I)


@dataclass(frozen=True)
class Heading:
    start: int
    text: str
    label: str
    chapter_number: int | None


ONES = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def parse_roman(value: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    upper = value.upper()
    total = 0
    previous = 0
    for char in reversed(upper):
        current = values.get(char)
        if current is None:
            return None
        total += -current if current < previous else current
        previous = max(previous, current)
    numerals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),
        (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
        (5, "V"), (4, "IV"), (1, "I"),
    ]
    remainder = total
    canonical = ""
    for number, numeral in numerals:
        while remainder >= number:
            canonical += numeral
            remainder -= number
    return total if total > 0 and canonical == upper else None


def parse_chapter_number(label: str) -> int | None:
    lowered = label.lower()
    if lowered.isdigit():
        value = int(lowered)
        return value if value > 0 else None
    roman = parse_roman(label)
    if roman is not None:
        return roman
    words = lowered.replace("-", " ").split()
    if len(words) == 1:
        return ONES.get(words[0], TENS.get(words[0]))
    if len(words) == 2 and words[0] in TENS and words[1] in ONES and ONES[words[1]] < 10:
        return TENS[words[0]] + ONES[words[1]]
    return None


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode_text(raw: bytes) -> tuple[str, str, str]:
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig", "text decoded without UTF BOM; no content normalization"
    if raw.startswith(codecs.BOM_UTF16_LE):
        return raw.decode("utf-16"), "utf-16-le-bom", "UTF-16 decoded without BOM; no content normalization"
    if raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16"), "utf-16-be-bom", "UTF-16 decoded without BOM; no content normalization"
    try:
        return raw.decode("utf-8"), "utf-8", "text decoded as UTF-8; no content normalization"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252", "text decoded as Windows-1252; no content normalization"


def extract_docx(raw: bytes) -> tuple[str, str, str, list[str]]:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            xml = archive.read("word/document.xml")
            auxiliary = sorted(
                name for name in archive.namelist()
                if re.fullmatch(r"word/(?:header|footer)[0-9]+[.]xml", name)
                or name in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
            )
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"invalid DOCX: {exc}") from exc
    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.iter(w + "p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == w + "t":
                parts.append(node.text or "")
            elif node.tag == w + "tab":
                parts.append("\t")
            elif node.tag in {w + "br", w + "cr"}:
                parts.append("\n")
            elif node.tag == w + "noBreakHyphen":
                parts.append("‑")
            elif node.tag == w + "softHyphen":
                parts.append("\u00ad")
        paragraphs.append("".join(parts))
    if not paragraphs:
        raise ValueError("DOCX body contains no paragraphs")
    warnings = [f"DOCX auxiliary part retained in original file but not included in body baseline: {name}" for name in auxiliary]
    return "\n".join(paragraphs), "docx-xml", "DOCX body XML in document order; paragraphs separated by LF", warnings


def load_source(path: Path) -> tuple[bytes, str, str, str, str, list[str]]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("supported inputs: .txt, .md, .docx")
    raw = path.read_bytes()
    source_format = SUPPORTED_SUFFIXES[suffix]
    if source_format == "docx":
        text, encoding, rule, warnings = extract_docx(raw)
    else:
        text, encoding, rule = decode_text(raw)
        warnings = []
    if not text:
        raise ValueError("source contains no text")
    return raw, text, source_format, encoding, rule, warnings


def find_headings(text: str) -> list[Heading]:
    headings: list[Heading] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        special = SPECIAL_RE.fullmatch(candidate)
        chapter = CHAPTER_RE.fullmatch(candidate)
        if special:
            headings.append(Heading(offset, candidate.strip(), special.group("label").lower(), None))
        elif chapter:
            label = chapter.group("label")
            headings.append(Heading(offset, candidate.strip(), label, parse_chapter_number(label)))
        offset += len(line)
    return headings


def split_chapters(text: str) -> tuple[list[dict[str, object]], str, list[str]]:
    headings = find_headings(text)
    warnings: list[str] = []
    spans: list[tuple[int, int, str | None, str | None, int]] = []
    if not headings:
        warnings.append("No anchored chapter heading detected; retained as one chapter for review.")
        spans.append((0, len(text), None, None, 1))
        status = "single_chapter_review"
    else:
        first_is_prologue = headings[0].label == "prologue"
        if headings[0].start > 0 and not first_is_prologue:
            spans.append((0, headings[0].start, None, "preamble", 0))
            status = "detected_with_preamble"
        else:
            status = "detected"
        numeric_values = [heading.chapter_number for heading in headings if heading.chapter_number is not None]
        epilogue_number = (max(numeric_values) + 1) if numeric_values else 1
        used_numbers = {0} if spans else set()
        for index, heading in enumerate(headings):
            end = headings[index + 1].start if index + 1 < len(headings) else len(text)
            start = 0 if index == 0 and first_is_prologue else heading.start
            if heading.chapter_number is not None:
                desired = heading.chapter_number
            elif heading.label == "prologue":
                desired = 0
            else:
                desired = epilogue_number
            if desired in used_numbers:
                fallback = 1
                while fallback in used_numbers:
                    fallback += 1
                warnings.append(f"Duplicate or conflicting chapter number {desired} at '{heading.text}'; assigned P{fallback:02d} for review.")
                desired = fallback
            used_numbers.add(desired)
            spans.append((start, end, heading.text, heading.label, desired))

    chapters: list[dict[str, object]] = []
    for start, end, heading, label, ordinal in spans:
        chapter_id = f"P{ordinal:02d}"
        chapter_text = text[start:end]
        if not chapter_text:
            continue
        chapters.append({
            "chapter_id": chapter_id,
            "ordinal": ordinal,
            "heading": heading,
            "source_label": label,
            "source_start": start,
            "source_end": end,
            "text": chapter_text,
        })
    return chapters, status, warnings


def slugify(value: str, fallback_hash: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (value or fallback_hash[:12])[:63].rstrip("-")


def write_json(path: Path, value: object) -> bytes:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def ingest_source(input_path: Path, project_dir: Path, title: str | None = None) -> Path:
    input_path = input_path.resolve()
    project_dir = project_dir.resolve()
    if not input_path.is_file():
        raise ValueError(f"source file not found: {input_path}")
    if project_dir.exists():
        raise ValueError(f"project directory already exists: {project_dir}")

    raw, canonical_text, source_format, encoding, rule, source_warnings = load_source(input_path)
    source_hash = sha256_bytes(raw)
    canonical_bytes = canonical_text.encode("utf-8")
    canonical_hash = sha256_bytes(canonical_bytes)
    title = title or input_path.stem
    project_id = f"PRJ-{slugify(title, source_hash)}"
    chapters, detection_status, warnings = split_chapters(canonical_text)

    project_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{project_dir.name}.", dir=project_dir.parent))
    try:
        source_dir = temporary / "source"
        chapters_dir = source_dir / "chapters"
        chapters_dir.mkdir(parents=True)

        source_copy = source_dir / f"original.input{input_path.suffix.lower()}"
        shutil.copyfile(input_path, source_copy)
        (source_dir / "original.txt").write_bytes(canonical_bytes)

        chapter_records: list[dict[str, object]] = []
        for chapter in chapters:
            chapter_id = str(chapter["chapter_id"])
            basename = chapter_id.lower()
            chapter_text = str(chapter.pop("text"))
            chapter_bytes = chapter_text.encode("utf-8")
            chapter_path = chapters_dir / f"{basename}.txt"
            chapter_path.write_bytes(chapter_bytes)
            chapter_records.append({
                **chapter,
                "character_length": len(chapter_text),
                "sha256": sha256_bytes(chapter_bytes),
                "source_path": f"source/chapters/{basename}.txt",
            })

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "source": {
                "filename": input_path.name,
                "format": source_format,
                "encoding": encoding,
                "copy_path": f"source/{source_copy.name}",
                "byte_length": len(raw),
                "sha256": source_hash,
                "warnings": source_warnings,
            },
            "canonical": {
                "path": "source/original.txt",
                "encoding": "utf-8",
                "character_length": len(canonical_text),
                "byte_length": len(canonical_bytes),
                "sha256": canonical_hash,
                "rule": rule,
            },
            "chapter_detection": {
                "method": "anchored-heading-v1",
                "status": detection_status,
                "warnings": warnings,
            },
            "chapters": chapter_records,
        }
        manifest_bytes = write_json(source_dir / "manifest.json", manifest)
        project = {
            "schema_version": SCHEMA_VERSION,
            "project_id": project_id,
            "title": title,
            "pipeline_version": PIPELINE_VERSION,
            "state": "ingested",
            "source_manifest": "source/manifest.json",
            "artifacts": [{
                "artifact_type": "source_manifest",
                "path": "source/manifest.json",
                "schema_version": SCHEMA_VERSION,
                "sha256": sha256_bytes(manifest_bytes),
            }],
        }
        write_json(temporary / "project.json", project)
        os.replace(temporary, project_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return project_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="无损接收英文原文并生成章节及稳定原文单元")
    parser.add_argument("input", type=Path, help=".txt, .md or .docx source")
    parser.add_argument("--project-dir", type=Path, required=True, help="new project directory")
    parser.add_argument("--title", help="project title; defaults to input filename")
    args = parser.parse_args()
    try:
        target = ingest_source(args.input, args.project_dir, args.title)
    except (OSError, ValueError, ElementTree.ParseError) as exc:
        print(f"INGESTION FAILED: {exc}")
        return 1
    print(f"INGESTION PASSED: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
