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

from build_catalogs import build_catalogs
from convert_to_docx import convert
from ingest_source import ingest_source
from manage_assets import record_event as asset_event
from manage_entities import record_event as entity_event
from preprod import read_json, write_json
from review_analysis import append_event, load_current_analysis
from run_pipeline import run_pipeline
from validate_analysis import validate_analysis
from validate_catalogs import validate_catalogs
from validate_ingest import validate_project
from validate_pipeline import validate_pipeline


SOURCE = (
    "Chapter 1: Arrival\n"
    "Violette POV\n\n"
    "“What?”\n"
    "The King entered his office.\n"
    "A silver key lay on the desk.\n"
    "Chapter 2: Name\n"
    "The King Viktor entered the same office.\n"
    "“Come.”\n"
)


def block(chapter: str, number: int, kind: str, en: str, zh: str, speaker: str | None = None) -> dict:
    return {"block_id": f"{chapter}-B{number:04d}", "kind": kind, "speaker_ref": speaker, "text_en": en, "text_zh": zh}


def chapter_one(input_hash: str, source_hash: str) -> dict:
    return {
        "schema_version": "3.0.0", "chapter_id": "P01", "source_sha256": source_hash, "input_sha256": input_hash,
        "title_en": "Arrival", "title_zh": "抵达", "pov": "Violette",
        "blocks": [
            block("P01", 1, "heading", "Chapter 1: Arrival\n", "第一章：抵达"),
            block("P01", 2, "heading", "Violette POV\n\n", "薇奥莱特视角"),
            block("P01", 3, "dialogue", "“What?”\n", "“什么？”", "violette"),
            block("P01", 4, "action", "The King entered his office.\n", "国王走进了自己的书房。"),
            block("P01", 5, "action", "A silver key lay on the desk.\n", "一把银钥匙放在书桌上。"),
        ],
        "front_matter_block_ids": ["P01-B0001", "P01-B0002"],
        "scenes": [{
            "scene_id": "P01-S001", "ordinal": 1, "name_zh": "王宫·国王的书房", "name_en": "Royal Palace · the King's office",
            "location_ref": "kings-office", "parent_location_zh": "王宫", "int_ext": "INT", "time": "UNKNOWN",
            "summary_zh": "薇奥莱特在国王书房内回应，国王进入，银钥匙出现在桌上。",
            "block_ids": ["P01-B0003", "P01-B0004", "P01-B0005"],
            "beats": [{"beat_id": "P01-S001-B001", "title_zh": "国王进入", "summary_zh": "一句追问后，国王进入书房，桌上出现银钥匙。", "block_ids": ["P01-B0003", "P01-B0004", "P01-B0005"]}],
            "character_refs": ["violette", "king"],
        }],
        "characters": [
            {"ref": "violette", "canonical_name_en": "Violette", "name_zh": "薇奥莱特", "aliases_en": [], "aliases_zh": [], "category": "named", "importance": "major", "story_role_zh": "女主"},
            {"ref": "king", "canonical_name_en": "the King", "name_zh": "国王", "aliases_en": ["King"], "aliases_zh": [], "category": "named", "importance": "major", "story_role_zh": "男主"},
        ],
        "character_facts": [
            {"fact_id": "P01-CF001", "subject_ref": "violette", "field": "identity", "value_zh": "故事视角人物", "source_block_ids": ["P01-B0002"]},
            {"fact_id": "P01-CF002", "subject_ref": "king", "field": "identity", "value_zh": "国王", "source_block_ids": ["P01-B0004"]},
        ],
        "locations": [{
            "ref": "kings-office", "name_en": "the King's office", "name_zh": "国王的书房", "aliases_en": ["his office"], "aliases_zh": [],
            "parent_zh": "王宫", "int_ext": "INT",
            "facts": [{"fact_id": "P01-LF001", "field": "furniture", "value_zh": "房内有书桌", "source_block_ids": ["P01-B0005"]}],
        }],
        "appearances": [
            {"appearance_id": "P01-AP001", "character_ref": "violette", "scene_id": "P01-S001", "presence": "actual", "summary_zh": "在书房发问。", "source_block_ids": ["P01-B0003"]},
            {"appearance_id": "P01-AP002", "character_ref": "king", "scene_id": "P01-S001", "presence": "actual", "summary_zh": "进入自己的书房。", "source_block_ids": ["P01-B0004"]},
        ],
        "identity_links": [],
        "asset_candidates": [{"candidate_key": "silver-key", "type": "prop", "name_zh": "银钥匙", "name_en": "silver key", "subject_ref": None, "variant_zh": None, "scene_ids": ["P01-S001"], "source_block_ids": ["P01-B0005"], "reason_zh": "剧情明确出现的独立道具。"}],
        "issues": [{"issue_id": "ISS-P01-0001", "scope_id": "P01-S001", "question_zh": "本场发生在一天中的什么时间？", "evidence_block_ids": ["P01-B0003", "P01-B0004"], "status": "pending", "resolution_zh": None}],
    }


def chapter_two(input_hash: str, source_hash: str) -> dict:
    return {
        "schema_version": "3.0.0", "chapter_id": "P02", "source_sha256": source_hash, "input_sha256": input_hash,
        "title_en": "Name", "title_zh": "名字", "pov": None,
        "blocks": [
            block("P02", 1, "heading", "Chapter 2: Name\n", "第二章：名字"),
            block("P02", 2, "action", "The King Viktor entered the same office.\n", "国王维克多走进了同一间书房。"),
            block("P02", 3, "dialogue", "“Come.”\n", "“过来。”", "viktor"),
        ],
        "front_matter_block_ids": ["P02-B0001"],
        "scenes": [{
            "scene_id": "P02-S001", "ordinal": 1, "name_zh": "王宫·维克多的书房", "name_en": "Royal Palace · Viktor's office",
            "location_ref": "kings-office", "parent_location_zh": "王宫", "int_ext": "INT", "time": "DAY",
            "summary_zh": "国王维克多进入书房并命令对方过来。", "block_ids": ["P02-B0002", "P02-B0003"],
            "beats": [{"beat_id": "P02-S001-B001", "title_zh": "维克多下令", "summary_zh": "维克多进入并发出命令。", "block_ids": ["P02-B0002", "P02-B0003"]}],
            "character_refs": ["king", "viktor"],
        }],
        "characters": [
            {"ref": "king", "canonical_name_en": "the King", "name_zh": "国王", "aliases_en": [], "aliases_zh": [], "category": "named", "importance": "major", "story_role_zh": "男主"},
            {"ref": "viktor", "canonical_name_en": "Viktor", "name_zh": "维克多", "aliases_en": [], "aliases_zh": [], "category": "named", "importance": "major", "story_role_zh": "男主"},
        ],
        "character_facts": [{"fact_id": "P02-CF001", "subject_ref": "viktor", "field": "identity", "value_zh": "国王", "source_block_ids": ["P02-B0002"]}],
        "locations": [{"ref": "kings-office", "name_en": "the same office", "name_zh": "维克多的书房", "aliases_en": [], "aliases_zh": ["国王的书房"], "parent_zh": "王宫", "int_ext": "INT", "facts": []}],
        "appearances": [{"appearance_id": "P02-AP001", "character_ref": "viktor", "scene_id": "P02-S001", "presence": "actual", "summary_zh": "进入书房并下令。", "source_block_ids": ["P02-B0002", "P02-B0003"]}],
        "identity_links": [{"link_id": "P02-IL001", "entity_type": "character", "left_ref": "king", "right_ref": "viktor", "relationship": "same", "note_zh": "原文将 King 与 Viktor 连写为同一称谓。", "source_block_ids": ["P02-B0002"]}],
        "asset_candidates": [], "issues": [],
    }


class V3PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "book.txt"
        self.source.write_text(SOURCE, encoding="utf-8", newline="")
        self.project = self.root / "project"
        ingest_source(self.source, self.project, "Lean Test")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _complete_chapters(self) -> dict:
        state = run_pipeline(self.project)
        self.assertEqual(state["next_task"]["chapter_id"], "P01")
        self.assertLessEqual(state["next_task"]["budget"]["rule_and_context_overhead_bytes"], 8192)
        manifest = read_json(self.project / "source" / "manifest.json")
        p01 = chapter_one(state["next_task"]["input_sha256"], manifest["chapters"][0]["sha256"])
        write_json(self.project / "data" / "chapters" / "p01.json", p01)
        state = run_pipeline(self.project)
        self.assertEqual(state["next_task"]["chapter_id"], "P02")
        p02 = chapter_two(state["next_task"]["input_sha256"], manifest["chapters"][1]["sha256"])
        write_json(self.project / "data" / "chapters" / "p02.json", p02)
        return run_pipeline(self.project)

    def test_ingest_is_lossless_and_does_not_duplicate_source_units(self) -> None:
        self.assertEqual(validate_project(self.project), [])
        manifest = read_json(self.project / "source" / "manifest.json")
        reconstructed = "".join((self.project / item["source_path"]).read_text(encoding="utf-8") for item in manifest["chapters"])
        self.assertEqual(reconstructed, SOURCE)
        self.assertFalse((self.project / "data" / "units").exists())
        self.assertEqual([item["chapter_id"] for item in manifest["chapters"]], ["P01", "P02"])

    def test_full_checkpoint_flow_builds_clean_markdown_and_alias_redirect(self) -> None:
        state = self._complete_chapters()
        self.assertEqual(state["status"], "ready_for_review")
        self.assertEqual(validate_pipeline(self.project), [])
        catalog = read_json(self.project / "data" / "catalogs" / "catalog.json")
        self.assertEqual(catalog["character_alias_index"]["king"], catalog["character_alias_index"]["viktor"])
        self.assertEqual(len(catalog["characters"]), 2)
        script = (self.project / "deliverables" / "scripts_bilingual" / "p01.md").read_text(encoding="utf-8")
        self.assertIn("P01-S001｜内景·王宫·国王的书房｜时间待确认", script)
        self.assertIn("薇奥莱特 / Violette（对白）", script)
        review = (self.project / "work" / "review" / "p01.review.md").read_text(encoding="utf-8")
        self.assertIn("ISS-P01-0001", review)
        self.assertIn("中文参考", review)
        self.assertNotIn("当前字段", review)
        global_review = (self.project / "deliverables" / "待确认问题汇总.md").read_text(encoding="utf-8")
        self.assertIn("“What?”", global_review)
        self.assertIn("“什么？”", global_review)
        self.assertTrue((self.project / "deliverables" / "全文美术资产候选清单.md").exists())

    def test_changed_english_is_rejected_and_output_budget_is_enforced(self) -> None:
        state = run_pipeline(self.project)
        manifest = read_json(self.project / "source" / "manifest.json")
        record = chapter_one(state["next_task"]["input_sha256"], manifest["chapters"][0]["sha256"])
        record["blocks"][2]["text_en"] = "Changed\n"
        write_json(self.project / "data" / "chapters" / "p01.json", record)
        errors = validate_analysis(self.project, "P01")
        self.assertTrue(any("reconstruct" in error for error in errors))
        repair = run_pipeline(self.project)
        self.assertEqual(repair["status"], "needs_repair")
        self.assertEqual(repair["next_task"]["task_type"], "repair_chapter")
        self.assertEqual(repair["next_task"]["chapter_input_sha256"], state["next_task"]["input_sha256"])

    def test_targeted_review_changes_translation_and_can_be_retracted(self) -> None:
        self._complete_chapters()
        event = append_event(self.project, "P01", "set", "P01-B0003", "text_zh", "“怎么？”", "调整译法", "tester")
        current, _, _ = load_current_analysis(self.project, "P01")
        self.assertEqual(current["blocks"][2]["text_zh"], "“怎么？”")
        with self.assertRaises(ValueError):
            append_event(self.project, "P01", "set", "P01-B0003", "text_en", "Changed", "禁止改英文", "tester")
        append_event(self.project, "P01", "retract", None, None, None, "撤销译法", "tester", event["event_id"])
        current, _, _ = load_current_analysis(self.project, "P01")
        self.assertEqual(current["blocks"][2]["text_zh"], "“什么？”")

    def test_entity_merge_event_is_invisible_downstream_and_retractable(self) -> None:
        self._complete_chapters()
        catalog = read_json(self.project / "data" / "catalogs" / "catalog.json")
        violette = catalog["character_alias_index"]["violette"]
        viktor = catalog["character_alias_index"]["viktor"]
        event = entity_event(self.project, "merge", "character", source_id=violette, target_id=viktor, note="测试身份归并", actor="tester")
        merged = read_json(self.project / "data" / "catalogs" / "catalog.json")
        self.assertEqual(merged["character_alias_index"]["violette"], merged["character_alias_index"]["viktor"])
        entity_event(self.project, "retract", "character", target_event_id=event["event_id"], note="撤销测试归并", actor="tester")
        restored = read_json(self.project / "data" / "catalogs" / "catalog.json")
        self.assertNotEqual(restored["character_alias_index"]["violette"], restored["character_alias_index"]["viktor"])

    def test_asset_decisions_and_review_completion(self) -> None:
        self._complete_chapters()
        append_event(self.project, "P01", "resolve_issue", "ISS-P01-0001", None, "原文未说明，接受时间待确认。", "完成全书复盘", "tester")
        build_catalogs(self.project)
        catalog = read_json(self.project / "data" / "catalogs" / "catalog.json")
        active = [item for item in catalog["assets"] if not item.get("merged_into")]
        for item in active:
            asset_event(self.project, "decide", asset_id=item["asset_id"], status="approved", note="测试确认", actor="tester")
        state = run_pipeline(self.project)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["review_summary"], {"pending_issues": 0, "pending_assets": 0})

    def test_asset_merge_split_and_retraction_are_rebuildable(self) -> None:
        self._complete_chapters()
        catalog = read_json(self.project / "data" / "catalogs" / "catalog.json")
        location = next(item for item in catalog["assets"] if item["type"] == "location" and not item.get("merged_into"))
        prop = next(item for item in catalog["assets"] if item["type"] == "prop" and not item.get("merged_into"))
        merge = asset_event(self.project, "merge", asset_id=prop["asset_id"], target_asset_id=location["asset_id"], note="测试共用", actor="tester")
        merged = read_json(self.project / "data" / "catalogs" / "catalog.json")
        self.assertEqual(merged["asset_redirects"][prop["asset_id"]], location["asset_id"])
        asset_event(self.project, "retract", target_event_id=merge["event_id"], note="撤销共用", actor="tester")
        restored = read_json(self.project / "data" / "catalogs" / "catalog.json")
        self.assertNotIn(prop["asset_id"], restored["asset_redirects"])
        split = asset_event(self.project, "split", asset_id=location["asset_id"], name_zh="书房第一章状态", scene_ids=["P01-S001"], note="测试拆分", actor="tester")
        split_catalog = read_json(self.project / "data" / "catalogs" / "catalog.json")
        self.assertTrue(any(item["source"] == "user_split" and item["scene_ids"] == ["P01-S001"] for item in split_catalog["assets"]))
        asset_event(self.project, "retract", target_event_id=split["event_id"], note="撤销拆分", actor="tester")
        final_catalog = read_json(self.project / "data" / "catalogs" / "catalog.json")
        self.assertFalse(any(item["source"] == "user_split" for item in final_catalog["assets"]))

    def test_single_markdown_can_be_converted_to_docx_on_request(self) -> None:
        self._complete_chapters()
        markdown = self.project / "deliverables" / "角色信息库.md"
        output = self.project / "deliverables" / "角色信息库.docx"
        convert(markdown, output)
        self.assertTrue(output.exists())
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("word/document.xml", archive.namelist())

    def test_incremental_cache_reuses_unchanged_chapters(self) -> None:
        self._complete_chapters()
        _, first = build_catalogs(self.project)
        _, second = build_catalogs(self.project)
        self.assertEqual(first["updated_chapters"], 0)
        self.assertEqual(second, {"reused_chapters": 2, "updated_chapters": 0})
        self.assertEqual(validate_catalogs(self.project), [])


if __name__ == "__main__":
    unittest.main()
