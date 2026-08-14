from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_source import ingest_source  # noqa: E402
from validate_ingest import validate_project  # noqa: E402


class IngestSourceTests(unittest.TestCase):
    def test_text_is_reconstructed_with_mixed_newlines(self) -> None:
        canonical = (
            "Front matter\r\n\r\n"
            "CHAPTER 1\r\nFirst line.\r\n\r\n"
            "Chapter Two\nSecond line without final newline"
        )
        raw = b"\xef\xbb\xbf" + canonical.encode("utf-8")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "story.txt"
            source.write_bytes(raw)
            project = ingest_source(source, root / "project", "Test Story")
            self.assertEqual(validate_project(project), [])
            manifest = json.loads((project / "source/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([c["chapter_id"] for c in manifest["chapters"]], ["P00", "P01", "P02"])
            first_unit = json.loads((project / manifest["chapters"][0]["units_path"]).read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(set(first_unit), {"schema_version", "unit_id", "chapter_id", "ordinal", "source_text", "source_sha256"})
            self.assertEqual((project / "source/original.txt").read_bytes(), canonical.encode("utf-8"))
            rebuilt = b"".join((project / c["source_path"]).read_bytes() for c in manifest["chapters"])
            self.assertEqual(rebuilt, canonical.encode("utf-8"))

    def test_ids_and_hashes_are_stable_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "story.md"
            source.write_bytes(b"Chapter 1\nAlpha\n\nChapter 2\nBeta\n")
            first = ingest_source(source, root / "first", "Stable")
            second = ingest_source(source, root / "second", "Stable")
            one = json.loads((first / "source/manifest.json").read_text(encoding="utf-8"))
            two = json.loads((second / "source/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(one, two)
            for chapter in one["chapters"]:
                self.assertEqual(
                    (first / chapter["units_path"]).read_bytes(),
                    (second / chapter["units_path"]).read_bytes(),
                )

    def test_no_heading_becomes_reviewable_single_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "story.txt"
            source.write_text("A story with no heading.\n", encoding="utf-8", newline="")
            project = ingest_source(source, root / "project")
            manifest = json.loads((project / "source/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["chapter_detection"]["status"], "single_chapter_review")
            self.assertEqual(manifest["chapters"][0]["chapter_id"], "P01")
            self.assertEqual(validate_project(project), [])

    def test_isolated_source_chapter_keeps_its_number(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "chapter3.txt"
            source.write_bytes(b"Chapter 3: Yes, Master.\nText\n")
            project = ingest_source(source, root / "project")
            manifest = json.loads((project / "source/manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["chapters"][0]["chapter_id"], "P03")
            self.assertEqual(manifest["chapters"][0]["source_label"], "3")
            self.assertEqual(validate_project(project), [])

    def test_docx_body_has_explicit_canonical_baseline(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Chapter 1</w:t></w:r></w:p>
    <w:p><w:r><w:t>Hello</w:t><w:tab/><w:t>world.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "story.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", xml)
            project = ingest_source(source, root / "project")
            self.assertEqual((project / "source/original.txt").read_text(encoding="utf-8"), "Chapter 1\nHello\tworld.")
            self.assertEqual(validate_project(project), [])

    def test_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "story.txt"
            source.write_bytes(b"Chapter 1\nOriginal\n")
            project = ingest_source(source, root / "project")
            (project / "source/chapters/p01.txt").write_bytes(b"Chapter 1\nChanged\n")
            errors = validate_project(project)
            self.assertTrue(any("canonical slice" in error or "hash mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
