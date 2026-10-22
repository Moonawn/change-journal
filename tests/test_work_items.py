from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import change_journal as cj
import hermes_adapter as hermes
import codex_hook as hook


class WorkItemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "journal"
        cj.init_root(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, **kw):
        args = dict(actor="codex", source="semantic", phase="verified", summary="checked change",
                    primary_path="/tmp/project/task", changed_paths=["/tmp/project/task/a.md"],
                    scope_id="PROJECT", host_id="local-host", profile_id="personal", session_id="task-session",
                    verification_status="passed", verification_refs=["isolated check"])
        args.update(kw)
        return cj.record_event(self.root, **args)

    def catalog(self, event_ids=(), **kw):
        item = dict(work_id="review-task", title="本轮审阅", scope_id="PROJECT", host_id="local-host",
                    profile_id="personal", event_ids=list(event_ids), bindings=[])
        item.update(kw)
        (self.root / "work-items.json").write_text(json.dumps({"schema_version": "1.0", "items": [item]}))
        return item

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root / "events").rglob("*.json")}

    def test_card_groups_without_hiding_old_unresolved_actions(self):
        old = self.record(work_id="release", work_title="本轮发布", follow_ups=["verify loading"], occurred_at="2026-10-16T01:00:00Z")
        self.record(work_id="release", summary="local checks passed", occurred_at="2026-10-16T02:00:00Z")
        before = self.hashes()
        result = cj.render_views(self.root)
        card = cj.query_events(self.root, work_id="release")["work_items"][0]
        self.assertEqual(card["event_count"], 2)
        self.assertEqual(card["latest_summary"], "local checks passed")
        self.assertEqual(card["follow_ups"], [{"event_id": old["event_id"], "action": "verify loading"}])
        self.assertEqual(result["review_items"], 1)
        self.assertEqual(before, self.hashes())
        self.assertEqual(len(list((self.root / "views/by-work").glob("*.md"))), 1)

    def test_inherit_only_explicit_lifecycle_not_ordinary_reference(self):
        old = self.record(work_id="release", work_title="发布", follow_ups=["verify"])
        unrelated = self.record(related_event_ids=[old["event_id"]])
        self.assertEqual(unrelated["work_id"], "")
        resolved = self.record(resolves_event_ids=[old["event_id"]])
        self.assertEqual(resolved["work_id"], "release")
        self.assertEqual(cj.query_events(self.root, work_id="release")["pending"], [])

    def test_multiple_tasks_and_different_objects_are_not_auto_merged(self):
        a = self.record(work_id="a", follow_ups=["action a"])
        b = self.record(work_id="b", follow_ups=["action b"])
        combined = self.record(resolves_event_ids=[a["event_id"], b["event_id"]])
        self.assertEqual(combined["work_id"], "")
        self.assertTrue(combined["warnings"])
        other = self.record(scope_id="OTHER", resolves_event_ids=[a["event_id"]])
        self.assertEqual(other["work_id"], "")

    def test_same_work_name_or_id_never_merges_identity_boundaries(self):
        self.record(work_id="shared-id", work_title="same title")
        self.record(work_id="shared-id", work_title="same title", profile_id="customer")
        self.record(work_id="shared-id", work_title="same title", host_id="remote-host")
        self.record(work_id="shared-id", work_title="same title", scope_id="OTHER")
        result = cj.query_events(self.root, work_id="shared-id")
        self.assertEqual(result["work_items_matched"], 4)
        self.assertEqual(len({c["view_name"] for c in result["work_items"]}), 4)
        self.assertEqual(cj.query_events(self.root, work_id="shared-id", host_id="remote-host")["matched"], 1)

    def test_historical_classification_is_reversible_and_preserves_bytes(self):
        old = self.record(scope_id="BROAD")
        before = self.hashes()
        self.catalog([old["event_id"]])
        result = cj.query_events(self.root, scope_id="PROJECT")
        self.assertEqual(result["events"][0]["recorded_scope_id"], "BROAD")
        cj.render_views(self.root)
        old_view = next((self.root / "views/by-work").glob("*.md"))
        self.catalog([old["event_id"]], scope_id="CORRECTED", work_id="correct-task")
        cj.render_views(self.root)
        self.assertIn("停用", old_view.read_text())
        self.assertIn("停用", (self.root / "views/by-scope/PROJECT.md").read_text())
        self.assertEqual(cj.query_events(self.root, scope_id="PROJECT")["matched"], 0)
        self.assertEqual(cj.query_events(self.root, scope_id="CORRECTED")["matched"], 1)
        self.assertEqual(before, self.hashes())

    def test_binding_requires_session_target_and_identity_and_honors_new_id(self):
        self.catalog(bindings=[{"session_id": "task-session", "path": "/tmp/project/task"}])
        self.assertEqual(self.record()["work_id"], "review-task")
        self.assertEqual(self.record(session_id="another-session")["work_id"], "")
        self.assertEqual(self.record(profile_id="customer")["work_id"], "")
        self.assertEqual(self.record(primary_path="/tmp/project/other")["work_id"], "")
        self.assertEqual(self.record(work_id="separate-task")["work_id"], "separate-task")
        mixed = self.record(primary_path="", changed_paths=["/tmp/project/task/a", "/tmp/elsewhere/b"])
        self.assertEqual(mixed["work_id"], "")

    def test_invalid_identity_conflicts_and_unknown_seeds_are_reported(self):
        old = self.record()
        self.catalog([old["event_id"]], profile_id="customer")
        self.assertFalse(cj.doctor(self.root)["ok"])
        self.catalog(["missing-event"])
        self.assertFalse(cj.doctor(self.root)["ok"])
        item = self.catalog([old["event_id"]])
        (self.root / "work-items.json").write_text(json.dumps({"schema_version": "1.0", "items": [item, dict(item, work_id="other")]}))
        with self.assertRaises(cj.JournalError):
            cj.query_events(self.root, scope_id="PROJECT")

    def test_ambiguous_binding_preserves_fact_without_guessing_task(self):
        item = self.catalog(bindings=[{"session_id": "task-session", "path": "/tmp/project/task"}])
        (self.root / "work-items.json").write_text(json.dumps({"schema_version": "1.0", "items": [item, dict(item, work_id="other")]}))
        result = self.record()
        self.assertEqual(result["work_id"], "")
        self.assertTrue(result["warnings"])
        self.assertEqual(len(cj.load_events(self.root)), 1)
        self.assertFalse(cj.doctor(self.root)["ok"])

    def test_malformed_index_does_not_block_fact_capture(self):
        (self.root / "work-items.json").write_text("not valid json")
        result = self.record(work_id="explicit-task", work_title="Explicit task")
        self.assertEqual(result["work_id"], "explicit-task")
        self.assertTrue(result["warnings"])
        self.assertEqual(len(cj.load_events(self.root)), 1)
        self.assertFalse(cj.doctor(self.root)["ok"])

    def test_bounded_query_exposes_total_and_resolves_over_complete_history(self):
        old = self.record(work_id="task", follow_ups=["action 0"])
        for n in range(1, 7):
            self.record(work_id="task", follow_ups=[f"action {n}"])
        self.record(work_id="task", resolves_event_ids=[old["event_id"]])
        result = cj.query_events(self.root, work_id="task", limit=2)
        self.assertEqual(result["matched"], 8)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(result["pending_matched"], 6)
        self.assertEqual(result["work_items"][0]["follow_ups_total"], 6)
        self.assertEqual(len(result["work_items"][0]["follow_ups"]), 2)
        absent = Path(self.temp.name) / "not-created"
        self.assertEqual(cj.query_events(absent, work_id="none")["matched"], 0)
        self.assertFalse(absent.exists())

    def test_cli_and_adapter_accept_work_ids_and_remain_idempotent(self):
        argv = ["--root", str(self.root), "record", "--actor", "codex", "--source", "semantic", "--phase", "applied",
                "--summary", "applied", "--work-id", "cli-task", "--work-title", "CLI task", "--idempotency-key", "same-call", "--render"]
        for _ in range(2):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cj.main(argv), 0)
            self.assertEqual(json.loads(out.getvalue())["work_id"], "cli-task")
        self.assertEqual(len(cj.load_events(self.root)), 1)
        result = hermes.record_payload(dict(profile_id="personal", external_event_id="one", phase="applied",
            summary="adapter work", work_id="hermes-task", work_title="Hermes task"), self.root)
        self.assertEqual(result["work_id"], "hermes-task")
        spool = self.root / ".spool/new"
        spool.mkdir()
        (spool / "new.json").write_text(json.dumps({"schema_version": "1.2", "category": "file_mutation"}))
        self.assertEqual(len(hook._load_observations(spool)), 1)

    def test_title_only_starts_task_and_same_second_order_is_preserved(self):
        a = self.record(work_title="A task", occurred_at="2026-10-16T01:00:00Z")
        self.assertTrue(a["work_id"].startswith("work-"))
        self.record(work_id=a["work_id"], summary="latest actual progress", occurred_at="2026-10-16T01:00:00Z")
        result = cj.query_events(self.root, work_id=a["work_id"])
        self.assertEqual(result["work_items"][0]["latest_summary"], "latest actual progress")

    def test_object_routes_do_not_reclassify_unassigned_history(self):
        old = self.record(scope_id="BROAD")
        registry = json.loads((self.root / "registry.json").read_text())
        registry["routes"].append({"path": "/tmp/project", "scope_id": "PROJECT"})
        (self.root / "registry.json").write_text(json.dumps(registry))
        new = self.record(scope_id="")
        self.assertEqual(cj.query_events(self.root, scope_id="PROJECT")["events"][0]["event_id"], new["event_id"])
        self.assertEqual(cj.query_events(self.root, scope_id="BROAD")["events"][0]["event_id"], old["event_id"])


if __name__ == "__main__":
    unittest.main()
