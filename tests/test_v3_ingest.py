from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ingest_source import ingest_source
from validate_ingest import validate_project


class V3IngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _manifest(self, project: Path) -> dict:
        return json.loads((project / "source" / "manifest.json").read_text(encoding="utf-8"))

    def test_mixed_newlines_are_reconstructed_exactly(self) -> None:
        raw = b"Chapter 1\r\nLine one\nChapter 2\rLine two\r\n"
        source = self.root / "mixed.txt"
        source.write_bytes(raw)
        project = ingest_source(source, self.root / "project")
        self.assertEqual(validate_project(project), [])
        manifest = self._manifest(project)
        reconstructed = b"".join((project / item["source_path"]).read_bytes() for item in manifest["chapters"])
        self.assertEqual(reconstructed, raw)

    def test_no_heading_is_one_reviewable_p01(self) -> None:
        source = self.root / "plain.md"
        source.write_text("A story without a heading.\n", encoding="utf-8")
        project = ingest_source(source, self.root / "project")
        manifest = self._manifest(project)
        self.assertEqual(manifest["chapter_detection"]["status"], "single_chapter_review")
        self.assertEqual([item["chapter_id"] for item in manifest["chapters"]], ["P01"])

    def test_source_chapter_number_is_kept_as_pnn(self) -> None:
        source = self.root / "chapter.txt"
        source.write_text("Chapter 12: Test\nBody\n", encoding="utf-8")
        project = ingest_source(source, self.root / "project")
        self.assertEqual(self._manifest(project)["chapters"][0]["chapter_id"], "P12")

    def test_docx_body_becomes_explicit_utf8_baseline(self) -> None:
        source = self.root / "book.docx"
        content_types = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>'
        document = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>Chapter 1</w:t></w:r></w:p><w:p><w:r><w:t>Body text.</w:t></w:r></w:p>
</w:body></w:document>'''
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("word/document.xml", document)
        project = ingest_source(source, self.root / "project")
        self.assertEqual((project / "source" / "original.txt").read_text(encoding="utf-8"), "Chapter 1\nBody text.")
        self.assertEqual(validate_project(project), [])

    def test_tampering_is_detected(self) -> None:
        source = self.root / "book.txt"
        source.write_text("Chapter 1\nBody\n", encoding="utf-8")
        project = ingest_source(source, self.root / "project")
        chapter = project / "source" / "chapters" / "p01.txt"
        chapter.write_text("Changed", encoding="utf-8")
        errors = validate_project(project)
        self.assertTrue(any("chapter" in error.lower() for error in errors))


if __name__ == "__main__":
    unittest.main()
