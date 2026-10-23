from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import change_journal as cj


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal"
        cj.init_root(self.root)
        self.env = mock.patch.dict(os.environ, {"CODEX_SESSION_ID": "", "CODEX_THREAD_ID": "", "CODEX_TURN_ID": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def record(self, **kwargs):
        args = dict(actor="codex", source="semantic", host_id="host-a", scope_id="PROJECT",
                    phase="verified", summary="checked change", primary_path="/workspace/target",
                    verification_status="passed", verification_refs=["isolated test"])
        args.update(kwargs)
        return cj.record_event(self.root, **args)

    def query(self, **kwargs):
        return cj.compact_query(self.root, **dict({"scope_id": "PROJECT"}, **kwargs))

    def cli(self, *args, script="change_journal.py", payload=None, ok=True):
        proc = subprocess.run([sys.executable, str(SCRIPTS / script), "--root", str(self.root), *args],
                              input=json.dumps(payload) if payload is not None else None,
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0 if ok else 2, proc.stderr)
        return json.loads(proc.stdout if ok else proc.stderr), proc.stdout

    def test_summary_groups_work_and_keeps_unassigned_facts_provenance_and_open_actions(self):
        first = self.record(work_id="one", follow_ups=["check deployment"])
        latest = self.record(actor="claude", work_id="one", summary="second step")
        for i in range(3):
            self.record(summary="standalone " + str(i), occurred_at="2025-01-01T00:00:00Z")
        result = self.query()
        self.assertEqual(result["mode"], "snapshot")
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual((result["matched"], result["cards_matched"], result["omitted"]), (5, 4, 1))
        self.assertNotIn("has_more", result)  # A summary checkpoint is not a history pagination cursor.
        card = result["items"][0]
        self.assertEqual(card["event_id"], latest["event_id"])
        self.assertEqual((card["actor"], card["source"], card["event_count"]), ("claude", "semantic", 2))
        self.assertEqual(card["follow_up"]["event_id"], first["event_id"])
        self.assertEqual(result["pending_matched"], 1)
        self.assertNotIn("events", result)
        self.assertNotIn("work_items", result)

    def test_cli_default_is_budgeted_and_detail_and_show_preserve_full_evidence(self):
        summary = "跨平台变更😀" * 70
        event = self.record(summary=summary, work_id="unicode", follow_ups=["验证" * 100])
        compact, wire = self.cli("query", "--work-id", "unicode")
        self.assertLessEqual(len(wire), 6000)
        self.assertIn("summary", compact["items"][0]["truncated_fields"])
        full, _ = self.cli("show", event["event_id"])
        self.assertEqual(full["event"], json.loads(Path(event["event_path"]).read_text(encoding="utf-8")))
        detail, _ = self.cli("query", "--work-id", "unicode", "--detail")
        self.assertEqual(detail["events"][0]["summary"], full["event"]["summary"])
        self.assertEqual(detail["events"][0]["actor"], "codex")
        self.cli("query", "--work-id", "unicode", "--detail", "--cursor", compact["cursor"], ok=False)

    def test_large_unicode_output_respects_total_wire_budget(self):
        for i in range(6):
            self.record(summary="😀" * 500, work_id="w" + str(i), work_title="中" * 200,
                        actor="a" * 128, source="s" * 128, profile_id="p" * 128, host_id="h" * 128,
                        follow_ups=["😀" * 600] * 3)
        for budget in (4096, 6000, 9000, 64000):
            result, wire = self.cli("query", "--scope-id", "PROJECT", "--limit", "50", "--max-chars", str(budget))
            self.assertLessEqual(len(wire), budget)
            self.assertTrue(result["items"])
            self.assertEqual(result["omitted"], result["cards_matched"] - len(result["items"]))

    def test_unchanged_is_small_and_read_only(self):
        self.record()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        initial = self.query()
        unchanged = self.query(cursor=initial["cursor"])
        self.assertEqual(unchanged["mode"], "unchanged")
        self.assertEqual(unchanged["cursor"], initial["cursor"])
        self.assertLess(cj._wire_size(unchanged), 600)
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_delta_includes_backdated_closure_and_lesson_retirement(self):
        old = self.record(follow_ups=["verify"], lesson_key="check", lesson_trigger="before retry", lesson_check="inspect fact")
        start = self.query()
        closed = self.record(resolves_event_ids=[old["event_id"]], occurred_at="2020-01-01T00:00:00Z")
        retired = self.record(lesson_key="check", lesson_trigger="before retry", lesson_check="inspect fact", lesson_status="retired")
        result = self.query(cursor=start["cursor"])
        self.assertEqual(result["mode"], "delta")
        self.assertEqual([e["event_id"] for e in result["items"]], [closed["event_id"], retired["event_id"]])
        self.assertEqual(result["items"][0]["resolves_event_ids"], [old["event_id"]])
        self.assertEqual(result["items"][1]["lesson"]["status"], "retired")
        self.assertEqual((result["pending_matched"], result["lessons_matched"]), (0, 0))
        self.assertEqual(self.query(cursor=result["cursor"])["mode"], "unchanged")

    def test_delta_paginates_without_skipping_and_accepts_new_writes_between_pages(self):
        cursor = self.query()["cursor"]
        expected = [self.record(summary=str(i), occurred_at="2020-01-01T00:00:00Z")["event_id"] for i in range(4)]
        seen = []
        for index in range(5):
            result = self.query(cursor=cursor, limit=1)
            seen.extend(e["event_id"] for e in result["items"])
            cursor = result["cursor"]
            if index == 0:
                expected.append(self.record(summary="arrived during pagination")["event_id"])
        self.assertEqual(seen, expected)
        self.assertFalse(result["has_more"])
        self.assertEqual(self.query(cursor=cursor)["mode"], "unchanged")

    def test_budget_pages_do_not_acknowledge_omitted_changes(self):
        cursor = self.query()["cursor"]
        expected = [self.record(summary="😀" * 500, follow_ups=["😀" * 600])["event_id"] for _ in range(7)]
        seen = []
        for _ in range(8):
            result = self.query(cursor=cursor, limit=50, max_chars=4096)
            self.assertLessEqual(cj._wire_size(result), 4096)
            if result["mode"] == "unchanged":
                break
            self.assertTrue(result["items"])
            seen.extend(e["event_id"] for e in result["items"])
            cursor = result["cursor"]
        self.assertEqual(seen, expected)

    def test_delta_sees_closure_and_lesson_updates_outside_the_path_filter(self):
        old = self.record(work_id="old", follow_ups=["verify"], lesson_key="check", lesson_trigger="retry", lesson_check="inspect")
        start = self.query(scope_id="", path="/workspace/target")
        closed = self.record(work_id="another", primary_path="/workspace/elsewhere", resolves_event_ids=[old["event_id"]],
                             lesson_key="check", lesson_trigger="retry", lesson_check="inspect", lesson_status="retired")
        result = self.query(scope_id="", path="/workspace/target", cursor=start["cursor"])
        self.assertEqual(result["mode"], "delta")
        self.assertEqual(result["items"][0]["event_id"], closed["event_id"])
        self.assertEqual((result["pending_matched"], result["lessons_matched"]), (0, 0))

    def test_unrelated_writes_advance_checkpoint_without_loading_new_context(self):
        start = self.query()
        self.record(scope_id="OTHER")
        result = self.query(cursor=start["cursor"])
        self.assertEqual(result["mode"], "unchanged")
        self.assertNotEqual(start["cursor"], result["cursor"])
        wanted = self.record()
        self.assertEqual(self.query(cursor=result["cursor"])["items"][0]["event_id"], wanted["event_id"])

    def test_imported_legacy_closure_outside_path_is_not_reported_as_unchanged(self):
        old = self.record(follow_ups=["verify"])
        start = self.query(scope_id="", path="/workspace/target")
        imported = self.record(primary_path="/workspace/other", related_event_ids=[old["event_id"]])
        path = Path(imported["event_path"])
        data = json.loads(path.read_text(encoding="utf-8"))
        data["schema_version"] = "1.0"
        path.write_text(json.dumps(data), encoding="utf-8")  # Simulate a legacy writer/import.
        result = self.query(scope_id="", path="/workspace/target", cursor=start["cursor"])
        self.assertEqual(result["mode"], "delta")
        self.assertEqual(result["pending_matched"], 0)
        self.assertEqual(result["items"][0]["related_event_ids"], [old["event_id"]])

    def test_delta_distinguishes_recorded_actions_from_current_open_actions(self):
        start = self.query()
        opened = self.record(follow_ups=["verify"])
        self.record(resolves_event_ids=[opened["event_id"]])
        result = self.query(cursor=start["cursor"])
        self.assertEqual(result["pending_matched"], 0)
        item = result["items"][0]
        self.assertEqual(item["recorded_follow_ups_total"], 1)
        self.assertNotIn("follow_up", item)

    def test_history_rewrite_deletion_and_late_import_reset_the_baseline(self):
        original = self.record()
        path = Path(original["event_path"])
        start = self.query()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["summary"] = "external rewrite"
        path.write_text(json.dumps(data), encoding="utf-8")
        rewritten = self.query(cursor=start["cursor"])
        self.assertEqual(rewritten["reset_reason"], "history_changed")
        path.unlink()  # Synthetic fixture only; production facts stay immutable.
        deleted = self.query(cursor=rewritten["cursor"])
        self.assertEqual(deleted["reset_reason"], "history_changed")
        self.record()
        start = self.query()
        imported = self.record(occurred_at="2020-01-01T00:00:00Z")
        imported_path = Path(imported["event_path"])
        data = json.loads(imported_path.read_text(encoding="utf-8"))
        data["recorded_at"] = "2020-01-01T00:00:00Z"
        imported_path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(self.query(cursor=start["cursor"])["reset_reason"], "history_changed")

    def test_config_changes_reset_and_cannot_report_false_unchanged(self):
        event = self.record()
        start = self.query()
        (self.root / "work-items.json").write_text(json.dumps({"schema_version": "1.0", "items": [
            dict(scope_id="OTHER", host_id="host-a", profile_id="", work_id="reclassified", title="New grouping",
                 event_ids=[event["event_id"]])]}), encoding="utf-8")
        result = self.query(cursor=start["cursor"])
        self.assertEqual(result["reset_reason"], "classification_changed")
        self.assertEqual(result["matched"], 0)
        registry = self.root / "registry.json"
        registry.write_text(registry.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(self.query(cursor=result["cursor"])["reset_reason"], "classification_changed")
        registry.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.query(cursor=result["cursor"])

    def test_cursor_binds_root_and_filter_and_does_not_replace_a_fresh_context(self):
        self.record()
        start = self.query()
        self.assertEqual(self.query(scope_id="OTHER", cursor=start["cursor"])["reset_reason"], "query_changed")
        other = Path(self.temp.name) / "other"
        cj.init_root(other)
        self.assertEqual(cj.compact_query(other, scope_id="PROJECT", cursor=start["cursor"])["reset_reason"], "query_changed")
        self.assertEqual(self.query()["mode"], "snapshot")

    def test_corrupt_or_duplicate_evidence_cannot_advance_a_cursor(self):
        old = self.record(follow_ups=["verify"])
        start = self.query()
        closed = self.record(resolves_event_ids=[old["event_id"]])
        path = Path(closed["event_path"])
        original = path.read_bytes()
        path.write_text("{broken", encoding="utf-8")
        result = self.query(cursor=start["cursor"])
        self.assertTrue(result["partial"])
        self.assertIsNone(result["cursor"])
        self.assertIsNone(result["pending_matched"])
        self.assertEqual(cj.show_event(self.root, old["event_id"])["event"]["event_id"], old["event_id"])
        path.write_bytes(original)
        (path.parent / "duplicate.json").write_bytes(original)
        self.assertTrue(self.query(cursor=start["cursor"])["partial"])

    def test_invalid_cursors_and_missing_root_fail_without_creating_journal(self):
        values = ["not a cursor", "a" * 1025, base64.b64encode(b"[]").decode(), base64.b64encode(b"{}").decode()]
        for value in values:
            with self.subTest(value=value), self.assertRaises(cj.JournalError):
                self.query(cursor=value)
        absent = Path(self.temp.name) / "absent"
        with self.assertRaises(cj.JournalError):
            cj.compact_query(absent, scope_id="PROJECT")
        self.assertFalse(absent.exists())

    def test_real_cli_and_hermes_share_facts_without_key_collision_or_silent_conflict(self):
        args = ["record", "--actor", "codex", "--source", "semantic", "--host-id", "host-a",
                "--profile-id", "personal", "--scope-id", "PROJECT", "--phase", "applied",
                "--summary", "first change", "--work-id", "shared", "--idempotency-key", "hermes:personal:step"]
        first, _ = self.cli(*args)
        self.assertTrue(self.cli(*args)[0]["duplicate"])
        baseline, _ = self.cli("query", "--work-id", "shared")
        payload = dict(profile_id="personal", external_event_id="step", host_id="host-a", scope_id="PROJECT",
                       phase="applied", summary="second change", work_id="shared")
        second, _ = self.cli(script="hermes_adapter.py", payload=payload)
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertTrue(self.cli(script="hermes_adapter.py", payload=payload)[0]["duplicate"])
        conflict, _ = self.cli(script="hermes_adapter.py", payload=dict(payload, summary="third change"), ok=False)
        self.assertIn("idempotency conflict", conflict["error"])
        delta, _ = self.cli("query", "--work-id", "shared", "--cursor", baseline["cursor"])
        self.assertEqual(delta["items"][0]["actor"], "hermes")
        self.assertEqual(delta["items"][0]["event_id"], second["event_id"])
        self.assertEqual(len(cj.load_events(self.root)), 2)
        self.assertTrue(self.cli("doctor")[0]["ok"])


if __name__ == "__main__":
    unittest.main()
