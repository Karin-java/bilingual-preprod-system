#!/usr/bin/env python3
"""Convert one user-selected Markdown deliverable to a basic standalone DOCX."""
from __future__ import annotations

import argparse
import re
import zipfile
from html import escape
from pathlib import Path


def _run(text: str, *, bold: bool = False) -> str:
    properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{properties}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(text: str = "", style: str | None = None) -> str:
    properties = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    bold = text.startswith("**") and text.endswith("**") and len(text) > 4
    if bold:
        text = text[2:-2]
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return f"<w:p>{properties}{_run(text, bold=bold)}</w:p>"


def _table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [cell.strip().replace("\\|", "|") for cell in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        row = "".join(f"<w:tc><w:tcPr/><w:p>{_run(cell)}</w:p></w:tc>" for cell in cells)
        rows.append(f"<w:tr>{row}</w:tr>")
    return '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr>' + "".join(rows) + "</w:tbl>"


def markdown_body(markdown: str) -> str:
    lines = markdown.splitlines()
    body: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            body.append(_table(table_lines))
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            body.append(_paragraph(heading.group(2), f"Heading{len(heading.group(1))}"))
        elif line.startswith("- "):
            body.append(_paragraph("• " + line[2:], "ListParagraph"))
        elif line.startswith(">"):
            body.append(_paragraph(line[1:].lstrip(), "Quote"))
        elif line == "---":
            body.append(_paragraph("────────────────────────"))
        else:
            body.append(_paragraph(line))
        index += 1
    return "".join(body)


def convert(markdown_path: Path, output_path: Path, replace: bool = False) -> Path:
    markdown_path = markdown_path.resolve()
    output_path = output_path.resolve()
    if markdown_path.suffix.lower() != ".md" or not markdown_path.is_file():
        raise ValueError("input must be an existing Markdown file")
    if output_path.suffix.lower() != ".docx":
        raise ValueError("output must use the .docx extension")
    if output_path.exists() and not replace:
        raise ValueError("output exists; use --replace after review")
    body = markdown_body(markdown_path.read_text(encoding="utf-8"))
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>'''
    styles = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:eastAsia="Microsoft YaHei"/><w:sz w:val="21"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="420"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:pPr><w:ind w:left="420"/><w:spacing w:after="80"/></w:pPr></w:style>
</w:styles>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", document_rels)
    with zipfile.ZipFile(output_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("generated DOCX failed ZIP integrity validation")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="按用户选择将一个 Markdown 文件转换为 DOCX")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    output = args.output or args.markdown.with_suffix(".docx")
    try:
        path = convert(args.markdown, output, args.replace)
    except (OSError, ValueError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        print(f"DOCX CONVERSION FAILED: {exc}")
        return 1
    print(f"DOCX CREATED: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
