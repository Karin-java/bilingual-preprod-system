from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_source import ingest_source  # noqa: E402
from prepare_analysis import prepare_packet  # noqa: E402
from render_chapter import render_chapter  # noqa: E402
from review_analysis import append_event, materialize  # noqa: E402
from validate_analysis import validate_analysis  # noqa: E402
from validate_render import validate_render  # noqa: E402


def evidence(unit_id: str, quote: str) -> list[dict[str, str]]:
    return [{"source_unit_id": unit_id, "quote": quote}]


def segment(unit_id: str, ordinal: int, start: int, text: str, text_type: str, speaker=None) -> dict:
    return {
        "schema_version": "2.0.0",
        "segment_id": f"{unit_id}-G{ordinal:03d}",
        "unit_id": unit_id,
        "ordinal": ordinal,
        "start": start,
        "end": start + len(text),
        "source_text": text,
        "translation": {"text_zh": f"中文译文：{text}", "status": "agent_draft"},
        "text_type": text_type,
        "classification": {"status": "explicit", "confidence": 1, "evidence": evidence(unit_id, text)},
        "speaker": speaker,
    }


class AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source = self.root / "story.txt"
        source.write_bytes(
            b"Chapter 1\n"
            b"At night, Violette entered the king's study.\n"
            b"Violette bowed. \"Your slave is here, my King.\"\n"
            b"The king looked up.\n"
        )
        self.project = ingest_source(source, self.root / "project", "Analysis Test")
        self.packet_path, self.packet_hash = prepare_packet(self.project, "P01")
        self.packet = json.loads(self.packet_path.read_text(encoding="utf-8"))
        self.units = {unit["unit_id"]: unit["source_text"] for unit in self.packet["units"]}
        self.analysis = self.make_valid_analysis()
        self.write_analysis(self.analysis)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_valid_analysis(self) -> dict:
        u1, u2, u3, u4 = [f"P01-U{i:04d}" for i in range(1, 5)]
        heading = "Chapter 1"
        action2 = "At night, Violette entered the king's study."
        action3 = "Violette bowed. "
        dialogue = '"Your slave is here, my King."'
        action4 = "The king looked up."
        speaker = {
            "kind": "named",
            "canonical_label": "Violette",
            "chinese_label": "薇奥莱特",
            "character_id": None,
            "status": "explicit",
            "confidence": 1,
            "evidence": evidence(u3, "Violette bowed."),
        }
        assessment = lambda value, quote: {
            "value": value,
            "status": "inferred",
            "confidence": 0.9,
            "evidence": evidence(u2, quote),
        }
        return {
            "schema_version": "2.0.0",
            "analysis_method": "agent-semantic-v1",
            "chapter_id": "P01",
            "source_sha256": self.packet["source_sha256"],
            "input_packet_sha256": self.packet_hash,
            "source_unit_ids": self.packet["source_unit_ids"],
            "unit_analyses": [
                {"unit_id": u1, "segments": [segment(u1, 1, 0, heading, "chapter_heading")]},
                {"unit_id": u2, "segments": [segment(u2, 1, 0, action2, "action")]},
                {"unit_id": u3, "segments": [
                    segment(u3, 1, 0, action3, "action"),
                    segment(u3, 2, len(action3), dialogue, "dialogue", speaker),
                ]},
                {"unit_id": u4, "segments": [segment(u4, 1, 0, action4, "action")]},
            ],
            "scenes": [{
                "scene_id": "P01-S001",
                "ordinal": 1,
                "summary_zh": "薇奥莱特夜间进入国王书房并向国王行礼。",
                "location": {
                    "location_id": None,
                    "source_text": "king's study",
                    "standardized_name": "国王的书房",
                    "parent_location": "王宫",
                    "sub_location": "书房",
                    "status": "explicit",
                    "confidence": 1,
                    "evidence": evidence(u2, "king's study"),
                },
                "int_ext": assessment("INT", "entered the king's study"),
                "time_of_day": assessment("night", "At night"),
                "reality_layer": assessment("present", action2),
                "source_unit_ids": [u2, u3, u4],
                "boundary_evidence": evidence(u2, action2),
                "beats": [{
                    "beat_id": "P01-S001-B001",
                    "ordinal": 1,
                    "summary_zh": "薇奥莱特进入书房、行礼并开口。",
                    "change_type": "opening",
                    "source_unit_ids": [u2, u3, u4],
                    "status": "explicit",
                    "confidence": 1,
                    "evidence": evidence(u2, action2),
                }],
            }],
            "review": {"status": "ready", "issues": []},
        }

    def write_analysis(self, analysis: dict) -> None:
        target = self.project / self.packet["output_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_valid_mixed_action_and_dialogue(self) -> None:
        self.assertEqual(validate_analysis(self.project, "P01"), [])
        mixed = self.analysis["unit_analyses"][2]["segments"]
        self.assertEqual([item["text_type"] for item in mixed], ["action", "dialogue"])

    def test_uncovered_non_whitespace_is_rejected(self) -> None:
        changed = copy.deepcopy(self.analysis)
        dialogue = changed["unit_analyses"][2]["segments"][1]
        dialogue["ordinal"] = 1
        dialogue["segment_id"] = "P01-U0003-G001"
        changed["unit_analyses"][2]["segments"] = [dialogue]
        self.write_analysis(changed)
        self.assertTrue(any("uncovered non-whitespace" in error for error in validate_analysis(self.project, "P01")))

    def test_fabricated_evidence_is_rejected(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["scenes"][0]["location"]["evidence"][0]["quote"] = "a location absent from source"
        self.write_analysis(changed)
        self.assertTrue(any("evidence quote is not present" in error for error in validate_analysis(self.project, "P01")))

    def test_missing_chinese_translation_is_rejected(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["unit_analyses"][1]["segments"][0]["translation"]["text_zh"] = ""
        self.write_analysis(changed)
        self.assertTrue(any("translation.text_zh is empty" in error for error in validate_analysis(self.project, "P01")))

    def test_beats_must_partition_scene(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["scenes"][0]["beats"][0]["source_unit_ids"].pop()
        self.write_analysis(changed)
        self.assertTrue(any("beats must partition" in error for error in validate_analysis(self.project, "P01")))

    def test_unknown_speaker_requires_review(self) -> None:
        changed = copy.deepcopy(self.analysis)
        speaker = changed["unit_analyses"][2]["segments"][1]["speaker"]
        speaker.update({"kind": "unknown", "canonical_label": None, "chinese_label": None, "status": "unknown", "confidence": 0})
        changed["review"] = {
            "status": "provisional",
            "issues": [{
                "issue_id": "ISS-P01-0001",
                "scope_type": "segment",
                "scope_id": "P01-U0003-G002",
                "field_path": "speaker.canonical_label",
                "code": "unknown_speaker",
                "message": "说话人尚未确认",
                "source_unit_ids": ["P01-U0003"],
            }],
        }
        self.write_analysis(changed)
        self.assertEqual(validate_analysis(self.project, "P01"), [])

    def test_unknown_without_pending_issue_is_rejected(self) -> None:
        changed = copy.deepcopy(self.analysis)
        speaker = changed["unit_analyses"][2]["segments"][1]["speaker"]
        speaker.update({"kind": "unknown", "canonical_label": None, "chinese_label": None, "status": "unknown", "confidence": 0})
        self.write_analysis(changed)
        self.assertTrue(any("review.status = provisional" in error for error in validate_analysis(self.project, "P01")))

    def test_scene_override_is_append_only_and_targeted(self) -> None:
        changed = copy.deepcopy(self.analysis)
        scene = changed["scenes"][0]
        scene["time_of_day"] = {
            "value": "unknown",
            "status": "unknown",
            "confidence": 0,
            "evidence": evidence("P01-U0002", "At night"),
        }
        changed["review"] = {
            "status": "provisional",
            "issues": [{
                "issue_id": "ISS-P01-0001",
                "scope_type": "scene",
                "scope_id": "P01-S001",
                "field_path": "time_of_day.value",
                "code": "time_unconfirmed",
                "message": "场景时间待确认",
                "source_unit_ids": ["P01-U0002"],
            }],
        }
        self.write_analysis(changed)
        base_path = self.project / "data" / "analysis" / "p01.analysis.json"
        base_before = base_path.read_bytes()
        event = append_event(self.project, "P01", {
            "actor": {"type": "user", "label": "验收人"},
            "scope_type": "scene",
            "scope_id": "P01-S001",
            "operation": "set_field",
            "field_path": "time_of_day.value",
            "value": "night",
            "note": "原文明确写明 At night",
            "resolves_issue_ids": ["ISS-P01-0001"],
        })
        resolved_path, _, manifest = materialize(self.project, "P01")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        self.assertEqual(base_path.read_bytes(), base_before)
        self.assertEqual(event["event_id"], "REV-P01-000001")
        self.assertEqual(resolved["scenes"][0]["time_of_day"]["value"], "night")
        self.assertEqual(resolved["scenes"][0]["time_of_day"]["status"], "user_confirmed")
        self.assertEqual(resolved["review"], {"status": "ready", "issues": []})
        self.assertEqual(manifest["open_issue_ids"], [])

    def test_retract_restores_base_value(self) -> None:
        first = append_event(self.project, "P01", {
            "actor": {"type": "user", "label": "验收人"},
            "scope_type": "scene",
            "scope_id": "P01-S001",
            "operation": "set_field",
            "field_path": "summary_zh",
            "value": "人工修订摘要",
            "note": "修订摘要",
        })
        append_event(self.project, "P01", {
            "actor": {"type": "user", "label": "验收人"},
            "scope_type": "scene",
            "scope_id": "P01-S001",
            "operation": "retract_event",
            "note": "撤回该修订",
            "target_event_id": first["event_id"],
        })
        resolved_path, _, manifest = materialize(self.project, "P01")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        self.assertEqual(resolved["scenes"][0]["summary_zh"], self.analysis["scenes"][0]["summary_zh"])
        self.assertEqual(manifest["retracted_event_ids"], [first["event_id"]])

    def test_translation_override_is_recorded_in_resolved_view(self) -> None:
        append_event(self.project, "P01", {
            "actor": {"type": "user", "label": "验收人"},
            "scope_type": "segment",
            "scope_id": "P01-U0003-G002",
            "operation": "set_field",
            "field_path": "translation.text_zh",
            "value": "“您的奴隶在此，吾王。”",
            "note": "确认正式称谓译法",
        })
        resolved_path, _, _ = materialize(self.project, "P01")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        translation = resolved["unit_analyses"][2]["segments"][1]["translation"]
        self.assertEqual(translation["text_zh"], "“您的奴隶在此，吾王。”")
        self.assertEqual(translation["status"], "user_confirmed")

    def test_user_can_accept_an_irreducible_unknown(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["scenes"][0]["time_of_day"] = {
            "value": "unknown",
            "status": "unknown",
            "confidence": 0,
            "evidence": evidence("P01-U0002", "At night"),
        }
        changed["review"] = {
            "status": "provisional",
            "issues": [{
                "issue_id": "ISS-P01-0001",
                "scope_type": "scene",
                "scope_id": "P01-S001",
                "field_path": "time_of_day.value",
                "code": "time_unconfirmed",
                "message": "场景时间无法进一步确认",
                "source_unit_ids": ["P01-U0002"],
            }],
        }
        self.write_analysis(changed)
        append_event(self.project, "P01", {
            "actor": {"type": "user", "label": "验收人"},
            "scope_type": "scene",
            "scope_id": "P01-S001",
            "operation": "add_note",
            "note": "接受未知，不做剧情外推断",
            "resolves_issue_ids": ["ISS-P01-0001"],
        })
        resolved_path, _, manifest = materialize(self.project, "P01")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        self.assertEqual(resolved["scenes"][0]["time_of_day"]["value"], "unknown")
        self.assertEqual(resolved["review"]["status"], "ready")
        self.assertEqual(manifest["resolved_issue_ids"], ["ISS-P01-0001"])
        self.assertEqual(manifest["review_notes"][0]["scope_id"], "P01-S001")

    def test_packet_hash_binds_analysis_to_input(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["input_packet_sha256"] = hashlib.sha256(b"other packet").hexdigest()
        self.write_analysis(changed)
        self.assertIn("analysis packet hash mismatch", validate_analysis(self.project, "P01"))

    def test_packet_generation_is_deterministic(self) -> None:
        path, packet_hash = prepare_packet(self.project, "P01")
        self.assertEqual(path, self.packet_path)
        self.assertEqual(packet_hash, self.packet_hash)

    def test_bilingual_markdown_render_is_deterministic(self) -> None:
        script_path, review_path, _, _ = render_chapter(self.project, "P01")
        first_script = script_path.read_bytes()
        first_review = review_path.read_bytes()
        render_chapter(self.project, "P01")
        self.assertEqual(script_path.read_bytes(), first_script)
        self.assertEqual(review_path.read_bytes(), first_review)
        self.assertIn('"Your slave is here, my King."', first_script.decode("utf-8"))
        self.assertIn('中文译文："Your slave is here, my King."', first_script.decode("utf-8"))
        self.assertEqual(validate_render(self.project, "P01"), [])
        self.assertFalse(any(self.project.rglob("*.docx")))

    def test_review_sheet_contains_bilingual_evidence(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["scenes"][0]["time_of_day"] = {
            "value": "unknown",
            "status": "unknown",
            "confidence": 0,
            "evidence": evidence("P01-U0002", "At night"),
        }
        changed["review"] = {
            "status": "provisional",
            "issues": [{
                "issue_id": "ISS-P01-0001",
                "scope_type": "scene",
                "scope_id": "P01-S001",
                "field_path": "time_of_day.value",
                "code": "time_unconfirmed",
                "message": "场景时间待确认",
                "source_unit_ids": ["P01-U0002"],
            }],
        }
        self.write_analysis(changed)
        _, review_path, _, _ = render_chapter(self.project, "P01")
        review = review_path.read_text(encoding="utf-8")
        self.assertIn("## ISS-P01-0001\n", review)
        self.assertNotIn("## ISS-P01-0001｜", review)
        self.assertNotIn("当前字段", review)
        self.assertNotIn("当前值", review)
        self.assertNotIn("#### P01-U0002", review)
        self.assertIn("#### 线索 1", review)
        self.assertGreaterEqual(review.count("\n---\n"), 2)
        self.assertIn("At night", review)
        self.assertIn("中文译文：At night, Violette entered the king's study.", review)
        self.assertEqual(validate_render(self.project, "P01"), [])

    def test_render_tampering_is_detected(self) -> None:
        script_path, _, _, _ = render_chapter(self.project, "P01")
        script_path.write_text("tampered\n", encoding="utf-8")
        self.assertTrue(any("script hash mismatch" in error for error in validate_render(self.project, "P01")))


if __name__ == "__main__":
    unittest.main()
