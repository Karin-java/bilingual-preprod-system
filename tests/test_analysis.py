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
from build_appearance_reports import build_appearance_reports, render_chapter_table  # noqa: E402
from build_profiles import build_profiles  # noqa: E402
from build_registries import build_registries, resolve_character_reference  # noqa: E402
from manage_character_identities import append_identity_event  # noqa: E402
from manage_profile_decisions import append_event as append_profile_event  # noqa: E402
from prepare_analysis import prepare_packet  # noqa: E402
from render_chapter import render_chapter  # noqa: E402
from review_analysis import append_event, materialize  # noqa: E402
from validate_analysis import validate_analysis  # noqa: E402
from validate_appearance_reports import validate_appearance_reports  # noqa: E402
from validate_profiles import validate_profiles  # noqa: E402
from validate_render import validate_render  # noqa: E402
from validate_registries import validate_registries  # noqa: E402


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
            "character_observations": [
                {
                    "observation_id": "COBS-P01-0001",
                    "entity_key": "violette",
                    "matched_character_id": None,
                    "canonical_label": "Violette",
                    "chinese_label": "薇奥莱特",
                    "aliases": [],
                    "chinese_aliases": [],
                    "summary_zh": "薇奥莱特进入书房并向国王行礼。",
                    "character_type": "named",
                    "importance_hint": "candidate_major",
                    "scene_id": "P01-S001",
                    "presence_type": "physical",
                    "has_dialogue": True,
                    "speaker_segment_ids": ["P01-U0003-G002"],
                    "source_unit_ids": [u2, u3],
                    "status": "explicit",
                    "confidence": 1,
                    "evidence": evidence(u2, "Violette"),
                },
                {
                    "observation_id": "COBS-P01-0002",
                    "entity_key": "king",
                    "matched_character_id": None,
                    "canonical_label": "King",
                    "chinese_label": "国王",
                    "aliases": ["the king", "my King"],
                    "chinese_aliases": [],
                    "summary_zh": "国王在书房接受薇奥莱特的行礼。",
                    "character_type": "role",
                    "importance_hint": "candidate_major",
                    "scene_id": "P01-S001",
                    "presence_type": "physical",
                    "has_dialogue": False,
                    "speaker_segment_ids": [],
                    "source_unit_ids": [u3, u4],
                    "status": "explicit",
                    "confidence": 1,
                    "evidence": evidence(u4, "The king"),
                },
            ],
            "profile_observations": [],
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
        changed["character_observations"][0]["has_dialogue"] = False
        changed["character_observations"][0]["speaker_segment_ids"] = []
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

    def test_character_observation_can_be_corrected_without_rewriting_base(self) -> None:
        base_path = self.project / "data" / "analysis" / "p01.analysis.json"
        base_before = base_path.read_bytes()
        append_event(self.project, "P01", {
            "actor": {"type": "user", "label": "验收人"},
            "scope_type": "character_observation",
            "scope_id": "COBS-P01-0002",
            "operation": "set_field",
            "field_path": "chinese_label",
            "value": "本国国王",
            "note": "与维克托国王区分",
        })
        resolved_path, _, _ = materialize(self.project, "P01")
        resolved = json.loads(resolved_path.read_text(encoding="utf-8"))
        self.assertEqual(base_path.read_bytes(), base_before)
        self.assertEqual(resolved["character_observations"][1]["chinese_label"], "本国国王")
        self.assertEqual(resolved["character_observations"][1]["status"], "user_confirmed")

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

    def test_entity_registry_is_stable_and_readable(self) -> None:
        registry_path, review_path, registry = build_registries(self.project)
        first_registry = registry_path.read_bytes()
        first_review = review_path.read_bytes()
        self.assertEqual([item["character_id"] for item in registry["characters"]], ["CHAR-0001", "CHAR-0002"])
        self.assertEqual([item["canonical_name"] for item in registry["characters"]], ["Violette", "King"])
        self.assertEqual(registry["locations"][0]["location_id"], "LOC-0001")
        self.assertIn("CHAR-0001｜Violette / 薇奥莱特", first_review.decode("utf-8"))
        self.assertIn("类型：具名角色", first_review.decode("utf-8"))
        self.assertNotIn("candidate_major", first_review.decode("utf-8"))
        build_registries(self.project)
        self.assertEqual(registry_path.read_bytes(), first_registry)
        self.assertEqual(review_path.read_bytes(), first_review)
        self.assertEqual(validate_registries(self.project), [])

    def test_registry_tampering_is_detected(self) -> None:
        registry_path, _, registry = build_registries(self.project)
        registry["characters"][0]["canonical_name"] = "Tampered"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertTrue(any("deterministic rebuild" in error for error in validate_registries(self.project)))

    def test_analysis_packet_includes_compact_registry_context(self) -> None:
        build_registries(self.project)
        packet_path, _ = prepare_packet(self.project, "P01", replace=True)
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        context = packet["registry_context"]
        self.assertEqual(context["registry_version"], "entity-registry-v2")
        self.assertEqual([item["character_id"] for item in context["characters"]], ["CHAR-0001", "CHAR-0002"])
        self.assertEqual(context["locations"][0]["location_id"], "LOC-0001")

    def add_king_viktor_observation(self, matched_character_id=None, presence_type="mentioned") -> None:
        changed = copy.deepcopy(self.analysis)
        observation = {
            "observation_id": "COBS-P01-0003",
            "entity_key": "king-viktor",
            "matched_character_id": matched_character_id,
            "canonical_label": "King Viktor",
            "chinese_label": "维克托国王",
            "aliases": ["Viktor"],
            "chinese_aliases": ["维克托"],
            "summary_zh": "维克托国王在书房中出现。",
            "character_type": "named",
            "importance_hint": "candidate_major",
            "scene_id": "P01-S001",
            "presence_type": presence_type,
            "has_dialogue": False,
            "speaker_segment_ids": [],
            "source_unit_ids": ["P01-U0004"],
            "status": "explicit",
            "confidence": 1,
            "evidence": evidence("P01-U0004", "The king"),
        }
        existing_index = next((index for index, item in enumerate(changed["character_observations"]) if item["entity_key"] == "king-viktor"), None)
        if existing_index is None:
            changed["character_observations"].append(observation)
        else:
            changed["character_observations"][existing_index] = observation
        self.analysis = changed
        self.write_analysis(changed)

    def test_confirmed_identity_merge_is_invisible_to_downstream(self) -> None:
        self.add_king_viktor_observation()
        base_before = (self.project / "data" / "analysis" / "p01.analysis.json").read_bytes()
        _, _, before = build_registries(self.project)
        self.assertEqual([item["character_id"] for item in before["characters"]], ["CHAR-0001", "CHAR-0002", "CHAR-0003"])
        event = append_identity_event(self.project, {
            "operation": "merge_characters",
            "source_character_id": "CHAR-0003",
            "target_character_id": "CHAR-0002",
            "canonical_name": "Viktor",
            "chinese_name": "维克托",
            "aliases": ["King"],
            "chinese_aliases": ["国王"],
            "source_observation_ids": ["COBS-P01-0003"],
            "actor": {"type": "user", "label": "验收人"},
            "note": "确认国王与 Viktor 是同一角色",
        })
        registry = json.loads((self.project / "data" / "registries" / "entities.json").read_text(encoding="utf-8"))
        self.assertEqual((self.project / "data" / "analysis" / "p01.analysis.json").read_bytes(), base_before)
        self.assertEqual([item["character_id"] for item in registry["characters"]], ["CHAR-0001", "CHAR-0002"])
        viktor = registry["characters"][1]
        self.assertEqual((viktor["canonical_name"], viktor["chinese_name"]), ("Viktor", "维克托"))
        self.assertEqual(registry["character_key_index"]["king"], "CHAR-0002")
        self.assertEqual(registry["character_key_index"]["king-viktor"], "CHAR-0002")
        self.assertEqual(registry["character_id_redirects"], {"CHAR-0003": "CHAR-0002"})
        for reference in ("King", "国王", "Viktor", "维克托", "CHAR-0003"):
            self.assertEqual(resolve_character_reference(registry, reference), {"status": "resolved", "character_id": "CHAR-0002"})
        self.assertEqual(viktor["identity_event_ids"], [event["event_id"]])
        self.assertEqual(validate_registries(self.project), [])
        packet_path, _ = prepare_packet(self.project, "P01", replace=True)
        context = json.loads(packet_path.read_text(encoding="utf-8"))["registry_context"]
        context_viktor = next(item for item in context["characters"] if item["character_id"] == "CHAR-0002")
        self.assertEqual(context_viktor["canonical_name"], "Viktor")
        self.assertIn("King", context_viktor["aliases"])
        self.assertNotIn("CHAR-0003", [item["character_id"] for item in context["characters"]])

    def test_identity_merge_can_be_retracted_without_rewriting_analysis(self) -> None:
        self.add_king_viktor_observation()
        build_registries(self.project)
        merge = append_identity_event(self.project, {
            "operation": "merge_characters", "source_character_id": "CHAR-0003", "target_character_id": "CHAR-0002",
            "canonical_name": "Viktor", "chinese_name": "维克托", "note": "确认同一角色",
        })
        append_identity_event(self.project, {
            "operation": "retract_event", "target_event_id": merge["event_id"], "note": "撤销错误归并",
        })
        registry = json.loads((self.project / "data" / "registries" / "entities.json").read_text(encoding="utf-8"))
        self.assertEqual([item["character_id"] for item in registry["characters"]], ["CHAR-0001", "CHAR-0002", "CHAR-0003"])
        self.assertEqual(registry["character_id_redirects"], {})
        self.assertEqual(registry["identity_event_log"]["retracted_event_ids"], [merge["event_id"]])
        self.assertEqual(validate_registries(self.project), [])

    def test_matched_id_conflict_becomes_bilingual_review_candidate(self) -> None:
        self.add_king_viktor_observation()
        build_registries(self.project)
        self.add_king_viktor_observation(matched_character_id="CHAR-0002")
        _, review_path, registry = build_registries(self.project)
        self.assertEqual(registry["identity_candidates"][0]["candidate_id"], "IDN-0001")
        review = review_path.read_text(encoding="utf-8")
        self.assertIn("### IDN-0001", review)
        self.assertIn("- 原文：The king", review)
        self.assertIn("- 中文参考：", review)
        self.assertGreaterEqual(review.count("\n---\n"), 2)
        self.assertEqual(validate_registries(self.project), [])

    def test_shared_role_name_remains_ambiguous_until_confirmed(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["character_observations"].append({
            "observation_id": "COBS-P01-0003", "entity_key": "northern-king", "matched_character_id": None,
            "canonical_label": "Northern King", "chinese_label": "北境国王", "aliases": ["King"], "chinese_aliases": ["国王"],
            "character_type": "role", "importance_hint": "supporting", "scene_id": "P01-S001", "presence_type": "mentioned",
            "has_dialogue": False, "speaker_segment_ids": [], "source_unit_ids": ["P01-U0004"], "status": "explicit", "confidence": 1,
            "evidence": evidence("P01-U0004", "The king"),
        })
        self.write_analysis(changed)
        _, _, registry = build_registries(self.project)
        result = resolve_character_reference(registry, "King")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["character_ids"], ["CHAR-0002", "CHAR-0003"])

    def test_appearance_reports_are_stable_readable_and_markdown_only(self) -> None:
        build_registries(self.project)
        data_path, chapter_path, major_path, bundle = build_appearance_reports(self.project)
        first = (data_path.read_bytes(), chapter_path.read_bytes(), major_path.read_bytes())
        self.assertEqual(len(bundle["appearances"]), 2)
        self.assertEqual(len(bundle["chapter_rows"]), 2)
        self.assertEqual(len(bundle["major_character_scenes"]), 2)
        chapter = chapter_path.read_text(encoding="utf-8")
        major = major_path.read_text(encoding="utf-8")
        self.assertIn("| 角色 | P01 |", chapter)
        self.assertIn("| Violette / 薇奥莱特 | 现实：场1；台词：场1 |", chapter)
        self.assertNotIn("| 角色 | 章节 |", chapter)
        self.assertIn("## 国王 / King", major)
        self.assertIn("**基本信息**：主要角色候选", major)
        self.assertIn("| 出场章节 | 出场场景（原文位置） | 时间 | 备注 |", major)
        self.assertIn("| P01 | 国王的书房 | 夜·内 |", major)
        self.assertIn("国王在书房接受薇奥莱特的行礼。", major)
        self.assertFalse(any(self.project.rglob("*.docx")))
        build_appearance_reports(self.project)
        self.assertEqual((data_path.read_bytes(), chapter_path.read_bytes(), major_path.read_bytes()), first)
        self.assertEqual(validate_appearance_reports(self.project), [])

    def test_chapter_appearance_deliverable_is_character_by_chapter_matrix(self) -> None:
        _, _, registry = build_registries(self.project)
        _, _, _, bundle = build_appearance_reports(self.project)
        bundle = copy.deepcopy(bundle)
        bundle["analysis_inputs"].append({"chapter_id": "P03", "path": "data/analysis/p03.analysis.json", "sha256": "0" * 64})
        scene_by_id = {scene["scene_id"]: scene for scene in self.analysis["scenes"]}
        matrix = render_chapter_table(bundle, registry, scene_by_id)
        self.assertIn("| 角色 | P01 | P03 |", matrix)
        self.assertEqual(matrix.count("| Violette / 薇奥莱特 |"), 1)
        self.assertIn("| Violette / 薇奥莱特 | 现实：场1；台词：场1 | — |", matrix)
        self.assertEqual(matrix.count("| King / 国王 |"), 1)

    def test_merged_alias_observations_count_as_one_major_scene(self) -> None:
        self.add_king_viktor_observation(presence_type="physical")
        build_registries(self.project)
        append_identity_event(self.project, {
            "operation": "merge_characters", "source_character_id": "CHAR-0003", "target_character_id": "CHAR-0002",
            "canonical_name": "Viktor", "chinese_name": "维克托", "note": "确认国王与 Viktor 是同一角色",
        })
        _, chapter_path, major_path, bundle = build_appearance_reports(self.project)
        viktor_rows = [item for item in bundle["major_character_scenes"] if item["character_id"] == "CHAR-0002"]
        self.assertEqual(len(viktor_rows), 1)
        self.assertEqual(viktor_rows[0]["appearance_ids"], ["APP-P01-0002", "APP-P01-0003"])
        self.assertEqual(chapter_path.read_text(encoding="utf-8").count("| Viktor / 维克托 |"), 1)
        major = major_path.read_text(encoding="utf-8")
        self.assertEqual(major.count("## 维克托 / Viktor"), 1)
        self.assertEqual(major.count("| 出场章节 | 出场场景（原文位置） | 时间 | 备注 |"), 2)
        self.assertEqual(major.count("| P01 | 国王的书房 | 夜·内 |"), 2)
        self.assertEqual(validate_appearance_reports(self.project), [])

    def test_appearance_report_tampering_is_detected(self) -> None:
        _, chapter_path, _, _ = build_appearance_reports(self.project)
        chapter_path.write_text("tampered\n", encoding="utf-8")
        self.assertTrue(any("chapter Markdown" in error for error in validate_appearance_reports(self.project)))

    def test_profiles_accumulate_sparse_complementary_facts_without_fabrication(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["profile_observations"] = [
            {
                "profile_observation_id": "POBS-P01-0001", "entity_key": "violette", "matched_character_id": None,
                "field": "identity", "value_zh": "国王的奴隶", "normalized_value": "slave-of-king",
                "status": "explicit", "confidence": 1, "source_unit_ids": ["P01-U0003"],
                "evidence": evidence("P01-U0003", "Your slave is here"), "note_zh": "台词明确自称奴隶。",
            },
            {
                "profile_observation_id": "POBS-P01-0002", "entity_key": "violette", "matched_character_id": None,
                "field": "identity", "value_zh": "薇奥莱特", "normalized_value": "named-violette",
                "status": "explicit", "confidence": 1, "source_unit_ids": ["P01-U0002"],
                "evidence": evidence("P01-U0002", "Violette"), "note_zh": "姓名信息与身份线索可以并存。",
            },
        ]
        self.write_analysis(changed)
        build_registries(self.project)
        _, profile_path, _, bundle = build_profiles(self.project)
        violette = next(item for item in bundle["profiles"] if item["character_id"] == "CHAR-0001")
        self.assertEqual(violette["fields"]["identity"]["status"], "explicit")
        self.assertEqual(violette["fields"]["identity"]["value_zh"], "国王的奴隶；薇奥莱特")
        self.assertEqual(violette["fields"]["age"], {"value_zh": None, "status": "unknown", "fact_ids": [], "facts": []})
        markdown = profile_path.read_text(encoding="utf-8")
        self.assertIn("| 年龄 | 待确认 | 待确认 | — |", markdown)
        self.assertEqual(validate_profiles(self.project), [])

    def test_profile_decision_updates_downstream_without_rewriting_chapter(self) -> None:
        build_registries(self.project)
        build_profiles(self.project)
        base_path = self.project / self.packet["output_path"]
        base_before = base_path.read_bytes()
        event = append_profile_event(self.project, {
            "operation": "set_field", "character_ref": "Violette", "field": "story_role",
            "value_zh": "女主", "normalized_value": "female-lead", "note": "用户确认剧情定位", "actor": "验收用户",
        })
        self.assertEqual(event["character_ref"], "CHAR-0001")
        _, profile_path, _, bundle = build_profiles(self.project)
        violette = next(item for item in bundle["profiles"] if item["character_id"] == "CHAR-0001")
        self.assertEqual(violette["fields"]["story_role"]["status"], "user_confirmed")
        self.assertEqual(violette["basic_info_summary_zh"], "女主")
        self.assertEqual(base_path.read_bytes(), base_before)
        _, _, major_path, appearance_bundle = build_appearance_reports(self.project)
        self.assertIn("**基本信息**：女主", major_path.read_text(encoding="utf-8"))
        self.assertIn("profile_input", appearance_bundle)
        self.assertEqual(validate_profiles(self.project), [])
        packet_path, _ = prepare_packet(self.project, "P01", replace=True)
        context = json.loads(packet_path.read_text(encoding="utf-8"))["registry_context"]
        violette_context = next(item for item in context["characters"] if item["character_id"] == "CHAR-0001")
        self.assertEqual(violette_context["profile_fields"]["story_role"], {"value_zh": "女主", "status": "user_confirmed"})

    def test_single_value_profile_conflict_enters_bilingual_recap(self) -> None:
        changed = copy.deepcopy(self.analysis)
        changed["profile_observations"] = [
            {
                "profile_observation_id": "POBS-P01-0001", "entity_key": "violette", "matched_character_id": None,
                "field": "gender", "value_zh": "女性", "normalized_value": "female", "status": "inferred", "confidence": 0.7,
                "source_unit_ids": ["P01-U0002"], "evidence": evidence("P01-U0002", "Violette"), "note_zh": "测试推断一。",
            },
            {
                "profile_observation_id": "POBS-P01-0002", "entity_key": "violette", "matched_character_id": None,
                "field": "gender", "value_zh": "男性", "normalized_value": "male", "status": "inferred", "confidence": 0.6,
                "source_unit_ids": ["P01-U0003"], "evidence": evidence("P01-U0003", "my King"), "note_zh": "测试推断二。",
            },
        ]
        self.write_analysis(changed)
        build_registries(self.project)
        _, _, recap_path, bundle = build_profiles(self.project)
        violette = next(item for item in bundle["profiles"] if item["character_id"] == "CHAR-0001")
        self.assertEqual(violette["fields"]["gender"]["status"], "conflict")
        recap = recap_path.read_text(encoding="utf-8")
        self.assertIn("英文：Violette", recap)
        self.assertIn("中文：中文译文：At night, Violette entered the king's study.", recap)
        self.assertEqual(validate_profiles(self.project), [])

    def test_profile_decision_can_be_retracted_without_rewriting_chapter(self) -> None:
        build_registries(self.project)
        base_path = self.project / self.packet["output_path"]
        base_before = base_path.read_bytes()
        decision = append_profile_event(self.project, {
            "operation": "set_field", "character_ref": "Violette", "field": "story_role",
            "value_zh": "女主", "normalized_value": "female-lead", "note": "临时确认", "actor": "验收用户",
        })
        append_profile_event(self.project, {
            "operation": "retract_event", "target_event_id": decision["event_id"],
            "note": "撤销临时确认", "actor": "验收用户",
        })
        _, _, _, bundle = build_profiles(self.project)
        violette = next(item for item in bundle["profiles"] if item["character_id"] == "CHAR-0001")
        self.assertEqual(violette["fields"]["story_role"]["status"], "unknown")
        self.assertEqual(bundle["decision_log"]["active_event_ids"], [])
        self.assertEqual(bundle["decision_log"]["retracted_event_ids"], [decision["event_id"]])
        self.assertEqual(base_path.read_bytes(), base_before)

    def test_profile_decision_follows_character_redirect_after_identity_merge(self) -> None:
        self.add_king_viktor_observation()
        build_registries(self.project)
        decision = append_profile_event(self.project, {
            "operation": "set_field", "character_ref": "King Viktor", "field": "story_role",
            "value_zh": "男主", "normalized_value": "male-lead", "note": "用户确认剧情定位", "actor": "验收用户",
        })
        self.assertEqual(decision["character_ref"], "CHAR-0003")
        append_identity_event(self.project, {
            "operation": "merge_characters", "source_character_id": "CHAR-0003", "target_character_id": "CHAR-0002",
            "canonical_name": "Viktor", "chinese_name": "维克托", "note": "确认国王与 Viktor 是同一角色",
        })
        _, _, _, bundle = build_profiles(self.project)
        self.assertNotIn("CHAR-0003", {item["character_id"] for item in bundle["profiles"]})
        viktor = next(item for item in bundle["profiles"] if item["character_id"] == "CHAR-0002")
        self.assertEqual(viktor["fields"]["story_role"]["value_zh"], "男主")
        self.assertEqual(viktor["fields"]["story_role"]["status"], "user_confirmed")


if __name__ == "__main__":
    unittest.main()
