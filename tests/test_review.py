from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import change_journal as cj
import codex_hook as hook
import hermes_adapter as hermes


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal"
        cj.init_root(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, **kw):
        args = dict(actor="codex", source="semantic", phase="verified", summary="checked change",
                    changed_paths=["/workspace/project/project/file.md"],
                    verification_status="passed", verification_refs=["diff checked"])
        args.update(kw)
        return cj.record_event(self.root, **args)

    def legacy(self, **kw):
        result = self.record(**kw)
        path = Path(result["event_path"])
        event = json.loads(path.read_text(encoding="utf-8"))
        event["schema_version"] = "1.0"
        for key in ("follow_ups", "limitations", "resolves_event_ids", "supersedes_event_ids", "lesson"):
            event.pop(key, None)
        path.write_text(json.dumps(event), encoding="utf-8")
        return result

    def test_only_explicit_actions_are_pending(self):
        self.record(phase="active", limitations=["no production benchmark"])
        self.record(phase="failed", verification_status="failed", limitations=["experiment ended"])
        self.record(unresolved=["无"])
        problem = self.record(unresolved=["无法读取附件"])
        action = self.record(follow_ups=["check the target after drain"])
        events = cj.load_events(self.root)
        self.assertEqual([e["event_id"] for e in cj.open_continuation_events(events)], [action["event_id"]])
        self.assertEqual([e["event_id"] for e in cj.legacy_continuation_events(events)], [problem["event_id"]])

    def test_reference_does_not_resolve_but_explicit_resolution_does(self):
        old = self.record(follow_ups=["pending action"])
        self.record(related_event_ids=[old["event_id"]])
        self.assertEqual(cj.status(self.root)["pending"], 1)
        self.record(resolves_event_ids=[old["event_id"]])
        self.assertEqual(cj.status(self.root)["pending"], 0)

    def test_legacy_relation_preserved_without_changing_bytes(self):
        old = self.legacy(unresolved=["old note"])
        self.legacy(related_event_ids=[old["event_id"]], unresolved=["remaining note"])
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root / "events").rglob("*.json")}
        cj.render_views(self.root)
        after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root / "events").rglob("*.json")}
        self.assertEqual(before, after)
        self.assertEqual(cj.status(self.root)["legacy_pending"], 1)
        self.assertTrue(cj.doctor(self.root)["ok"])

    def test_cannot_close_wrong_profile_or_missing_event(self):
        old = self.record(profile_id="profile-a", follow_ups=["profile-a action"])
        with self.assertRaises(cj.JournalError):
            self.record(profile_id="profile-b", resolves_event_ids=[old["event_id"]])
        with self.assertRaises(cj.JournalError):
            self.record(resolves_event_ids=["missing-event"])
        self.assertEqual(cj.status(self.root)["events"], 1)

    def test_target_beats_backup_and_unrelated_cwd(self):
        registry = cj.default_registry()
        registry["routes"].append({"path": "/workspace/project", "scope_id": "EXAMPLE-PROJECT"})
        registry["routes"].append({"path": str(self.root), "scope_id": "CHANGE-JOURNAL"})
        (self.root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
        backup = str(self.root / "备份_BACKUPS" / "file.bak")
        target = "/workspace/project/project/file.md"
        self.assertEqual(cj.route_scope(self.root, "/workspace/skills/change-journal",
                                       [target, backup]), "EXAMPLE-PROJECT")
        self.assertEqual(cj.route_scope(self.root, "/workspace/project", [target, backup],
                                       primary_path="/tmp/unregistered"), "UNASSIGNED")

    def test_lessons_are_revised_and_retired_per_identity(self):
        args = dict(lesson_key="repeatable-check", lesson_trigger="before the same operation",
                    lesson_check="check the actual target", profile_id="profile-a")
        self.record(**args, occurred_at="2026-10-15T01:00:00Z")
        self.record(**dict(args, lesson_check="revised check"), occurred_at="2026-10-15T02:00:00Z")
        self.record(**dict(args, profile_id="profile-b"), occurred_at="2026-10-15T03:00:00Z")
        self.assertEqual(len(cj.current_lessons(cj.load_events(self.root))), 2)
        self.record(**args, lesson_status="retired", occurred_at="2026-10-15T04:00:00Z")
        lessons = cj.current_lessons(cj.load_events(self.root))
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["profile_id"], "profile-b")
        self.assertEqual(len(cj.load_events(self.root)), 4)

    def test_unverified_hypothesis_cannot_become_lesson(self):
        with self.assertRaises(cj.JournalError):
            self.record(verification_status="partial", lesson_key="hypothesis",
                        lesson_trigger="some condition", lesson_check="unverified idea")

    def test_inconsistent_legacy_caller_is_warned_not_lost(self):
        result = self.record(verification_status="partial")
        self.assertTrue(result["warnings"])
        self.assertEqual(cj.load_events(self.root)[0]["verification"]["status"], "partial")

    def test_local_day_and_bounded_review(self):
        self.assertEqual(cj.local_day("2026-10-15T17:30:00Z"), "2026-10-16")
        for n in range(15):
            self.record(summary=f"change-{n:02}", occurred_at=f"2026-10-15T17:{n:02}:00Z")
        result = cj.render_views(self.root)
        text = Path(result["review_view"]).read_text(encoding="utf-8")
        self.assertIn("change-14", text)
        self.assertNotIn("change-00", text)
        self.assertTrue((self.root / "views" / "daily" / "2026-10-16.md").exists())
        query = cj.query_events(self.root, path="/workspace/project/project", limit=3)
        self.assertEqual(query["matched"], 15)
        self.assertEqual(len(query["events"]), 3)

    def test_query_resolves_against_all_events_before_target_filter(self):
        old = self.record(follow_ups=["pending action"])
        self.record(resolves_event_ids=[old["event_id"]], changed_paths=["/workspace/governance/other.md"])
        self.assertEqual(cj.query_events(self.root, path="/workspace/project/project")["pending"], [])
        absent = Path(self.temp.name) / "absent"
        cj.query_events(absent, path="/tmp")
        self.assertFalse(absent.exists())

    def test_pending_query_keeps_profile_identity(self):
        self.record(profile_id="profile-a", follow_ups=["own action"])
        self.record(profile_id="profile-b", follow_ups=["another action"])
        result = cj.query_events(self.root, path="/workspace/project/project")
        self.assertEqual({e["profile_id"] for e in result["pending"]}, {"profile-a", "profile-b"})
        self.assertEqual(result["pending_matched"], 2)

    def test_record_and_render_failure_is_recoverable_without_repeating_change(self):
        argv = ["--root", str(self.root), "record", "--actor", "codex", "--source", "semantic",
                "--phase", "applied", "--summary", "saved event", "--idempotency-key", "render-failure", "--render"]
        output = io.StringIO()
        with mock.patch.object(cj, "render_views", side_effect=OSError("disk error")), contextlib.redirect_stdout(output):
            self.assertEqual(cj.main(argv), 0)
        self.assertFalse(json.loads(output.getvalue())["views_updated"])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cj.main(argv), 0)
        self.assertTrue(json.loads(output.getvalue())["duplicate"])
        self.assertEqual(cj.status(self.root)["events"], 1)

    def test_unchanged_views_are_not_rewritten(self):
        self.record(occurred_at="2026-10-14T01:00:00Z")
        cj.render_views(self.root)
        old_day = self.root / "views" / "daily" / "2026-10-14.md"
        before = old_day.stat().st_mtime_ns
        self.record(occurred_at="2026-10-15T01:00:00Z")
        cj.render_views(self.root)
        self.assertEqual(old_day.stat().st_mtime_ns, before)

    def test_old_hook_spool_and_new_adapter_fields(self):
        spool = self.root / ".spool" / "fixture"
        spool.mkdir(parents=True)
        (spool / "old.json").write_text(json.dumps({"schema_version": "1.0", "category": "file_mutation"}), encoding="utf-8")
        self.assertEqual(len(hook._load_observations(spool)), 1)
        result = hermes.record_payload(dict(profile_id="profile-a", external_event_id="once", phase="applied",
            summary="Hermes change", record_level="full", kind="runtime",
            primary_path="/workspace/project/project", follow_ups=["check load"], limitations=["no external post"]), self.root)
        event = json.loads(Path(result["event_path"]).read_text(encoding="utf-8"))
        self.assertEqual(event["follow_ups"], ["check load"])
        self.assertEqual(event["record_level"], "full")


if __name__ == "__main__":
    unittest.main()
