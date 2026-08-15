from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


extract = load_module("extract_chapters", ROOT / "scripts" / "extract_chapters.py")
verify = load_module("verify_and_collect", ROOT / "scripts" / "verify_and_collect.py")


def valid_p01_markdown() -> str:
    return """# P01 · Chapter 1: Test

## 场1 · 王宫·书房 → 薇奥莱特受到询问（Violette POV）
> 地点：王宫·书房·待确认·内 ｜ 【视角】薇奥莱特 / Violette

【VOF·旁白】薇奥莱特 / Violette
<span style="background-color:#EFEEE9; color:#5B5952; padding:3px 8px; border-radius:5px; display:inline-block;">Chapter 1: Test<br>第一章：测试</span>

【VOF·旁白】薇奥莱特 / Violette
<span style="background-color:#EFEEE9; color:#5B5952; padding:3px 8px; border-radius:5px; display:inline-block;">Violette POV<br>薇奥莱特视角</span>

⭐【CVF·对白】薇奥莱特 / Violette（疑惑）
<span style="background-color:#FBEFDB; color:#8A5A2B; padding:3px 8px; border-radius:5px; display:inline-block;">“What?”<br>“什么？”</span>

【VOF·旁白】薇奥莱特 / Violette
<span style="background-color:#EFEEE9; color:#5B5952; padding:3px 8px; border-radius:5px; display:inline-block;">He stood in the office.<br>他站在书房里。</span>

## 本章索引

### 角色-出场场合对照

| 角色 | 出场场合 | 所在场 | 备注 |
| --- | --- | --- | --- |
| 薇奥莱特 / Violette | 王宫·书房 | 场1 | 实际出镜；提出疑问 |
| 他 / He | 王宫·书房 | 场1 | 实际出镜；站在书房内 |

### 场景表

| 场次 | 具体地点 | 时间 | 出场角色 | 情节概括 |
| --- | --- | --- | --- | --- |
| 场1 | 王宫·书房 | 待确认·内 | 薇奥莱特；他 | 薇奥莱特提出疑问 |

### 新增线索与待确认

| 编号 | 对象 | 类型 | 信息或问题 | 原文线索 EN | 中文参考 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| P01-Q01 | He | 角色身份 | 此人身份是谁？ | He stood in the office. | 他站在书房里。 | 待确认 |
"""


class ExtractTests(unittest.TestCase):
    def test_crlf_source_reconstructs_exactly_and_preserves_source_numbers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "novel.txt"
            original = "Title\r\n\r\nChapter 1: One\r\nAlpha\r\nChapter 3: Three\r\nOmega\r\n"
            source.write_bytes(original.encode("utf-8"))
            project = root / "project"

            manifest = extract.initialize_project(source, project, "Test")

            self.assertEqual([item["id"] for item in manifest["chapters"]], ["P00", "P01", "P03"])
            rebuilt = ""
            for item in manifest["chapters"]:
                rebuilt += extract.read_text_exact(project / item["path"])
            self.assertEqual(rebuilt, original)
            self.assertTrue(manifest["reconstruction_verified"])

    def test_text_without_chapter_heading_becomes_p01(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "single.md"
            source.write_text("A short standalone text.", encoding="utf-8")
            manifest = extract.initialize_project(source, root / "project", "Single")
            self.assertEqual([item["id"] for item in manifest["chapters"]], ["P01"])

    def test_leading_blank_lines_do_not_create_empty_p00(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "novel.txt"
            original = "\n\nChapter 1: One\nBody"
            source.write_bytes(original.encode("utf-8"))
            project = root / "project"
            manifest = extract.initialize_project(source, project, "Whitespace")
            self.assertEqual([item["id"] for item in manifest["chapters"]], ["P01"])
            self.assertEqual(extract.read_text_exact(project / "source" / "chapters" / "P01.txt"), original)


class VerifyTests(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        source = root / "novel.txt"
        source.write_text(
            "Chapter 1: Test\nViolette POV\n“What?”\nHe stood in the office.",
            encoding="utf-8",
        )
        project = root / "project"
        extract.initialize_project(source, project, "Verification")
        return project

    def test_valid_chapter_checkpoints_and_builds_memory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self.make_project(Path(temp_dir))
            output = project / "deliverables" / "chapters" / "P01.md"
            output.write_text(valid_p01_markdown(), encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                result = verify.verify_command(project, "P01")
            self.assertEqual(result, 0)
            state = json.loads((project / "work" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["chapters"]["P01"]["status"], "verified")
            memory = (project / "work" / "项目记忆.md").read_text(encoding="utf-8")
            self.assertIn("薇奥莱特 / Violette", memory)
            self.assertIn("王宫·书房", memory)
            full_story = (project / "work" / "全文复盘输入.md").read_text(encoding="utf-8")
            self.assertIn("P01-Q01", full_story)

    def test_summary_like_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self.make_project(Path(temp_dir))
            source = (project / "source" / "chapters" / "P01.txt").read_text(encoding="utf-8")
            bad = f"""# P01

【VOF·旁白】薇奥莱特 / Violette
<span style="background-color:#EFEEE9; color:#5B5952; display:inline-block;">{source}<br>本章讲述一次询问。</span>
"""
            (project / "deliverables" / "chapters" / "P01.md").write_text(bad, encoding="utf-8")

            errors, _ = verify.verify_chapter(project, "P01")
            self.assertTrue(any("双语块数量过少" in error for error in errors))
            self.assertTrue(any("未进入CV对白声轨" in error for error in errors))

    def test_multiple_locations_in_one_role_row_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self.make_project(Path(temp_dir))
            bad = valid_p01_markdown().replace("王宫·书房 | 场1", "王宫·书房；王宫·走廊 | 场1", 1)
            (project / "deliverables" / "chapters" / "P01.md").write_text(bad, encoding="utf-8")
            errors, _ = verify.verify_chapter(project, "P01")
            self.assertTrue(any("跨多个出场场合时必须分行" in error for error in errors))

    def test_changed_english_word_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = self.make_project(Path(temp_dir))
            changed = valid_p01_markdown().replace("He stood in the office.", "He waited in the office.", 1)
            (project / "deliverables" / "chapters" / "P01.md").write_text(changed, encoding="utf-8")
            errors, _ = verify.verify_chapter(project, "P01")
            self.assertTrue(any("英文未完整按序覆盖原文" in error for error in errors))

    def test_two_chapters_resume_in_source_order(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "novel.txt"
            body = "Violette POV\n“What?”\nHe stood in the office."
            source.write_text(
                f"Chapter 1: Test\n{body}\nChapter 2: Test\n{body}",
                encoding="utf-8",
            )
            project = root / "project"
            extract.initialize_project(source, project, "Two chapters")

            chapter_dir = project / "deliverables" / "chapters"
            (chapter_dir / "P01.md").write_text(valid_p01_markdown(), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                p01_result = verify.verify_command(project, "P01")
            self.assertEqual(p01_result, 0)
            progress = (project / "work" / "制作进度.md").read_text(encoding="utf-8")
            self.assertIn("**下一章**：P02", progress)

            p02 = valid_p01_markdown().replace("P01", "P02").replace("Chapter 1", "Chapter 2")
            (chapter_dir / "P02.md").write_text(p02, encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                p02_result = verify.verify_command(project, "P02")
            self.assertEqual(p02_result, 0)
            progress = (project / "work" / "制作进度.md").read_text(encoding="utf-8")
            self.assertIn("全部章节已通过，进入全文复盘", progress)


if __name__ == "__main__":
    unittest.main()
